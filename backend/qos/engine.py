"""Deterministic packet-agnostic traffic and queue simulator."""
from __future__ import annotations

import math
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


CLASSES = ["Voice", "Video call", "Gaming", "Streaming", "Browsing", "Downloads", "Cloud backup", "Unknown"]
PROTOCOLS = ["UDP", "UDP", "UDP", "QUIC", "HTTPS", "HTTPS", "HTTPS", "TCP"]
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
SCENARIOS = {
    "work_call_iso": "Work call vs ISO download",
    "capacity_drop": "100 → 20 Mbps capacity drop",
    "gaming_streams": "Gaming vs three TV streams",
    "bulk_backup": "Downloads vs cloud backups",
}


def jain_fairness(values: list[float]) -> float:
    """Jain index across nonnegative class throughputs; zero when no service."""
    vals = [max(0.0, float(x)) for x in values]
    if not vals or sum(x * x for x in vals) == 0:
        return 0.0
    return (sum(vals) ** 2) / (len(vals) * sum(x * x for x in vals))


def weighted_allocate(demand: dict[str, float], capacity: float, policies: dict[str, dict], adaptive: bool) -> dict[str, float]:
    """Allocate rates with capped weighted max-min fill and minimum-rate guarantees.

    If requested minima exceed capacity, minima are scaled proportionally, so an
    infeasible policy can never claim more bandwidth than the link can provide.
    """
    names = list(demand)
    caps = {n: max(0, min(demand[n], float(policies[n]["limit"]))) for n in names}
    minima = {n: min(caps[n], max(0, float(policies[n]["minimum"]))) for n in names}
    result = {n: 0.0 for n in names}
    min_total = sum(minima.values())
    if min_total > capacity and min_total:
        result = {n: minima[n] * capacity / min_total for n in names}
        return result
    result = minima.copy()
    remaining = max(0.0, capacity - sum(result.values()))
    active = {n for n in names if caps[n] > result[n] + 1e-9}
    while active and remaining > 1e-9:
        weights = {n: (max(0.1, float(policies[n]["weight"])) * max(1, 9 - int(policies[n]["priority"])) if adaptive else 1.0) for n in active}
        total_w = sum(weights.values())
        progressed = 0.0
        saturated = set()
        for n in active:
            share = remaining * weights[n] / total_w
            add = min(share, caps[n] - result[n])
            result[n] += add
            progressed += add
            if caps[n] - result[n] <= 1e-9:
                saturated.add(n)
        remaining -= progressed
        active -= saturated
        if progressed <= 1e-12:
            break
    return result


class CapacityEstimator:
    """EWMA capacity estimator that tracks sudden downshifts conservatively."""
    def __init__(self, initial: float = 100.0, alpha: float = 0.45):
        self.estimate = initial
        self.alpha = alpha

    def observe(self, measured: float) -> float:
        measured = max(1.0, float(measured))
        self.estimate = measured if measured < self.estimate else self.alpha * measured + (1 - self.alpha) * self.estimate
        return self.estimate


def scenario_sessions(scenario: str) -> list[dict[str, Any]]:
    specs = {
        "work_call_iso": [("Video call", 6, 2), ("Downloads", 78, 1)],
        "capacity_drop": [("Voice", 1, 2), ("Video call", 10, 2), ("Streaming", 32, 1), ("Browsing", 8, 2), ("Downloads", 55, 1)],
        "gaming_streams": [("Gaming", 5, 2), ("Streaming", 24, 1), ("Streaming", 22, 1), ("Streaming", 20, 1), ("Browsing", 5, 2)],
        "bulk_backup": [("Downloads", 48, 2), ("Downloads", 42, 1), ("Cloud backup", 35, 2), ("Cloud backup", 30, 1), ("Browsing", 6, 2)],
    }
    result = []
    for i, (kind, rate, count) in enumerate(specs.get(scenario, specs["work_call_iso"])):
        for j in range(count):
            result.append({"id": f"s{i}{j}", "name": ["Weekly team sync", "Linux ISO mirror", "Living room TV", "Console session", "Photo backup", "Web browsing"][((i+j) % 6)],
                           "class": kind, "protocol": PROTOCOLS[CLASSES.index(kind)], "offered": float(rate),
                           "confidence": round(0.86 + 0.13 * ((i + j) % 4) / 4, 2), "priority": "Interactive" if kind in ("Voice", "Video call", "Gaming", "Browsing") else "Bulk"})
    return result


@dataclass
class Simulator:
    running: bool = False
    paused: bool = False
    adaptive: bool = True
    scenario: str = "work_call_iso"
    capacity: float = 100.0
    estimated_capacity: float = 100.0
    policies: dict[str, dict] = field(default_factory=lambda: {k: v.copy() for k, v in DEFAULT_POLICIES.items()})
    sessions: list[dict] = field(default_factory=list)
    intents: list[dict] = field(default_factory=list)
    tick: int = 0
    seed: int = 42
    latency_hist: deque = field(default_factory=lambda: deque(maxlen=100))
    timeline: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    policy_log: list[dict] = field(default_factory=list)
    last_metrics: dict = field(default_factory=dict)
    started_at: float | None = None
    _rng: random.Random = field(default_factory=lambda: random.Random(42))
    _traffic_rng: random.Random = field(default_factory=lambda: random.Random(42))
    estimator: CapacityEstimator = field(default_factory=CapacityEstimator)

    def start(self, scenario: str | None = None, adaptive: bool = True, seed: int = 42) -> dict:
        if scenario:
            if scenario not in SCENARIOS:
                raise ValueError("Unknown experiment scenario")
            self.scenario = scenario
        self.adaptive, self.seed = bool(adaptive), int(seed)
        self._rng = random.Random(self.seed)
        self._traffic_rng = random.Random(self.seed + 991)
        self.tick, self.latency_hist = 0, deque(maxlen=100)
        self.capacity = 100.0
        self.estimated_capacity = self.capacity
        self.estimator = CapacityEstimator(self.capacity)
        self.sessions = scenario_sessions(self.scenario)
        self.running, self.paused, self.started_at = True, False, time.time()
        self.timeline = [{"time": time.time(), "event": f"Experiment started · {SCENARIOS[self.scenario]}", "type": "info"}]
        return self.step()

    def step(self) -> dict:
        if not self.sessions:
            self.sessions = scenario_sessions(self.scenario)
        self.tick += 1
        # A demand/capacity transient creates realistic queue and delay dynamics.
        offered_by_class = {c: 0.0 for c in CLASSES}
        for s in self.sessions:
            offered_by_class[s["class"]] = offered_by_class.get(s["class"], 0) + s["offered"] * (0.95 + self._traffic_rng.random() * 0.1)
        if self.scenario == "capacity_drop" and self.tick == 8:
            self.capacity = 20.0
            self.timeline.append({"time": time.time(), "event": "Link capacity dropped from 100 to 20 Mbps", "type": "warning"})
        self.estimated_capacity = self.estimator.observe(self.capacity)
        if self.adaptive:
            policy = self._effective_policies()
        else:
            policy = {c: {"minimum": 0, "limit": 1000, "weight": 1, "priority": 5} for c in CLASSES}
        allocated = weighted_allocate(offered_by_class, self.estimated_capacity, policy, self.adaptive)
        offered = sum(offered_by_class.values())
        throughput = sum(allocated.values())
        congestion = max(0.0, offered / max(1, self.estimated_capacity) - 0.82)
        qdepth = max(0.0, (0 if not self.last_metrics else self.last_metrics.get("queue_depth", 0) * 0.64) + congestion * 70 + (offered - throughput) * 0.12)
        latency = (8 + min(48, qdepth * 0.4) if self.adaptive else 10 + qdepth * 1.25) + self._rng.random() * 2
        jitter = 1.1 + min(24, qdepth * (0.12 if self.adaptive else 0.25)) + self._rng.random() * 1.2
        loss = min(12, max(0, congestion * (2.0 if self.adaptive else 5.2) + max(0, qdepth - 60) * 0.025))
        self.latency_hist.append(latency)
        p95 = sorted(self.latency_hist)[max(0, math.ceil(len(self.latency_hist) * .95) - 1)]
        fairness = jain_fairness(list(allocated.values()))
        per_class = {c: round(allocated.get(c, 0), 2) for c in CLASSES if offered_by_class.get(c, 0) > 0}
        for s in self.sessions:
            class_total = offered_by_class.get(s["class"], 0)
            s["throughput"] = round(allocated.get(s["class"], 0) * s["offered"] / class_total, 2) if class_total else 0
            s["qos_class"] = s["class"]
        self.last_metrics = {"tick": self.tick, "time": time.time(), "throughput": round(throughput, 2), "capacity": self.capacity,
                             "estimated_capacity": round(self.estimated_capacity, 2), "offered": round(offered, 2), "latency": round(latency, 2), "p95_latency": round(p95, 2),
                             "jitter": round(jitter, 2), "packet_loss": round(loss, 3), "queue_depth": round(qdepth, 2), "fairness": round(fairness, 4),
                             "throughput_by_class": per_class, "active_sessions": len(self.sessions), "adaptive": self.adaptive, "simulated": True}
        self.timeline.append({"time": time.time(), "event": f"Tick {self.tick}: {throughput:.1f} Mbps served; {latency:.1f} ms latency", "type": "metric"})
        return self.snapshot()

    def _effective_policies(self) -> dict[str, dict]:
        result = {k: v.copy() for k, v in self.policies.items()}
        now = time.time()
        for intent in list(self.intents):
            if intent["expires_at"] <= now:
                self.intents.remove(intent)
                self.timeline.append({"time": now, "event": f"Intent expired · {intent['name']}", "type": "info"})
                continue
            target = {"Prioritize Work Call": "Video call", "Gaming": "Gaming", "Voice": "Voice"}.get(intent["name"])
            if target:
                result[target]["minimum"] *= 1.8
                result[target]["weight"] *= 2.5
        return result

    def snapshot(self) -> dict:
        return {"status": "running" if self.running and not self.paused else ("paused" if self.paused else "stopped"),
                "metrics": self.last_metrics, "sessions": self.sessions, "policies": self.policies,
                "intents": self.intents, "timeline": self.timeline[-40:], "simulated": True,
                "controller": "Adaptive" if self.adaptive else "Baseline", "policy_changes": self.policy_log[-10:]}

    def set_capacity(self, capacity: float) -> dict:
        if capacity < 1 or capacity > 1000:
            raise ValueError("Capacity must be between 1 and 1000 Mbps")
        before = self.capacity
        self.capacity = float(capacity)
        self.timeline.append({"time": time.time(), "event": f"Link capacity changed: {before:g} → {capacity:g} Mbps", "type": "warning"})
        return self.step() if self.running and not self.paused else self.snapshot()

    def apply_policies(self, policies: dict[str, dict]) -> dict:
        if set(policies) != set(CLASSES):
            raise ValueError("Policy must configure all traffic classes")
        for name, cfg in policies.items():
            if set(cfg) != {"priority", "minimum", "limit", "weight"} or not 1 <= int(cfg["priority"]) <= 8 or not 0 <= float(cfg["minimum"]) <= 1000 or not 0.1 <= float(cfg["weight"]) <= 100 or not 0.1 <= float(cfg["limit"]) <= 1000:
                raise ValueError(f"Invalid policy values for {name}")
        old = self.policies
        event={"id": str(uuid.uuid4()), "time": time.time(), "before": old, "after": policies, "label": "Policy applied"}
        self.history.append(event)
        self.policy_log.append(event)
        self.policies = {k: {a: float(b) if a in ("minimum", "limit", "weight") else int(b) for a, b in v.items()} for k, v in policies.items()}
        self.timeline.append({"time": time.time(), "event": "Adaptive policy updated", "type": "policy"})
        return self.snapshot()

    def rollback(self) -> dict:
        if not self.history:
            raise ValueError("No policy change available to roll back")
        change = self.history.pop()
        self.policies = change["before"]
        self.policy_log.append({"id":str(uuid.uuid4()),"time":time.time(),"before":change["after"],"after":self.policies,"label":"Policy rollback"})
        self.timeline.append({"time": time.time(), "event": "Policy rollback applied", "type": "policy"})
        return self.snapshot()

    def activate_intent(self, name: str, duration: int) -> dict:
        if name not in ("Prioritize Work Call", "Gaming", "Voice", "Balanced household") or not 1 <= duration <= 240:
            raise ValueError("Select a supported intent and duration between 1 and 240 minutes")
        intent = {"id": str(uuid.uuid4()), "name": name, "duration_minutes": duration, "activated_at": time.time(), "expires_at": time.time() + duration * 60}
        self.intents.append(intent)
        self.timeline.append({"time": time.time(), "event": f"Intent activated · {name} ({duration} min)", "type": "policy"})
        return self.step() if self.running else self.snapshot()

    def cancel_intent(self, intent_id: str) -> dict:
        self.intents = [x for x in self.intents if x["id"] != intent_id]
        self.timeline.append({"time": time.time(), "event": "Service intent cancelled", "type": "info"})
        return self.snapshot()

