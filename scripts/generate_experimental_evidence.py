"""Generate paired, seeded simulation evidence and presentation PNG graphs.

All measured values in these outputs come from the project's synthetic queue
model. They are not measurements from a physical router or ISP connection.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from qos.capacity import CapacityEstimator  # noqa: E402
from qos.engine import CLASSES, SCENARIOS, Simulator  # noqa: E402
from qos.traffic import SCENARIO_CONFIG, generate_sessions  # noqa: E402

POLICIES = ["no_qos", "static_qos", "adaptive"]
LABELS = {"no_qos": "No QoS", "static_qos": "Static QoS", "adaptive": "Adaptive QoS"}
COLORS = {"no_qos": "#718096", "static_qos": "#f0a35e", "adaptive": "#35b8a5"}
SEEDS = [17, 42, 73, 101, 202]
TICKS = 40


def mean_sd(values: list[float]) -> tuple[float, float]:
    return (statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0)


def run_trial(scenario: str, policy: str, seed: int) -> dict:
    engine = Simulator()
    started = time.process_time_ns()
    engine.start(scenario, adaptive=policy == "adaptive", seed=seed, policy_mode=policy)
    rows = [dict(engine.last_metrics)]
    for _ in range(TICKS - 1):
        engine.step()
        rows.append(dict(engine.last_metrics))
    cpu_ms = (time.process_time_ns() - started) / 1_000_000 / TICKS
    arrivals = sum(row["arrived_packets"] for row in rows)
    drops = sum(row["dropped_packets"] for row in rows)
    present_classes = [name for name in CLASSES if any(name in row["throughput_by_class"] for row in rows)]
    class_means = {name: statistics.fmean(row["throughput_by_class"].get(name, 0.0) for row in rows)
                   for name in present_classes}
    class_minima = {name: min(row["throughput_by_class"].get(name, 0.0) for row in rows)
                    for name in present_classes}
    changes = SCENARIO_CONFIG[scenario].get("capacity_change")
    adapt_seconds = None
    detect_tick = None
    if changes and policy == "adaptive":
        detected = next((row for row in rows if row["tick"] >= changes["tick"] and
                         abs(row["estimated_capacity"] - changes["capacity_mbps"]) / changes["capacity_mbps"] <= .1), None)
        if detected:
            detect_tick = detected["tick"]
            adapt_seconds = max(0, detect_tick - changes["tick"])
    churn = []
    allocations = [row["throughput_by_class"] for row in rows]
    for before, after in zip(allocations, allocations[1:]):
        keys = set(before) | set(after)
        denom = max(1.0, sum(before.values()), sum(after.values()))
        churn.append(100 * sum(abs(after.get(k, 0) - before.get(k, 0)) for k in keys) / denom)
    summary = {
        "latency_ms": statistics.fmean(row["latency"] for row in rows),
        "p95_latency_ms": sorted(row["latency"] for row in rows)[math.ceil(.95 * len(rows)) - 1],
        "jitter_ms": statistics.fmean(row["jitter"] for row in rows),
        "packet_loss_pct": 100 * drops / arrivals if arrivals else 0.0,
        "throughput_mbps": statistics.fmean(row["throughput"] for row in rows),
        "queue_depth_packets": statistics.fmean(row["queue_depth"] for row in rows),
        "queue_depth_max_packets": max(row["queue_depth"] for row in rows),
        "jain_fairness": statistics.fmean(row["fairness"] for row in rows),
        "adaptation_time_s": adapt_seconds,
        "capacity_detection_tick": detect_tick,
        "allocation_churn_pct": statistics.fmean(churn) if churn else 0.0,
        "engine_cpu_ms_per_tick": cpu_ms,
        "mean_throughput_by_class_mbps": class_means,
        "minimum_throughput_by_class_mbps": class_minima,
    }
    return {"scenario": scenario, "policy": policy, "seed": seed, "summary": summary,
            "timeseries": rows, "capacity_samples": engine.capacity_samples,
            "classification": [{"truth": s["truth_class"], "predicted": s["automatic_class"],
                                "confidence": s["confidence"], "rule": s["classification_rule"]}
                               for s in engine.sessions]}


def mean_series(trials: list[dict], field: str, policy: str, scenario: str) -> list[tuple[int, float, float]]:
    selected = [t for t in trials if t["scenario"] == scenario and t["policy"] == policy]
    grouped: dict[int, list[float]] = defaultdict(list)
    for trial in selected:
        for row in trial["timeseries"]:
            grouped[row["tick"]].append(float(row[field]))
    return [(tick, *mean_sd(vals)) for tick, vals in sorted(grouped.items())]


def write_outputs(out: Path, trials: list[dict]) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    raw = {
        "title": "Adaptive QoS model experiments",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_type": "simulated model output; not physical-router or ISP measurement",
        "methodology": {
            "scenarios": list(SCENARIOS), "policies": LABELS,
            "seeds": SEEDS, "ticks_per_run": TICKS, "tick_seconds": 1,
            "paired_conditions": "Each policy within a scenario uses the same fixed seed, generated flow workload, configured capacity, and number of ticks.",
            "policy_definitions": {
                "no_qos": "One shared FIFO queue, with offered-load-proportional service and no class priority.",
                "static_qos": "Separate class queues with the project's fixed DEFAULT_POLICIES minima, caps, weights, and priorities; no intent or congestion-triggered reweighting.",
                "adaptive": "Separate class queues; congestion-sensitive interactive weight boost based on offered load versus estimated capacity, plus the existing temporary-intent policy path.",
            },
            "latency_p95": "95th percentile of per-tick modeled interactive latency within each run.",
            "jitter": "Mean tick-to-tick modeled queue-delay change; not packet-level jitter.",
            "packet_loss": "Dropped packets divided by arrived packets across the run.",
            "fairness": "Mean per-tick Jain fairness over active generated flow rates.",
            "cpu": "Local Python process CPU time per simulated tick; not router CPU overhead.",
            "adaptation": "Adaptive capacity estimate first reaches within 10% of the configured 20 Mbps after the tick-8 capacity step.",
        },
        "trials": trials,
    }
    (out / "raw_trials.json").write_text(json.dumps(raw, indent=2, allow_nan=False), encoding="utf-8")
    write_csvs(out, trials)
    summaries = summarize(trials)
    (out / "summary.json").write_text(json.dumps(summaries, indent=2, allow_nan=False), encoding="utf-8")
    make_graphs(out, trials, summaries)
    extra = run_feature_checks(out)
    (out / "packet_lab_status.json").write_text(json.dumps({
        "status": "NOT RUN", "host_platform": platform.platform(),
        "reason": "The supplied packet lab requires Linux network namespaces and CAP_NET_ADMIN; this run is on Windows.",
        "script": "scripts/packet_lab.sh", "adaptive_controller_integrated": False,
    }, indent=2), encoding="utf-8")
    (out / "README.md").write_text(make_readme(summaries, extra), encoding="utf-8")
    return {"summaries": summaries, "features": extra}


def summarize(trials: list[dict]) -> list[dict]:
    keys = ["latency_ms", "p95_latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps",
            "queue_depth_packets", "queue_depth_max_packets", "jain_fairness", "allocation_churn_pct",
            "engine_cpu_ms_per_tick"]
    result = []
    for scenario in SCENARIOS:
        for policy in POLICIES:
            group = [t for t in trials if t["scenario"] == scenario and t["policy"] == policy]
            row = {"scenario": scenario, "policy": policy, "runs": len(group)}
            for key in keys:
                m, sd = mean_sd([float(t["summary"][key]) for t in group])
                row[key] = m
                row[key.replace("_ms", "_ms").replace("_pct", "_pct") + "_sd"] = sd
            adapt = [t["summary"]["adaptation_time_s"] for t in group if t["summary"]["adaptation_time_s"] is not None]
            row["adaptation_time_s"] = statistics.fmean(adapt) if adapt else None
            classes = sorted({c for t in group for c in t["summary"]["mean_throughput_by_class_mbps"]})
            row["mean_throughput_by_class_mbps"] = {c: statistics.fmean(t["summary"]["mean_throughput_by_class_mbps"].get(c, 0) for t in group) for c in classes}
            row["minimum_throughput_by_class_mbps"] = {c: min(t["summary"]["minimum_throughput_by_class_mbps"].get(c, 0) for t in group) for c in classes}
            result.append(row)
    return result


def write_csvs(out: Path, trials: list[dict]) -> None:
    summary_fields = ["scenario", "policy", "runs", "latency_ms", "latency_ms_sd", "p95_latency_ms", "p95_latency_ms_sd",
                      "jitter_ms", "jitter_ms_sd", "packet_loss_pct", "packet_loss_pct_sd", "throughput_mbps", "throughput_mbps_sd",
                      "queue_depth_packets", "queue_depth_packets_sd", "queue_depth_max_packets", "queue_depth_max_packets_sd",
                      "jain_fairness", "jain_fairness_sd", "adaptation_time_s", "allocation_churn_pct", "allocation_churn_pct_sd",
                      "engine_cpu_ms_per_tick", "engine_cpu_ms_per_tick_sd", "mean_throughput_by_class_mbps", "minimum_throughput_by_class_mbps"]
    summary_rows = summarize(trials)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=summary_fields); w.writeheader()
        for row in summary_rows:
            w.writerow({k: json.dumps(row[k], sort_keys=True) if isinstance(row.get(k), dict) else row.get(k) for k in summary_fields})
    fields = ["scenario", "policy", "seed", "tick", "latency_ms", "p95_latency_ms", "jitter_ms", "packet_loss_pct",
              "throughput_mbps", "queue_depth_packets", "jain_fairness", "capacity_mbps", "estimated_capacity_mbps",
              "capacity_error_pct", "throughput_by_class_mbps_json"]
    with (out / "raw_timeseries.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for trial in trials:
            for m in trial["timeseries"]:
                w.writerow({"scenario": trial["scenario"], "policy": trial["policy"], "seed": trial["seed"],
                            "tick": m["tick"], "latency_ms": m["latency"], "p95_latency_ms": m["p95_latency"],
                            "jitter_ms": m["jitter"], "packet_loss_pct": m["packet_loss"],
                            "throughput_mbps": m["throughput"], "queue_depth_packets": m["queue_depth"],
                            "jain_fairness": m["fairness"], "capacity_mbps": m["capacity"],
                            "estimated_capacity_mbps": m["estimated_capacity"], "capacity_error_pct": m["capacity_error_pct"],
                            "throughput_by_class_mbps_json": json.dumps(m["throughput_by_class"], sort_keys=True)})


def plot_metric(out: Path, trials: list[dict], metric: str, title: str, ylabel: str, filename: str) -> None:
    fig, ax = plt.subplots(figsize=(9.5, 5.4), layout="constrained")
    x = np.arange(len(SCENARIOS)); width = .23
    for i, policy in enumerate(POLICIES):
        means, sds = [], []
        for scenario in SCENARIOS:
            vals = [float(t["summary"][metric]) for t in trials if t["scenario"] == scenario and t["policy"] == policy]
            m, sd = mean_sd(vals); means.append(m); sds.append(sd)
        ax.bar(x + (i - 1) * width, means, width, yerr=sds, capsize=3, label=LABELS[policy], color=COLORS[policy], edgecolor="white", linewidth=.4)
    ax.set_xticks(x, [SCENARIOS[s].replace(" ", "\n", 1) for s in SCENARIOS])
    ax.set_ylabel(ylabel); ax.set_title(title, loc="left", weight="bold")
    ax.legend(frameon=False, ncols=3, loc="upper right"); ax.grid(axis="y", alpha=.22); ax.set_axisbelow(True)
    fig.text(.01, -.01, "Model output · mean ± SD across five paired seeds · not physical-network measurements", fontsize=8, color="#555")
    fig.savefig(out / filename, dpi=240, bbox_inches="tight"); plt.close(fig)


def make_graphs(out: Path, trials: list[dict], summaries: list[dict]) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
                        "axes.titleweight": "bold", "figure.facecolor": "white", "axes.facecolor": "#fbfcfe"})
    plot_metric(out, trials, "latency_ms", "Average interactive latency", "Latency (ms)", "graph_01_average_latency.png")
    plot_metric(out, trials, "p95_latency_ms", "P95 interactive latency", "P95 latency (ms)", "graph_02_p95_latency.png")
    plot_metric(out, trials, "jitter_ms", "Modeled jitter", "Tick-to-tick delay change (ms)", "graph_03_jitter.png")
    plot_metric(out, trials, "packet_loss_pct", "Modeled packet loss", "Dropped packets (%)", "graph_04_packet_loss.png")

    fig, axes = plt.subplots(2, 2, figsize=(13, 8), layout="constrained")
    for ax, scenario in zip(axes.flat, SCENARIOS):
        classes = [c for c in CLASSES if any(c in t["summary"]["mean_throughput_by_class_mbps"] for t in trials if t["scenario"] == scenario)]
        x = np.arange(len(classes)); width = .24
        for i, policy in enumerate(POLICIES):
            selected = [t for t in trials if t["scenario"] == scenario and t["policy"] == policy]
            vals_by_class = [[t["summary"]["mean_throughput_by_class_mbps"].get(c, 0.0) for t in selected] for c in classes]
            means = [statistics.fmean(v) for v in vals_by_class]
            sds = [statistics.stdev(v) if len(v)>1 else 0 for v in vals_by_class]
            ax.bar(x+(i-1)*width, means, width, yerr=sds, capsize=2, color=COLORS[policy], label=LABELS[policy])
        ax.set_title(SCENARIOS[scenario]); ax.set_xticks(x, classes, rotation=25, ha="right", fontsize=8); ax.set_ylabel("Mbps"); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    axes[0,0].legend(frameon=False, ncols=3, fontsize=8)
    fig.suptitle("Mean throughput by generated traffic class", fontsize=14, weight="bold")
    fig.text(.01, -.01, "Rates are modeled service allocation, averaged across five paired seeds; absent classes are omitted.", fontsize=8)
    fig.savefig(out / "graph_05_class_throughput.png", dpi=240, bbox_inches="tight"); plt.close(fig)

    plot_capacity_response(out, trials)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
    for ax, scenario in zip(axes.flat, SCENARIOS):
        for policy in POLICIES:
            rows = mean_series(trials, "queue_depth", policy, scenario)
            ax.plot([r[0] for r in rows], [r[1] for r in rows], color=COLORS[policy], lw=2, label=LABELS[policy])
        config = SCENARIO_CONFIG[scenario].get("capacity_change")
        if config: ax.axvline(config["tick"], color="#b34c4c", ls="--", lw=1, label="Capacity step")
        ax.set_title(SCENARIOS[scenario]); ax.set_xlabel("Tick (1 simulated second)"); ax.set_ylabel("Queue depth (packets)"); ax.grid(alpha=.2)
    axes[0,0].legend(frameon=False, fontsize=8)
    fig.suptitle("Queue depth and recovery", fontsize=14, weight="bold")
    fig.savefig(out / "graph_07_queue_depth.png", dpi=240, bbox_inches="tight"); plt.close(fig)

    plot_fairness_and_minimum(out, trials)
    plot_stability(out, trials)
    plot_metric(out, trials, "engine_cpu_ms_per_tick", "Model execution overhead", "Python process CPU time (ms / tick)", "graph_10_cpu_overhead.png")


def plot_capacity_response(out: Path, trials: list[dict]) -> None:
    scenario = "capacity_drop"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, layout="constrained")
    for policy in POLICIES:
        latency = mean_series(trials, "latency", policy, scenario)
        queue = mean_series(trials, "queue_depth", policy, scenario)
        ax1.plot([x[0] for x in latency], [x[1] for x in latency], color=COLORS[policy], lw=2, label=LABELS[policy])
        ax2.plot([x[0] for x in queue], [x[1] for x in queue], color=COLORS[policy], lw=2, label=LABELS[policy])
    change = SCENARIO_CONFIG[scenario]["capacity_change"]["tick"]
    for ax in (ax1, ax2): ax.axvline(change, color="#ba5c4d", ls="--", label="100 → 20 Mbps")
    detections = [t["summary"]["capacity_detection_tick"] for t in trials if t["policy"] == "adaptive" and t["summary"]["capacity_detection_tick"]]
    if detections:
        detect = statistics.fmean(detections); ax1.axvline(detect, color=COLORS["adaptive"], ls=":", label=f"Adaptive capacity estimate (~tick {detect:.1f})")
    ax1.set_ylabel("Interactive latency (ms)"); ax1.set_title("Capacity-drop response", loc="left", weight="bold"); ax1.legend(frameon=False, ncols=2, fontsize=8); ax1.grid(alpha=.2)
    ax2.set_ylabel("Queue depth (packets)"); ax2.set_xlabel("Tick (1 simulated second)"); ax2.legend(frameon=False, ncols=2, fontsize=8); ax2.grid(alpha=.2)
    fig.text(.01, -.01, "Means across five seeds. Queue model values; not packet capture.", fontsize=8)
    fig.savefig(out / "graph_06_capacity_drop_response.png", dpi=240, bbox_inches="tight"); plt.close(fig)


def plot_fairness_and_minimum(out: Path, trials: list[dict]) -> None:
    fig = plt.figure(figsize=(14, 10), layout="constrained")
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1])
    ax = fig.add_subplot(gs[0, :]); x = np.arange(len(SCENARIOS)); width=.23
    for i, policy in enumerate(POLICIES):
        means=[]; sds=[]
        for scenario in SCENARIOS:
            vals=[t["summary"]["jain_fairness"] for t in trials if t["scenario"]==scenario and t["policy"]==policy]
            m,sd=mean_sd(vals); means.append(m); sds.append(sd)
        ax.bar(x+(i-1)*width,means,width,yerr=sds,capsize=2,color=COLORS[policy],label=LABELS[policy])
    ax.set_xticks(x,[SCENARIOS[s].replace(" ","\n",1) for s in SCENARIOS]); ax.set_ylim(0,1.05); ax.set_ylabel("Jain fairness index"); ax.set_title("Fairness by scenario (mean ± SD)",loc="left"); ax.legend(frameon=False,ncols=3); ax.grid(axis="y",alpha=.2)
    classes_by_scenario={}
    max_value=0
    for scenario in SCENARIOS:
        present=[c for c in CLASSES if any(c in t["summary"]["minimum_throughput_by_class_mbps"] for t in trials if t["scenario"]==scenario)]
        classes_by_scenario[scenario]=present
        max_value=max(max_value,max((t["summary"]["minimum_throughput_by_class_mbps"].get(c,0) for t in trials if t["scenario"]==scenario for c in present),default=0))
    for idx,scenario in enumerate(SCENARIOS):
        hax=fig.add_subplot(gs[1+idx//2,idx%2]); classes=classes_by_scenario[scenario]
        matrix=[]
        for policy in POLICIES:
            selected=[t for t in trials if t["scenario"]==scenario and t["policy"]==policy]
            matrix.append([min(t["summary"]["minimum_throughput_by_class_mbps"].get(c,0) for t in selected) for c in classes])
        im=hax.imshow(matrix,aspect="auto",cmap="YlGnBu",vmin=0,vmax=max(max_value,.01))
        hax.set_title("Minimum per-class throughput · "+SCENARIOS[scenario],fontsize=9)
        hax.set_xticks(range(len(classes)),classes,rotation=25,ha="right",fontsize=7); hax.set_yticks(range(3),[LABELS[p] for p in POLICIES],fontsize=8)
        for r,row in enumerate(matrix):
            for c,val in enumerate(row): hax.text(c,r,f"{val:.1f}",ha="center",va="center",fontsize=7,color="black" if val>max_value*.55 else "white")
    fig.colorbar(im,ax=fig.axes,shrink=.7,label="Worst-tick class allocation (Mbps)")
    fig.suptitle("Fairness and minimum service",fontsize=14,weight="bold")
    fig.text(.01,-.01,"Minimum is the smallest per-tick modeled allocation across the five runs; 0 may indicate model starvation.",fontsize=8)
    fig.savefig(out / "graph_08_fairness_and_minimum_service.png",dpi=240,bbox_inches="tight"); plt.close(fig)


def plot_stability(out: Path, trials: list[dict]) -> None:
    scenario="capacity_drop"
    fig,axes=plt.subplots(4,1,figsize=(11,13),sharex=True,layout="constrained")
    for policy in POLICIES:
        chosen=[t for t in trials if t["scenario"]==scenario and t["policy"]==policy]
        by_tick:dict[int,dict[str,list[float]]]=defaultdict(lambda:defaultdict(list))
        churn:dict[int,list[float]]=defaultdict(list)
        for trial in chosen:
            prev={}
            for row in trial["timeseries"]:
                alloc=row["throughput_by_class"]
                for cls,value in alloc.items(): by_tick[row["tick"]][cls].append(float(value))
                if prev:
                    keys=set(prev)|set(alloc); denom=max(1,sum(prev.values()),sum(alloc.values()))
                    churn[row["tick"]].append(100*sum(abs(alloc.get(k,0)-prev.get(k,0)) for k in keys)/denom)
                prev=alloc
        classes=[c for c in CLASSES if any(c in values for values in by_tick.values())]
        ax=axes[POLICIES.index(policy)]
        for cls in classes:
            ticks=sorted(by_tick)
            vals=[statistics.fmean(by_tick[t][cls]) if by_tick[t][cls] else 0 for t in ticks]
            ax.plot(ticks,vals,lw=1.5,label=cls)
        ax.axvline(8,color="#a44",ls="--",lw=1); ax.set_ylabel(f"{LABELS[policy]}\nMbps"); ax.grid(alpha=.18); ax.legend(frameon=False,ncols=4,fontsize=7,loc="upper right")
    ticks=sorted({t for policy in POLICIES for trial in trials if trial["scenario"]==scenario and trial["policy"]==policy for t in range(2,TICKS+1)})
    for policy in POLICIES:
        selected=[t for t in trials if t["scenario"]==scenario and t["policy"]==policy]
        vals=[]
        for tick in ticks:
            per_run=[]
            for trial in selected:
                series=trial["timeseries"]
                if tick<len(series):
                    before=series[tick-2]["throughput_by_class"]; after=series[tick-1]["throughput_by_class"]
                    keys=set(before)|set(after); denom=max(1,sum(before.values()),sum(after.values()))
                    per_run.append(100*sum(abs(after.get(k,0)-before.get(k,0)) for k in keys)/denom)
            vals.append(statistics.fmean(per_run) if per_run else 0)
        axes[3].plot(ticks,vals,color=COLORS[policy],label=LABELS[policy],lw=1.8)
    axes[3].axvline(8,color="#a44",ls="--",lw=1); axes[3].set_ylabel("Allocation churn\n% / tick"); axes[3].set_xlabel("Tick (capacity falls 100 → 20 Mbps at tick 8)"); axes[3].grid(alpha=.18); axes[3].legend(frameon=False,ncols=3)
    fig.suptitle("Allocation response and stability after capacity drop",fontsize=14,weight="bold")
    fig.text(.01,-.01,"Class allocations and churn are outputs of the synthetic scheduler model, not enforced router rates.",fontsize=8)
    fig.savefig(out/"graph_09_adaptation_stability.png",dpi=240,bbox_inches="tight"); plt.close(fig)


def run_feature_checks(out: Path) -> dict:
    classification = []
    for seed in SEEDS:
        classification.extend({"seed":seed,**row} for row in run_trial("work_call_iso","adaptive",seed)["classification"])
    labels=CLASSES
    matrix={truth:{pred:0 for pred in labels} for truth in labels}
    for row in classification:
        matrix[row["truth"]][row["predicted"]]+=1
    per_class={}
    for label in labels:
        tp=matrix[label][label]; fp=sum(matrix[t][label] for t in labels if t!=label); fn=sum(matrix[label][p] for p in labels if p!=label)
        precision=tp/(tp+fp) if tp+fp else 0; recall=tp/(tp+fn) if tp+fn else 0
        per_class[label]={"precision":precision,"recall":recall,"f1":2*precision*recall/(precision+recall) if precision+recall else 0,"support":sum(matrix[label].values())}
    total=sum(sum(row.values()) for row in matrix.values()); correct=sum(matrix[label][label] for label in labels)
    classification_report={"evidence":"generated metadata labels only; not real household classification accuracy","accuracy":correct/total if total else 0,
                           "macro_precision":statistics.fmean(x["precision"] for x in per_class.values()),"macro_recall":statistics.fmean(x["recall"] for x in per_class.values()),
                           "macro_f1":statistics.fmean(x["f1"] for x in per_class.values()),"labels":labels,"confusion_matrix":matrix,"per_class":per_class,"n_flows":total}
    (out/"classifier_report.json").write_text(json.dumps(classification_report,indent=2),encoding="utf-8")
    plot_confusion(out,matrix,labels)
    capacities=[20.0,50.0,100.0,200.0]
    capacity_rows=[]
    for target in capacities:
        for seed in SEEDS:
            rng=np.random.default_rng(seed+int(target*10)); estimator=CapacityEstimator(initial=100.0)
            for tick in range(30):
                probe=target*(1+rng.uniform(-.08,.08)); estimate=estimator.observe(probe)
                capacity_rows.append({"target_mbps":target,"seed":seed,"tick":tick+1,"probe_mbps":probe,"estimate_mbps":estimate,
                                     "absolute_error_mbps":abs(estimate-target),"percentage_error":abs(estimate-target)/target*100})
    with (out/"capacity_estimation.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=list(capacity_rows[0])); w.writeheader(); w.writerows(capacity_rows)
    capacity_summary=[]
    for target in capacities:
        final=[r["estimate_mbps"] for r in capacity_rows if r["target_mbps"]==target and r["tick"]==30]
        e=[abs(v-target) for v in final]
        capacity_summary.append({"configured_mbps":target,"estimated_mean_mbps":statistics.fmean(final),"estimated_sd_mbps":statistics.stdev(final),
                                 "absolute_error_mean_mbps":statistics.fmean(e),"percentage_error_mean":statistics.fmean(e)/target*100})
    (out/"capacity_estimation_summary.json").write_text(json.dumps({"source":"seeded noisy simulated probes (±8%); not a physical capacity test","runs_per_capacity":len(SEEDS),"summary":capacity_summary},indent=2),encoding="utf-8")
    plot_capacity_estimation(out,capacity_rows,capacities)
    intent=run_intent_check()
    (out/"temporary_intent.json").write_text(json.dumps(intent,indent=2),encoding="utf-8")
    plot_intent(out,intent)
    return {"classifier":classification_report,"capacity":capacity_summary,"intent":intent}


def plot_confusion(out: Path, matrix: dict, labels: list[str]) -> None:
    arr=np.array([[matrix[t][p] for p in labels] for t in labels])
    fig,ax=plt.subplots(figsize=(8,7),layout="constrained"); im=ax.imshow(arr,cmap="Blues")
    ax.set_xticks(range(len(labels)),labels,rotation=40,ha="right"); ax.set_yticks(range(len(labels)),labels)
    ax.set_xlabel("Predicted class"); ax.set_ylabel("Generated truth class"); ax.set_title("Generated-label classification confusion matrix",loc="left")
    for r in range(len(labels)):
        for c in range(len(labels)): ax.text(c,r,str(arr[r,c]),ha="center",va="center",color="white" if arr[r,c]>arr.max()*.55 else "#233")
    fig.colorbar(im,ax=ax,label="Flow count"); fig.text(.01,-.01,"Generated test metadata only; not a real-network classifier validation.",fontsize=8)
    fig.savefig(out/"graph_11_classifier_confusion_matrix.png",dpi=240,bbox_inches="tight"); plt.close(fig)


def plot_capacity_estimation(out: Path, rows: list[dict], capacities: list[float]) -> None:
    fig,ax=plt.subplots(figsize=(8,5),layout="constrained")
    means=[]; sds=[]
    for target in capacities:
        vals=[r["estimate_mbps"] for r in rows if r["target_mbps"]==target and r["tick"]==30]
        m,sd=mean_sd(vals); means.append(m); sds.append(sd)
    ax.errorbar(capacities,means,yerr=sds,fmt="o-",capsize=4,color="#278f83",label="Estimator final value (mean ± SD)")
    ax.plot(capacities,capacities,ls="--",color="#7c8795",label="Ideal estimate"); ax.set_xlabel("Configured capacity (Mbps)"); ax.set_ylabel("Estimated capacity (Mbps)")
    ax.set_title("Capacity estimation at known simulated rates",loc="left"); ax.grid(alpha=.2); ax.legend(frameon=False)
    fig.text(.01,-.01,"30 seeded probes per run with ±8% noise; estimator starts at 100 Mbps.",fontsize=8)
    fig.savefig(out/"graph_12_capacity_estimation.png",dpi=240,bbox_inches="tight"); plt.close(fig)


def run_intent_check() -> dict:
    clock=[1_000_000.0]
    with patch("qos.engine.time.time", side_effect=lambda: clock[0]):
        engine=Simulator(); engine.start("work_call_iso",adaptive=True,seed=42,policy_mode="adaptive")
        before=[dict(engine.last_metrics)]
        for _ in range(9): clock[0]+=1; engine.step(); before.append(dict(engine.last_metrics))
        engine.activate_intent("Prioritize Work Call",1)
        expires_at=engine.intents[-1]["expires_at"]
        during=[dict(engine.last_metrics)]
        while clock[0] < expires_at:
            clock[0]+=1; engine.step()
            if clock[0] < expires_at: during.append(dict(engine.last_metrics))
        after=[dict(engine.last_metrics)]
        for _ in range(9): clock[0]+=1; engine.step(); after.append(dict(engine.last_metrics))
        event_seen=any("Intent expired" in entry["event"] for entry in engine.timeline)
        return {"evidence":"simulated model with a deterministic test clock; 1-second ticks and 60-second configured intent lifetime",
                "seed":42,"intent":"Prioritize Work Call","duration_minutes":1,"expiration_event_seen":event_seen,
                "phase_summary":{name:{"latency_ms_mean":statistics.fmean(row["latency"] for row in phase),
                                      "video_call_mbps_mean":statistics.fmean(row["throughput_by_class"].get("Video call",0) for row in phase),"ticks":len(phase)}
                                 for name,phase in [("before",before),("during",during),("after",after)]},
                "timeseries":[{"phase":phase,"tick":row["tick"],"latency_ms":row["latency"],"video_call_mbps":row["throughput_by_class"].get("Video call",0)}
                              for phase,rows in [("before",before),("during",during),("after",after)] for row in rows]}


def plot_intent(out: Path, report: dict) -> None:
    rows=report["timeseries"]; fig,(a1,a2)=plt.subplots(2,1,figsize=(9,6),sharex=True,layout="constrained")
    for phase,color in [("before","#718096"),("during","#35b8a5"),("after","#f0a35e")]:
        selected=[r for r in rows if r["phase"]==phase]
        a1.plot([r["tick"] for r in selected],[r["latency_ms"] for r in selected],label=phase.title(),color=color,lw=2)
        a2.plot([r["tick"] for r in selected],[r["video_call_mbps"] for r in selected],label=phase.title(),color=color,lw=2)
    a1.set_ylabel("Interactive latency (ms)"); a1.legend(frameon=False,ncols=3); a1.grid(alpha=.2); a1.set_title("Temporary call-priority intent",loc="left")
    a2.set_ylabel("Video-call allocation (Mbps)"); a2.set_xlabel("Simulated second"); a2.grid(alpha=.2)
    fig.text(.01,-.01,"Seed 42; deterministic clock advances through a one-minute intent and its expiration.",fontsize=8)
    fig.savefig(out/"graph_13_temporary_intent.png",dpi=240,bbox_inches="tight"); plt.close(fig)


def make_readme(summaries: list[dict], extra: dict) -> str:
    out=["# Adaptive QoS experimental evidence", "",
         "All trial measurements here are generated by the project's synthetic queue model. They are not physical-router, Wi-Fi, ISP, or packet-capture results.", "",
         "## Method", "",
         f"Four configured scenarios × three scheduler modes × five paired fixed seeds ({', '.join(map(str,SEEDS))}) × {TICKS} one-second ticks. The same seed and generated flow definitions are used for every policy within a scenario.", "",
         "- **No QoS:** shared FIFO queue; offered-load-proportional service.",
         "- **Static QoS:** separate class queues; fixed configured priorities/minimums/limits/weights.",
         "- **Adaptive QoS:** same class queues plus a congestion-sensitive boost to interactive class weights and temporary intent support.",
         "- P95 is computed over per-tick modeled interactive latency. Jitter is tick-to-tick modeled queue-delay change, not packet jitter.",
         "- CPU is local Python process CPU per tick, not gateway CPU.",
         "- See raw_trials.json and raw_timeseries.csv for inputs and run-by-run values; summary.csv has mean and SD.", "",
         "## Generated graphs", ""]
    for i,title in enumerate(["Average interactive latency","P95 latency","Jitter","Packet loss","Throughput by class","Capacity drop response","Queue depth","Fairness and minimum service","Allocation response and stability","Python model CPU time","Generated-label confusion matrix","Capacity estimate error","Temporary user intent"],1):
        out.append(f"{i}. `graph_{i:02d}_*.png` — {title}.")
    out += ["", "## Additional validation", "",
            f"Generated-label classifier accuracy: {extra['classifier']['accuracy']:.3f}; macro precision {extra['classifier']['macro_precision']:.3f}, recall {extra['classifier']['macro_recall']:.3f}, F1 {extra['classifier']['macro_f1']:.3f}. This checks the metadata classifier against generated labels only.",
            "Capacity estimation uses seeded noisy synthetic probes at 20, 50, 100, and 200 Mbps. It is not a real bandwidth test.",
            f"Temporary intent expiration event observed by deterministic clock: {extra['intent']['expiration_event_seen']}; before/during/after spans {extra['intent']['phase_summary']['before']['ticks']}/{extra['intent']['phase_summary']['during']['ticks']}/{extra['intent']['phase_summary']['after']['ticks']} one-second ticks.",
            "The separate packet-lab status file records whether isolated Linux kernel packet tests ran. These tests do not control a physical router or use the adaptive controller.",
            "", "No result is manually adjusted. If an approach is worse on a metric, the graph and CSV preserve that result.", ""]
    return "\n".join(out)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=ROOT/"reports")
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out=args.out/f"adaptive-qos-evidence-{stamp}"
    trials=[]
    total=len(SCENARIOS)*len(POLICIES)*len(SEEDS)
    done=0
    for scenario in SCENARIOS:
        for seed in SEEDS:
            for policy in POLICIES:
                trials.append(run_trial(scenario,policy,seed)); done+=1
                print(f"[{done}/{total}] {scenario} / {policy} / seed {seed}")
    report=write_outputs(out,trials)
    print(f"Evidence directory: {out.resolve()}")
    print(f"Runs: {len(trials)} · Graphs: 13 PNG · Classifier accuracy: {report['features']['classifier']['accuracy']:.3f}")


if __name__=="__main__":
    main()
