"""Reproducible paired policy trials and evidence aggregation."""
from __future__ import annotations
import math
import time
from statistics import fmean
from typing import Any
from .engine import Simulator
from .traffic import SCENARIO_CONFIG, generate_devices, generate_sessions


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered=sorted(values)
    index=max(0,min(len(ordered)-1,math.ceil(p*len(ordered))-1))
    return ordered[index]


def run_policy_trial(scenario: str, adaptive: bool, seed: int, ticks: int) -> dict[str, Any]:
    engine=Simulator()
    start_cpu=time.process_time_ns()
    first=engine.start(scenario,adaptive,seed)
    rows=[{key:value for key,value in first["metrics"].items() if key!="time"}]
    for _ in range(ticks-1):
        engine.step()
        rows.append({key:value for key,value in engine.last_metrics.items() if key!="time"})
    cpu_ms=(time.process_time_ns()-start_cpu)/1_000_000/max(1,ticks)
    latency=[float(row["latency"]) for row in rows]
    arrived=sum(float(row["arrived_packets"]) for row in rows)
    dropped=sum(float(row["dropped_packets"]) for row in rows)
    class_load=[row["throughput_by_class"] for row in rows]
    churn=[]
    for before,after in zip(class_load,class_load[1:]):
        keys=set(before)|set(after)
        denominator=max(1.0,sum(float(v) for v in before.values()),sum(float(v) for v in after.values()))
        churn.append(sum(abs(float(after.get(k,0))-float(before.get(k,0))) for k in keys)/denominator)
    config=SCENARIO_CONFIG[scenario]
    change=config.get("capacity_change")
    adaptation_s=None
    if change:
        tick_seconds=float(config["impairments"]["tick_seconds"])
        settled=next((row for row in rows if row["tick"]>=change["tick"] and abs(row["estimated_capacity"]-change["capacity_mbps"])/change["capacity_mbps"]<=0.1),None)
        adaptation_s=None if settled is None else max(0,settled["tick"]-change["tick"])*tick_seconds
    summary={
        "latency":round(fmean(latency),4),"p95_latency":round(percentile(latency,.95),4),
        "jitter":round(fmean([float(row["jitter"]) for row in rows]),4),
        "packet_loss":round(100*dropped/arrived if arrived else 0,5),
        "throughput":round(fmean([float(row["throughput"]) for row in rows]),4),
        "fairness":round(fmean([float(row["fairness"]) for row in rows]),5),
        "capacity_estimation_mape_pct":round(fmean([float(row["capacity_error_pct"]) for row in rows]),4),
        "classifier_accuracy":round(fmean([float(row["classifier_accuracy"]) for row in rows]),5),
        "adaptation_time_s":None if adaptation_s is None else round(adaptation_s,3),
        "allocation_churn_pct":round(100*fmean(churn),4) if churn else 0.0,
        "engine_cpu_ms_per_tick":round(cpu_ms,6),
        "engine_cpu_pct_of_tick":round(100*cpu_ms/(1000*float(config["impairments"]["tick_seconds"])),4),
    }
    return {"summary":summary,"timeseries":rows,"capacity_samples":engine.capacity_samples,
            "classification":[{"session_id":s["id"],"device_id":s["device_id"],"device_name":s["device_name"],"truth_class":s["truth_class"],"predicted_class":s["automatic_class"],
                "confidence":s["confidence"],"rule":s["classification_rule"],"metadata":s["metadata"]} for s in engine.sessions]}


def run_comparison(scenario: str, seed: int, ticks: int) -> dict[str, Any]:
    if scenario not in SCENARIO_CONFIG:
        raise ValueError("Unknown scenario")
    baseline=run_policy_trial(scenario,False,seed,ticks)
    adaptive=run_policy_trial(scenario,True,seed,ticks)
    config=SCENARIO_CONFIG[scenario]
    flows=generate_sessions(scenario,seed)
    generated_data=[{"session_id":s["id"],"name":s["name"],"device_id":s["device_id"],"device_name":s["device_name"],
        "direction":s["direction"],"truth_class":s["truth_class"],"predicted_class":s["automatic_class"],
        "offered_mbps":s["offered"],"flow_metadata":s["metadata"]} for s in flows]
    b,a=baseline["summary"],adaptive["summary"]
    return {"scenario":scenario,"scenario_name":config["name"],"seed":seed,"ticks":ticks,
        "configuration":{"initial_capacity_mbps":config["link_mbps"],"capacity_change":config["capacity_change"],
            "impairments":config["impairments"],"generated_flows":generated_data,"demo_devices":generate_devices(scenario),
            "model":"Both arms use 1-second fluid arrivals, packet-size-based queue counts, finite byte buffers with tail drops, and propagation + queueing delay. Baseline is a shared FIFO queue with offered-load-proportional service; adaptive uses isolated per-class queues and the configured weighted policy. Jitter is tick-to-tick change in modeled queueing delay, not packet-level jitter."},
        "baseline":b,"adaptive":a,"timeseries":{"baseline":baseline["timeseries"],"adaptive":adaptive["timeseries"]},
        "capacity_samples":{"baseline":baseline["capacity_samples"],"adaptive":adaptive["capacity_samples"]},
        "classification_evidence":baseline["classification"],
        "improvement":{"latency_pct":round((b["latency"]-a["latency"])/max(.01,b["latency"])*100,2),
            "p95_pct":round((b["p95_latency"]-a["p95_latency"])/max(.01,b["p95_latency"])*100,2),
            "fairness_points":round(a["fairness"]-b["fairness"],5)}}
