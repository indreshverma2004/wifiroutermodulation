"""Reproducible synthetic flow scenarios and their network impairments."""
from __future__ import annotations
from typing import Any
from .classifier import classify_flow

DEVICE_PROFILES: list[dict[str, Any]] = [
    {"id":"work-laptop","name":"Jordan's laptop","type":"Laptop","room":"Office","wifi_band":"5 GHz","signal_dbm":-48,"ip":"192.168.1.21"},
    {"id":"gaming-pc","name":"Gaming PC","type":"Desktop","room":"Office","wifi_band":"Ethernet","signal_dbm":-1,"ip":"192.168.1.22"},
    {"id":"living-tv","name":"Living room TV","type":"Smart TV","room":"Living room","wifi_band":"5 GHz","signal_dbm":-55,"ip":"192.168.1.31"},
    {"id":"bedroom-tv","name":"Bedroom TV","type":"Smart TV","room":"Bedroom","wifi_band":"5 GHz","signal_dbm":-61,"ip":"192.168.1.32"},
    {"id":"kitchen-tv","name":"Kitchen TV","type":"Smart TV","room":"Kitchen","wifi_band":"2.4 GHz","signal_dbm":-67,"ip":"192.168.1.33"},
    {"id":"smartphone","name":"Jordan's phone","type":"Phone","room":"Mobile","wifi_band":"5 GHz","signal_dbm":-52,"ip":"192.168.1.41"},
    {"id":"security-camera","name":"Entry camera","type":"Camera","room":"Entry","wifi_band":"2.4 GHz","signal_dbm":-64,"ip":"192.168.1.51"},
    {"id":"smart-speaker","name":"Smart speaker","type":"IoT","room":"Living room","wifi_band":"2.4 GHz","signal_dbm":-58,"ip":"192.168.1.61"},
    {"id":"console","name":"Game console","type":"Console","room":"Living room","wifi_band":"Ethernet","signal_dbm":-1,"ip":"192.168.1.71"},
]
FLOW_DEVICES = {
    "Weekly team sync":"work-laptop", "Linux ISO mirror":"gaming-pc",
    "Voice handset":"smartphone", "Team video call":"work-laptop", "System update":"gaming-pc",
    "Console session":"console", "Living room TV":"living-tv", "Bedroom TV":"bedroom-tv",
    "Kitchen TV":"kitchen-tv", "Game library download":"gaming-pc", "Laptop sync":"work-laptop",
    "Photo archive":"smartphone", "Web browsing":"smartphone", "Phone browsing":"smartphone", "Living room stream":"living-tv",
    "Camera cloud sync":"security-camera", "Smart speaker heartbeat":"smart-speaker",
}

SCENARIO_CONFIG: dict[str, dict[str, Any]] = {
    "work_call_iso": {
        "name": "Work call vs ISO download", "link_mbps": 100, "capacity_change": None,
        "flows": [
            {"name":"Weekly team sync", "truth":"Video call", "protocol":"udp", "destination_port":3478, "dscp":34, "mean_packet_bytes":920, "packets_per_second":34, "burstiness":0.2, "active_seconds":120, "offered_mbps":10},
            {"name":"Linux ISO mirror", "truth":"Downloads", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1420, "packets_per_second":850, "burstiness":0.82, "active_seconds":600, "offered_mbps":150},
            {"name":"Living room stream", "truth":"Streaming", "protocol":"quic", "destination_port":443, "dscp":0, "mean_packet_bytes":1250, "packets_per_second":50, "burstiness":0.2, "active_seconds":300, "offered_mbps":8},
            {"name":"Phone browsing", "truth":"Browsing", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":620, "packets_per_second":8, "burstiness":0.5, "active_seconds":15, "offered_mbps":1},
            {"name":"Camera cloud sync", "truth":"Cloud backup", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1380, "packets_per_second":450, "burstiness":0.12, "active_seconds":900, "offered_mbps":5},
            {"name":"Smart speaker heartbeat", "truth":"Unknown", "protocol":"udp", "destination_port":5353, "dscp":0, "mean_packet_bytes":180, "packets_per_second":3, "burstiness":0.2, "active_seconds":300, "offered_mbps":0.1},
        ],
        "impairments":{"probe_noise_fraction":0.08,"buffer_bytes_per_class":524288,"base_rtt_ms":18,"tick_seconds":1},
    },
    "capacity_drop": {
        "name": "100 → 20 Mbps capacity drop", "link_mbps":100, "capacity_change":{"tick":8,"capacity_mbps":20},
        "flows": [
            {"name":"Voice handset", "truth":"Voice", "protocol":"udp", "destination_port":5060, "dscp":46, "mean_packet_bytes":220, "packets_per_second":50, "burstiness":0.18, "active_seconds":180, "offered_mbps":1},
            {"name":"Team video call", "truth":"Video call", "protocol":"udp", "destination_port":3478, "dscp":34, "mean_packet_bytes":920, "packets_per_second":34, "burstiness":0.22, "active_seconds":180, "offered_mbps":5},
            {"name":"Living room TV", "truth":"Streaming", "protocol":"quic", "destination_port":443, "dscp":0, "mean_packet_bytes":1250, "packets_per_second":120, "burstiness":0.18, "active_seconds":300, "offered_mbps":25},
            {"name":"Web browsing", "truth":"Browsing", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":620, "packets_per_second":8, "burstiness":0.52, "active_seconds":18, "offered_mbps":4},
            {"name":"System update", "truth":"Downloads", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1420, "packets_per_second":700, "burstiness":0.75, "active_seconds":480, "offered_mbps":70},
            {"name":"Camera cloud sync", "truth":"Cloud backup", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1380, "packets_per_second":450, "burstiness":0.12, "active_seconds":900, "offered_mbps":5},
            {"name":"Smart speaker heartbeat", "truth":"Unknown", "protocol":"udp", "destination_port":5353, "dscp":0, "mean_packet_bytes":180, "packets_per_second":3, "burstiness":0.2, "active_seconds":300, "offered_mbps":0.1},
        ],
        "impairments":{"probe_noise_fraction":0.08,"buffer_bytes_per_class":524288,"base_rtt_ms":18,"tick_seconds":1},
    },
    "gaming_streams": {
        "name":"Gaming vs three TV streams", "link_mbps":100, "capacity_change":None,
        "flows":[
            {"name":"Console session", "truth":"Gaming", "protocol":"udp", "destination_port":3074, "dscp":0, "mean_packet_bytes":180, "packets_per_second":100, "burstiness":0.25, "active_seconds":240, "offered_mbps":5},
            {"name":"Living room TV", "truth":"Streaming", "protocol":"quic", "destination_port":443, "dscp":0, "mean_packet_bytes":1250, "packets_per_second":120, "burstiness":0.2, "active_seconds":300, "offered_mbps":40},
            {"name":"Bedroom TV", "truth":"Streaming", "protocol":"quic", "destination_port":443, "dscp":0, "mean_packet_bytes":1250, "packets_per_second":120, "burstiness":0.2, "active_seconds":300, "offered_mbps":38},
            {"name":"Kitchen TV", "truth":"Streaming", "protocol":"quic", "destination_port":443, "dscp":0, "mean_packet_bytes":1250, "packets_per_second":120, "burstiness":0.2, "active_seconds":300, "offered_mbps":36},
            {"name":"Web browsing", "truth":"Browsing", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":620, "packets_per_second":8, "burstiness":0.5, "active_seconds":15, "offered_mbps":5},
            {"name":"Phone browsing", "truth":"Browsing", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":620, "packets_per_second":8, "burstiness":0.5, "active_seconds":15, "offered_mbps":1},
            {"name":"Camera cloud sync", "truth":"Cloud backup", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1380, "packets_per_second":450, "burstiness":0.12, "active_seconds":900, "offered_mbps":5},
            {"name":"Smart speaker heartbeat", "truth":"Unknown", "protocol":"udp", "destination_port":5353, "dscp":0, "mean_packet_bytes":180, "packets_per_second":3, "burstiness":0.2, "active_seconds":300, "offered_mbps":0.1},
        ],
        "impairments":{"probe_noise_fraction":0.08,"buffer_bytes_per_class":524288,"base_rtt_ms":18,"tick_seconds":1},
    },
    "bulk_backup": {
        "name":"Downloads vs cloud backups", "link_mbps":100, "capacity_change":None,
        "flows":[
            {"name":"Game library download", "truth":"Downloads", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1420, "packets_per_second":850, "burstiness":0.82, "active_seconds":600, "offered_mbps":48},
            {"name":"Laptop sync", "truth":"Cloud backup", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1380, "packets_per_second":500, "burstiness":0.12, "active_seconds":900, "offered_mbps":35},
            {"name":"Photo archive", "truth":"Cloud backup", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":1380, "packets_per_second":480, "burstiness":0.1, "active_seconds":900, "offered_mbps":30},
            {"name":"Web browsing", "truth":"Browsing", "protocol":"tcp", "destination_port":443, "dscp":0, "mean_packet_bytes":620, "packets_per_second":8, "burstiness":0.5, "active_seconds":15, "offered_mbps":6},
            {"name":"Living room stream", "truth":"Streaming", "protocol":"quic", "destination_port":443, "dscp":0, "mean_packet_bytes":1250, "packets_per_second":50, "burstiness":0.2, "active_seconds":300, "offered_mbps":8},
            {"name":"Smart speaker heartbeat", "truth":"Unknown", "protocol":"udp", "destination_port":5353, "dscp":0, "mean_packet_bytes":180, "packets_per_second":3, "burstiness":0.2, "active_seconds":300, "offered_mbps":0.1},
        ],
        "impairments":{"probe_noise_fraction":0.08,"buffer_bytes_per_class":524288,"base_rtt_ms":18,"tick_seconds":1},
    },
}

def generate_sessions(scenario: str, seed: int) -> list[dict[str, Any]]:
    if scenario not in SCENARIO_CONFIG:
        raise ValueError("Unknown experiment scenario")
    sessions=[]
    for index, spec in enumerate(SCENARIO_CONFIG[scenario]["flows"]):
        device_id = FLOW_DEVICES.get(spec["name"], "work-laptop")
        device = next(item for item in DEVICE_PROFILES if item["id"] == device_id)
        metadata={k:spec[k] for k in ("protocol","destination_port","dscp","mean_packet_bytes","packets_per_second","burstiness","active_seconds")}
        metadata["observed_mbps"] = spec["offered_mbps"]
        metadata["source_port"] = 40000 + index
        classified=classify_flow(metadata)
        sessions.append({"id":f"f{index+1}","name":spec["name"],"device_id":device_id,"device_name":device["name"],"device_room":device["room"],"direction":"upload" if spec["truth"]=="Cloud backup" else ("bidirectional" if spec["truth"] in {"Voice","Video call","Gaming"} else "download"),"truth_class":spec["truth"],"class":classified["class"],"automatic_class":classified["class"],
            "qos_class":classified["class"],"protocol":spec["protocol"].upper(),"offered":float(spec["offered_mbps"]),
            "confidence":classified["confidence"],"classification_rule":classified["rule"],"metadata":metadata,
            "mean_packet_bytes":metadata["mean_packet_bytes"],"throughput":0.0,"manual_override":False})
    return sessions


def generate_devices(scenario: str) -> list[dict[str, Any]]:
    """Return the demo household inventory; values are generated, not LAN discovery."""
    if scenario not in SCENARIO_CONFIG:
        raise ValueError("Unknown experiment scenario")
    active_device_ids = {FLOW_DEVICES.get(flow["name"], "work-laptop") for flow in SCENARIO_CONFIG[scenario]["flows"]}
    return [{**device, "connected": True, "source":"demo household", "active":device["id"] in active_device_ids,"history":[],
             "download_mbps":0.0,"upload_mbps":0.0,"total_mbps":0.0,"session_count":0,"usage_mb":0.0}
            for device in DEVICE_PROFILES]
