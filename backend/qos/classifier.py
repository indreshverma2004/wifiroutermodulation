"""Deterministic classification using flow metadata only (never payloads)."""
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FlowMetadata:
    protocol: str
    source_port: int
    destination_port: int
    dscp: int
    mean_packet_bytes: int
    packets_per_second: float
    burstiness: float
    active_seconds: float
    observed_mbps: float

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "FlowMetadata":
        return cls(
            protocol=str(value.get("protocol", "unknown")).lower(),
            source_port=int(value.get("source_port", 0)),
            destination_port=int(value.get("destination_port", 0)),
            dscp=int(value.get("dscp", 0)),
            mean_packet_bytes=max(1, int(value.get("mean_packet_bytes", 500))),
            packets_per_second=max(0.0, float(value.get("packets_per_second", 0))),
            burstiness=max(0.0, min(1.0, float(value.get("burstiness", 0.5)))),
            active_seconds=max(0.0, float(value.get("active_seconds", 0))),
            observed_mbps=max(0.0, float(value.get("observed_mbps", 0))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_flow(metadata: FlowMetadata | dict[str, Any]) -> dict[str, Any]:
    """Return broad class, confidence, and an inspectable rule explanation.

    This conservative prototype uses DSCP, transport protocol, ports, packet
    sizes/rates, burstiness and flow duration. It does not decrypt payloads.
    Encrypted traffic is inherently ambiguous; confidence records that ambiguity.
    """
    flow = metadata if isinstance(metadata, FlowMetadata) else FlowMetadata.from_mapping(metadata)
    proto = flow.protocol
    dport = flow.destination_port
    udp = proto in {"udp", "quic"}

    if udp and flow.dscp >= 46:
        label, confidence, rule = "Voice", 0.96, "EF DSCP with UDP transport"
    elif udp and (flow.dscp >= 34 or dport in {3478, 3479, 5004, 5005}):
        label, confidence, rule = "Video call", (0.91 if flow.dscp >= 34 else 0.82), "Interactive-media DSCP or common real-time media port"
    elif udp and flow.mean_packet_bytes <= 420 and flow.packets_per_second >= 35:
        label, confidence, rule = "Gaming", 0.78, "Small frequent UDP packets"
    elif (proto == "quic" or (udp and dport == 443)) and flow.mean_packet_bytes >= 800 and flow.observed_mbps >= 2:
        label, confidence, rule = "Streaming", 0.72, "Sustained large-packet QUIC/UDP flow; encrypted application remains ambiguous"
    elif flow.active_seconds >= 45 and flow.observed_mbps >= 5:
        if flow.burstiness <= 0.3:
            label, confidence, rule = "Cloud backup", 0.77, "Long-lived high-rate flow with steady transfer pattern"
        else:
            label, confidence, rule = "Downloads", 0.75, "Long-lived high-rate bursty transfer"
    elif proto in {"tcp", "quic", "udp"} and (dport in {80, 443, 8080, 8443} or proto == "tcp"):
        if flow.active_seconds < 45 and flow.observed_mbps < 8:
            label, confidence, rule = "Browsing", 0.64, "Short or low-rate web transport flow"
        else:
            label, confidence, rule = "Unknown", 0.42, "Encrypted/general transport flow lacks distinctive metadata"
    else:
        label, confidence, rule = "Unknown", 0.35, "Insufficient non-payload metadata"

    return {"class": label, "confidence": confidence, "rule": rule, "metadata": flow.to_dict()}
