"""Discrete-time fluid packet queues with finite buffers and explicit drops."""
from __future__ import annotations
import math


class QueueModel:
    def __init__(self, buffer_bytes_per_class: int = 524_288, base_rtt_ms: float = 18.0, drain_horizon_s: float = 2.0, shared_fifo: bool = False):
        if buffer_bytes_per_class < 1 or base_rtt_ms < 0 or drain_horizon_s <= 0:
            raise ValueError("Invalid queue model settings")
        self.buffer_bytes_per_class = int(buffer_bytes_per_class)
        self.base_rtt_ms = float(base_rtt_ms)
        self.drain_horizon_s = float(drain_horizon_s)
        self.shared_fifo = bool(shared_fifo)
        self.queued_bytes: dict[str, float] = {}
        self.previous_delay_ms: dict[str, float] = {}

    def reset(self) -> None:
        self.queued_bytes.clear()
        self.previous_delay_ms.clear()

    def backlog_demand_mbps(self, classes: list[str]) -> dict[str, float]:
        """Extra requested rate to drain existing queue over a target horizon."""
        return {c: self.queued_bytes.get(c, 0.0) * 8 / 1_000_000 / self.drain_horizon_s for c in classes}

    def advance(self, offered_mbps: dict[str, float], allocated_mbps: dict[str, float], packet_bytes: dict[str, float], tick_seconds: float) -> dict:
        if tick_seconds <= 0:
            raise ValueError("Tick duration must be positive")
        total_arrived_packets = 0.0
        total_dropped_packets = 0.0
        total_served_mbps = 0.0
        total_queue_packets = 0.0
        delay_by_class: dict[str, float] = {}
        if self.shared_fifo:
            active = [name for name, rate in offered_mbps.items() if rate > 0]
            offered_total = sum(max(0.0, float(rate)) for rate in offered_mbps.values())
            allocated_total = sum(max(0.0, float(rate)) for rate in allocated_mbps.values())
            weighted_size = sum(max(0.0, float(offered_mbps[name])) * max(1.0, float(packet_bytes.get(name, 1000))) for name in active) / offered_total if offered_total else 1000.0
            arrived_bytes = offered_total * 125_000 * tick_seconds
            service_bytes = allocated_total * 125_000 * tick_seconds
            available = self.queued_bytes.get("__shared__", 0.0) + arrived_bytes
            dropped = max(0.0, available - service_bytes - self.buffer_bytes_per_class)
            queued = min(self.buffer_bytes_per_class, max(0.0, available - service_bytes))
            self.queued_bytes = {"__shared__": queued}
            total_arrived_packets = arrived_bytes / weighted_size
            total_dropped_packets = dropped / weighted_size
            total_served_mbps = allocated_total
            total_queue_packets = queued / weighted_size
            queue_delay = queued * 8 / max(1.0, allocated_total * 1_000_000) * 1000
            shared_delay = self.base_rtt_ms + queue_delay
            delay_by_class = {name: shared_delay for name in active}
            interactive = [c for c in ("Voice", "Video call", "Gaming") if c in active]
            measured = interactive or active
            load = sum(max(0.0, float(offered_mbps[c])) for c in measured)
            latency = sum(delay_by_class[c] * max(0.0, float(offered_mbps[c])) for c in measured) / load if load else self.base_rtt_ms
            jitter = abs(shared_delay - self.previous_delay_ms.get("__shared__", shared_delay))
            self.previous_delay_ms = {"__shared__": shared_delay}
            loss_pct = 100.0 * total_dropped_packets / total_arrived_packets if total_arrived_packets else 0.0
            return {"throughput_mbps": total_served_mbps, "queue_packets": total_queue_packets,
                    "packet_loss_pct": loss_pct, "dropped_packets": total_dropped_packets,
                    "arrived_packets": total_arrived_packets, "latency_ms": latency,
                    "jitter_ms": jitter, "latency_by_class_ms": delay_by_class}

        names = set(offered_mbps) | set(self.queued_bytes) | set(allocated_mbps)
        for name in names:
            size = max(1.0, float(packet_bytes.get(name, 1000.0)))
            arrived_bytes = max(0.0, float(offered_mbps.get(name, 0.0))) * 125_000 * tick_seconds
            service_mbps = max(0.0, float(allocated_mbps.get(name, 0.0)))
            service_bytes = service_mbps * 125_000 * tick_seconds
            arrived_packets = arrived_bytes / size
            total_arrived_packets += arrived_packets
            available = self.queued_bytes.get(name, 0.0) + arrived_bytes
            dropped = max(0.0, available - service_bytes - self.buffer_bytes_per_class)
            queued = min(self.buffer_bytes_per_class, max(0.0, available - service_bytes))
            total_dropped_packets += dropped / size
            self.queued_bytes[name] = queued
            total_queue_packets += queued / size
            total_served_mbps += service_mbps
            queue_delay = queued * 8 / max(1.0, service_mbps * 1_000_000) * 1000
            delay_by_class[name] = self.base_rtt_ms + queue_delay

        active = [c for c, rate in offered_mbps.items() if rate > 0]
        interactive = [c for c in ("Voice", "Video call", "Gaming") if c in active]
        measured = interactive or active
        measured_load = sum(max(0.0, float(offered_mbps[c])) for c in measured)
        latency = (sum(delay_by_class[c] * max(0.0, float(offered_mbps[c])) for c in measured) / measured_load
                   if measured_load else self.base_rtt_ms)
        jitter_classes = interactive or active
        jitter_samples = [abs(delay_by_class[c] - self.previous_delay_ms[c]) for c in jitter_classes if c in self.previous_delay_ms]
        jitter = sum(jitter_samples) / len(jitter_samples) if jitter_samples else 0.0
        self.previous_delay_ms = delay_by_class
        loss_pct = 100.0 * total_dropped_packets / total_arrived_packets if total_arrived_packets else 0.0
        return {"throughput_mbps": total_served_mbps, "queue_packets": total_queue_packets,
                "packet_loss_pct": loss_pct, "dropped_packets": total_dropped_packets,
                "arrived_packets": total_arrived_packets, "latency_ms": latency,
                "jitter_ms": jitter, "latency_by_class_ms": delay_by_class}

