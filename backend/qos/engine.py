"""Simulation coordinator for metadata flows, probe estimates, and finite queues."""
from __future__ import annotations
import math
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .capacity import CapacityEstimator
from .queueing import QueueModel
from .traffic import SCENARIO_CONFIG, generate_devices, generate_sessions

CLASSES = ["Voice", "Video call", "Gaming", "Streaming", "Browsing", "Downloads", "Cloud backup", "Unknown"]
DEFAULT_POLICIES = {
    "Voice": {"priority": 1, "minimum": 1, "limit": 8, "weight": 8},
    "Video call": {"priority": 2, "minimum": 4, "limit": 30, "weight": 6},
    "Gaming": {"priority": 2, "minimum": 2, "limit": 18, "weight": 6},
    "Streaming": {"priority": 3, "minimum": 3, "limit": 45, "weight": 4},
    "Browsing": {"priority": 4, "minimum": 1, "limit": 15, "weight": 3},
    "Downloads": {"priority": 5, "minimum": 1, "limit": 80, "weight": 2},
    "Cloud backup": {"priority": 6, "minimum": 1, "limit": 40, "weight": 1},
    "Unknown": {"priority": 7, "minimum": 0.5, "limit": 20, "weight": 1},
}
SCENARIOS = {key: value["name"] for key, value in SCENARIO_CONFIG.items()}


def jain_fairness(values: list[float]) -> float:
    vals = [max(0.0, float(value)) for value in values]
    squares = sum(value * value for value in vals)
    return 0.0 if not vals or squares == 0 else sum(vals) ** 2 / (len(vals) * squares)


def weighted_allocate(demand: dict[str, float], capacity: float, policies: dict[str, dict], adaptive: bool) -> dict[str, float]:
    """Capped weighted max-min fill with proportional infeasible minima."""
    names = list(demand)
    caps = {name: max(0.0, min(float(demand[name]), float(policies[name]["limit"]))) for name in names}
    minima = {name: min(caps[name], max(0.0, float(policies[name]["minimum"]))) for name in names}
    minimum_total = sum(minima.values())
    if minimum_total > capacity and minimum_total:
        return {name: minima[name] * capacity / minimum_total for name in names}
    allocation = minima.copy()
    remaining = max(0.0, capacity - sum(allocation.values()))
    active = {name for name in names if caps[name] > allocation[name] + 1e-9}
    while active and remaining > 1e-9:
        weights = {name: (max(0.1, float(policies[name]["weight"])) * max(1, 9-int(policies[name]["priority"])) if adaptive else 1.0) for name in active}
        total_weight = sum(weights.values())
        progressed, saturated = 0.0, set()
        for name in active:
            addition = min(remaining * weights[name] / total_weight, caps[name] - allocation[name])
            allocation[name] += addition
            progressed += addition
            if caps[name] - allocation[name] <= 1e-9:
                saturated.add(name)
        remaining -= progressed
        active -= saturated
        if progressed <= 1e-12:
            break
    return allocation


def fifo_allocate(demand: dict[str, float], capacity: float) -> dict[str, float]:
    """Unprioritized FIFO reference: active classes share service by offered load."""
    caps = {name: max(0.0, float(rate)) for name, rate in demand.items()}
    total = sum(caps.values())
    if total <= capacity or total <= 0:
        return caps
    return {name: rate * capacity / total for name, rate in caps.items()}


def scenario_sessions(scenario: str, seed: int = 42) -> list[dict[str, Any]]:
    """Compatibility helper returning generated flows classified from metadata."""
    return generate_sessions(scenario, seed)


@dataclass
class Simulator:
    running: bool = False
    paused: bool = False
    adaptive: bool = True
    scenario: str = "work_call_iso"
    capacity: float = 100.0  # simulator's hidden ground truth, never given directly to the controller
    estimated_capacity: float = 100.0
    policies: dict[str, dict] = field(default_factory=lambda: {key: value.copy() for key, value in DEFAULT_POLICIES.items()})
    sessions: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    intents: list[dict] = field(default_factory=list)
    tick: int = 0
    seed: int = 42
    latency_hist: deque = field(default_factory=lambda: deque(maxlen=100))
    timeline: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    policy_log: list[dict] = field(default_factory=list)
    last_metrics: dict = field(default_factory=dict)
    timeseries: list[dict] = field(default_factory=list)
    capacity_samples: list[dict] = field(default_factory=list)
    started_at: float | None = None
    _rng: random.Random = field(default_factory=lambda: random.Random(42))
    _traffic_rng: random.Random = field(default_factory=lambda: random.Random(1033))
    _probe_rng: random.Random = field(default_factory=lambda: random.Random(17))
    estimator: CapacityEstimator = field(default_factory=CapacityEstimator)
    queues: QueueModel = field(default_factory=QueueModel)

    def start(self, scenario: str | None = None, adaptive: bool = True, seed: int = 42) -> dict:
        if scenario:
            if scenario not in SCENARIOS:
                raise ValueError("Unknown experiment scenario")
            self.scenario = scenario
        config = SCENARIO_CONFIG[self.scenario]
        self.adaptive, self.seed = bool(adaptive), int(seed)
        self._rng = random.Random(self.seed)
        self._traffic_rng = random.Random(self.seed + 991)
        self._probe_rng = random.Random(self.seed + 1777)
        self.tick = 0
        self.latency_hist = deque(maxlen=100)
        self.capacity = float(config["link_mbps"])
        self.estimated_capacity = self.capacity
        self.estimator = CapacityEstimator(self.capacity)
        impairment = config["impairments"]
        self.queues = QueueModel(impairment["buffer_bytes_per_class"], impairment["base_rtt_ms"], shared_fifo=not self.adaptive)
        self.sessions = generate_sessions(self.scenario, self.seed)
        self.devices = generate_devices(self.scenario)
        self.timeseries, self.capacity_samples = [], []
        self.last_metrics = {}
        self.running, self.paused, self.started_at = True, False, time.time()
        self.timeline = [{"time": time.time(), "event": f"Experiment started · {SCENARIOS[self.scenario]}", "type": "info"}]
        return self.step()

    def step(self) -> dict:
        if not self.sessions:
            self.sessions = generate_sessions(self.scenario, self.seed)
        self.tick += 1
        config = SCENARIO_CONFIG[self.scenario]
        impairment = config["impairments"]
        capacity_change = config.get("capacity_change")
        if capacity_change and self.tick == capacity_change["tick"]:
            before = self.capacity
            self.capacity = float(capacity_change["capacity_mbps"])
            self.timeline.append({"time": time.time(), "event": f"WAN capacity changed from {before:g} to {self.capacity:g} Mbps", "type": "warning"})

        # The estimator sees a noisy probe observation, never the hidden true capacity.
        noise = impairment["probe_noise_fraction"]
        probe = max(1.0, self.capacity * (1 + self._probe_rng.uniform(-noise, noise)))
        self.estimated_capacity = self.estimator.observe(probe)
        self.capacity_samples.append({"tick": self.tick, "true_mbps": self.capacity, "probe_mbps": round(probe, 3), "estimate_mbps": round(self.estimated_capacity, 3)})

        offered_by_class = {name: 0.0 for name in CLASSES}
        packet_bytes_by_class: dict[str, float] = {}
        for session in self.sessions:
            if not session["manual_override"]:
                # Classification is performed from the generated flow metadata at flow creation.
                pass
            offered = session["offered"] * (0.95 + self._traffic_rng.random() * 0.1)
            session["current_offered"] = offered
            kind = session["class"]
            offered_by_class[kind] += offered
            packet_bytes_by_class[kind] = session["mean_packet_bytes"]

        queue_demand = self.queues.backlog_demand_mbps(CLASSES)
        schedulable_demand = {name: offered_by_class[name] + queue_demand[name] for name in CLASSES}
        policy = self._effective_policies() if self.adaptive else {name: {"minimum":0,"limit":1000,"weight":1,"priority":5} for name in CLASSES}
        scheduled_allocation = (weighted_allocate(schedulable_demand, self.estimated_capacity, policy, True)
                                if self.adaptive else fifo_allocate(offered_by_class, self.estimated_capacity))
        scheduled_total = sum(scheduled_allocation.values())
        physical_scale = min(1.0, self.capacity / scheduled_total) if scheduled_total else 1.0
        allocation = {name: rate * physical_scale for name,rate in scheduled_allocation.items()}
        queue_metrics = self.queues.advance(offered_by_class, allocation, packet_bytes_by_class, impairment["tick_seconds"])
        offered_total = sum(offered_by_class.values())
        throughput = queue_metrics["throughput_mbps"]
        self.latency_hist.append(queue_metrics["latency_ms"])
        p95 = sorted(self.latency_hist)[max(0, math.ceil(len(self.latency_hist) * .95)-1)]
        for session in self.sessions:
            class_total = offered_by_class.get(session["class"], 0.0)
            share = session["current_offered"] / class_total if class_total else 0.0
            session["throughput"] = round(allocation.get(session["class"], 0.0) * share, 3)
            session["qos_class"] = session["class"]
        self._update_device_usage(float(impairment["tick_seconds"]))
        class_throughput = {name: round(allocation[name], 3) for name in CLASSES if offered_by_class[name] > 0}
        active_flow_rates = [session["throughput"] for session in self.sessions if session.get("current_offered", 0) > 0]
        classified_count = len(self.sessions)
        class_accuracy = sum(session["automatic_class"] == session["truth_class"] for session in self.sessions) / max(1, classified_count)
        metrics = {"tick":self.tick,"time":time.time(),"throughput":round(throughput,3),"capacity":self.capacity,
            "estimated_capacity":round(self.estimated_capacity,3),"capacity_probe":round(probe,3),"capacity_error_pct":round(abs(self.estimated_capacity-self.capacity)/self.capacity*100,3),
            "offered":round(offered_total,3),"latency":round(queue_metrics["latency_ms"],3),"p95_latency":round(p95,3),
            "jitter":round(queue_metrics["jitter_ms"],3),"packet_loss":round(queue_metrics["packet_loss_pct"],5),"queue_depth":round(queue_metrics["queue_packets"],2),
            "dropped_packets":round(queue_metrics["dropped_packets"],2),"arrived_packets":round(queue_metrics["arrived_packets"],2),
            "scheduled_mbps":round(scheduled_total,3),"capacity_limited":physical_scale < 0.999,
            "fairness":round(jain_fairness(active_flow_rates),5),"throughput_by_class":class_throughput,
            "latency_by_class":{key:round(value,3) for key,value in queue_metrics["latency_by_class_ms"].items()},
            "active_sessions":len(self.sessions),"connected_devices":len(self.devices),
            "active_devices":sum(device["active"] for device in self.devices),
            "device_throughput_mbps":{device["id"]:device["total_mbps"] for device in self.devices},
            "classifier_accuracy":round(class_accuracy,5),
            "adaptive":self.adaptive,"simulated":True}
        self.last_metrics = metrics
        self.timeseries.append({key:value for key,value in metrics.items() if key != "time"})
        self.timeline.append({"time":time.time(),"event":f"Tick {self.tick}: {throughput:.1f} Mbps served; {metrics['latency']:.1f} ms interactive latency","type":"metric"})
        return self.snapshot()

    def _update_device_usage(self, tick_seconds: float) -> None:
        usage = {device["id"]:{"download":0.0,"upload":0.0,"sessions":0} for device in self.devices}
        for session in self.sessions:
            device_usage = usage.get(session["device_id"])
            if device_usage is None:
                continue
            rate = max(0.0, float(session["throughput"]))
            if session["direction"] == "upload":
                device_usage["upload"] += rate
            elif session["direction"] == "bidirectional":
                device_usage["download"] += rate * 0.7
                device_usage["upload"] += rate * 0.3
            else:
                device_usage["download"] += rate
            device_usage["sessions"] += int(session.get("current_offered", 0) > 0)
        for device in self.devices:
            current = usage[device["id"]]
            download, upload = current["download"], current["upload"]
            total = download + upload
            device["download_mbps"] = round(download, 3)
            device["upload_mbps"] = round(upload, 3)
            device["total_mbps"] = round(total, 3)
            device["session_count"] = current["sessions"]
            device["active"] = total > 0.01
            device["usage_mb"] = round(device["usage_mb"] + total * 0.125 * tick_seconds, 3)
            device["history"] = (device["history"] + [round(total,3)])[-30:]

    def _effective_policies(self) -> dict[str, dict]:
        result={key:value.copy() for key,value in self.policies.items()}
        now=time.time()
        for intent in list(self.intents):
            if intent["expires_at"] <= now:
                self.intents.remove(intent)
                self.timeline.append({"time":now,"event":f"Intent expired · {intent['name']}","type":"info"})
                continue
            target={"Prioritize Work Call":"Video call","Gaming":"Gaming","Voice":"Voice"}.get(intent["name"])
            if target:
                result[target]["minimum"] *= 1.8
                result[target]["weight"] *= 2.5
        return result

    def snapshot(self) -> dict:
        safe_sessions=[]
        for session in self.sessions:
            safe={key:value for key,value in session.items() if key not in {"truth_class","automatic_class","manual_override","demand_factor","current_offered"}}
            safe_sessions.append(safe)
        return {"status":"running" if self.running and not self.paused else ("paused" if self.paused else "stopped"),
            "metrics":self.last_metrics,"sessions":safe_sessions,"devices":self.devices,"policies":self.policies,"intents":self.intents,
            "timeline":self.timeline[-40:],"simulated":True,"controller":"Adaptive" if self.adaptive else "Baseline",
            "policy_changes":self.policy_log[-10:]}

    def set_capacity(self, capacity: float) -> dict:
        if capacity < 1 or capacity > 1000:
            raise ValueError("Capacity must be between 1 and 1000 Mbps")
        before=self.capacity
        self.capacity=float(capacity)
        self.timeline.append({"time":time.time(),"event":f"Simulator link setting changed from {before:g} to {capacity:g} Mbps","type":"warning"})
        return self.step() if self.running and not self.paused else self.snapshot()

    def apply_policies(self, policies: dict[str, dict]) -> dict:
        if set(policies) != set(CLASSES):
            raise ValueError("Policy must configure all traffic classes")
        for name,cfg in policies.items():
            if set(cfg)!={"priority","minimum","limit","weight"} or not 1<=int(cfg["priority"])<=8 or not 0<=float(cfg["minimum"])<=1000 or not .1<=float(cfg["weight"])<=100 or not .1<=float(cfg["limit"])<=1000:
                raise ValueError(f"Invalid policy values for {name}")
        old=self.policies
        event={"id":str(uuid.uuid4()),"time":time.time(),"before":old,"after":policies,"label":"Policy applied"}
        self.history.append(event); self.policy_log.append(event)
        self.policies={key:{name:float(value) if name in ("minimum","limit","weight") else int(value) for name,value in cfg.items()} for key,cfg in policies.items()}
        self.timeline.append({"time":time.time(),"event":"Adaptive policy updated","type":"policy"})
        return self.snapshot()

    def rollback(self) -> dict:
        if not self.history:
            raise ValueError("No policy change available to roll back")
        change=self.history.pop(); self.policies=change["before"]
        self.policy_log.append({"id":str(uuid.uuid4()),"time":time.time(),"before":change["after"],"after":self.policies,"label":"Policy rollback"})
        self.timeline.append({"time":time.time(),"event":"Policy rollback applied","type":"policy"})
        return self.snapshot()

    def activate_intent(self, name: str, duration: int) -> dict:
        if name not in ("Prioritize Work Call","Gaming","Voice","Balanced household") or not 1<=duration<=240:
            raise ValueError("Select a supported intent and duration between 1 and 240 minutes")
        intent={"id":str(uuid.uuid4()),"name":name,"duration_minutes":duration,"activated_at":time.time(),"expires_at":time.time()+duration*60}
        self.intents.append(intent); self.timeline.append({"time":time.time(),"event":f"Intent activated · {name} ({duration} min)","type":"policy"})
        return self.step() if self.running and not self.paused else self.snapshot()

    def cancel_intent(self, intent_id: str) -> dict:
        self.intents=[item for item in self.intents if item["id"]!=intent_id]
        self.timeline.append({"time":time.time(),"event":"Service intent cancelled","type":"info"})
        return self.snapshot()
