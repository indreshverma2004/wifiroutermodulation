"""Opt-in, read-only discovery of IPv4 neighbors on this Windows Wi-Fi LAN."""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import platform
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
import psutil

_scan_lock = threading.Lock()
_last_scan_at = 0.0
_active_interface_name: str | None = None


def _powershell_json(script: str, timeout: float = 25.0):
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True, capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout.strip() or "null")


def _wifi_config() -> dict:
    script = r"""
$a = Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' -and ($_.Name -match 'Wi-?Fi|Wireless' -or $_.InterfaceDescription -match 'Wi-?Fi|Wireless|802.11') } | Select-Object -First 1
if (-not $a) { throw 'No active physical Wi-Fi adapter was found.' }
$c = Get-NetIPConfiguration -InterfaceIndex $a.ifIndex
if (-not $c.IPv4Address) { throw 'The active Wi-Fi adapter has no IPv4 address.' }
[pscustomobject]@{ interface=$a.Name; description=$a.InterfaceDescription; mac=$a.MacAddress; link_speed=[string]$a.LinkSpeed; ifIndex=$a.ifIndex; address=$c.IPv4Address.IPAddress; prefix=[int]$c.IPv4Address.PrefixLength; gateway=$c.IPv4DefaultGateway.NextHop; dns_servers=@($c.DNSServer.ServerAddresses) } | ConvertTo-Json -Compress
"""
    try:
        config = _powershell_json(script)
    except FileNotFoundError as exc:
        raise RuntimeError("Windows PowerShell is required for local Wi-Fi discovery.") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "")
        raise RuntimeError((detail or str(exc)).strip() or "Could not read the active Wi-Fi adapter.") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Could not identify the active Wi-Fi network.")
    return config


def _neighbors(interface_index: int):
    script = rf"""
$rows = @(Get-NetNeighbor -InterfaceIndex {int(interface_index)} -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {{ $_.State -in @('Reachable','Stale','Delay','Probe','Permanent') -and $_.LinkLayerAddress -match '^([0-9A-Fa-f]{{2}}-){{5}}[0-9A-Fa-f]{{2}}$' }} | Select-Object IPAddress,LinkLayerAddress,State)
$rows | ConvertTo-Json -Compress
"""
    try:
        data = _powershell_json(script)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def _wifi_radio_details() -> dict:
    """Read the current association details from Windows WLAN AutoConfig."""
    try:
        result = subprocess.run(
            ["netsh.exe", "wlan", "show", "interfaces"],
            check=True, capture_output=True, text=True, timeout=8.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}
    values = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().lower()] = value.strip()

    def number(key: str, pattern: str = r"-?\d+(?:\.\d+)?"):
        match = re.search(pattern, values.get(key, ""))
        return float(match.group()) if match else None

    return {
        "ssid": values.get("ssid"), "access_point_mac": values.get("ap bssid"),
        "band": values.get("band"), "channel": values.get("channel"),
        "radio_type": values.get("radio type"), "authentication": values.get("authentication"),
        "cipher": values.get("cipher"), "signal_percent": number("signal"),
        "signal_dbm": number("rssi"), "receive_link_mbps": number("receive rate (mbps)"),
        "transmit_link_mbps": number("transmit rate (mbps)"),
        "connection_state": values.get("state"),
    }


def wifi_adapter_traffic() -> dict:
    """Return cumulative counters for this PC's Wi-Fi adapter, not other clients."""
    global _active_interface_name
    if not _active_interface_name:
        config = _wifi_config()
        _active_interface_name = str(config["interface"])
    counters = psutil.net_io_counters(pernic=True).get(_active_interface_name)
    if counters is None:
        raise RuntimeError(f"No traffic counters are available for Wi-Fi interface {_active_interface_name}.")
    return {
        "interface": _active_interface_name,
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "received_bytes": counters.bytes_recv, "sent_bytes": counters.bytes_sent,
        "received_packets": counters.packets_recv, "sent_packets": counters.packets_sent,
        "receive_errors": counters.errin, "send_errors": counters.errout,
        "receive_drops": counters.dropin, "send_drops": counters.dropout,
    }


def _ping(address: str):
    try:
        subprocess.run(
            ["ping.exe", "-n", "1", "-w", "300", address],
            capture_output=True, timeout=2.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def scan_local_wifi() -> dict:
    """Probe only the current Wi-Fi IPv4 subnet and return actual ARP neighbors."""
    global _last_scan_at, _active_interface_name
    if platform.system() != "Windows":
        raise RuntimeError("Local Wi-Fi discovery currently requires the Windows local agent.")
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("A Wi-Fi scan is already running.")
    try:
        now = time.monotonic()
        if now - _last_scan_at < 8:
            raise RuntimeError("Please wait a few seconds before scanning again.")
        _last_scan_at = now
        config = _wifi_config()
        _active_interface_name = str(config["interface"])
        address = ipaddress.IPv4Address(config["address"])
        network = ipaddress.IPv4Network(f"{address}/{int(config['prefix'])}", strict=False)
        if not address.is_private or network.num_addresses > 1024:
            raise RuntimeError("The detected Wi-Fi subnet is outside the supported private /22-or-smaller scan range.")

        targets = [str(host) for host in network.hosts() if host != address]
        with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
            list(pool.map(_ping, targets))
        raw = _neighbors(int(config["ifIndex"]))
        seen: dict[str, dict] = {}
        for neighbor in raw:
            try:
                ip = ipaddress.IPv4Address(neighbor["IPAddress"])
            except (KeyError, ipaddress.AddressValueError):
                continue
            if ip not in network or ip == address or not ip.is_private:
                continue
            mac = str(neighbor.get("LinkLayerAddress", "")).upper()
            if not mac or mac == "FF-FF-FF-FF-FF-FF":
                continue
            is_gateway = str(ip) == str(config.get("gateway", ""))
            seen[str(ip)] = {
                "id": f"lan-{str(ip).replace('.', '-').replace(':', '-')}",
                "name": "Wi-Fi router" if is_gateway else "LAN client",
                "type": "Router" if is_gateway else "Network device",
                "ip": str(ip), "mac": mac,
                "connected": neighbor.get("State") == "Reachable", "active": neighbor.get("State") == "Reachable",
                "state": neighbor.get("State", "Unknown"),
                "source": "local Wi-Fi neighbor scan",
                "download_mbps": None, "upload_mbps": None, "total_mbps": None,
                "usage_mb": None, "signal_dbm": None, "wifi_band": "Wi-Fi (band unavailable)",
            }
        devices = sorted(seen.values(), key=lambda item: (not item["active"], ipaddress.IPv4Address(item["ip"])))
        return {
            "source": "local Wi-Fi neighbor scan",
            "simulated": False,
            "interface": config["interface"],
            "local_ip": str(address),
            "subnet": str(network),
            "gateway": config.get("gateway"),
            "adapter_description": config.get("description"),
            "adapter_mac": config.get("mac"), "adapter_link_speed": config.get("link_speed"),
            "dns_servers": config.get("dns_servers", []),
            "wifi": _wifi_radio_details(),
            "adapter_counters": wifi_adapter_traffic(),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "device_count": len(devices),
            "devices": devices,
            "bandwidth_available": False,
            "bandwidth_note": "Per-device bandwidth requires router traffic statistics; this scan reports only real IP/MAC neighbors.",
        }
    finally:
        _scan_lock.release()


if __name__ == "__main__":
    result = scan_local_wifi()
    summary = {key: result[key] for key in ("interface", "local_ip", "subnet", "gateway", "device_count", "bandwidth_available", "bandwidth_note")}
    summary["wifi"] = {key: result["wifi"].get(key) for key in ("band", "channel", "radio_type", "signal_percent", "signal_dbm", "receive_link_mbps", "transmit_link_mbps")}
    summary["adapter"] = {key: result[key] for key in ("adapter_description", "adapter_mac", "adapter_link_speed")}
    summary["adapter_counters"] = {key: result["adapter_counters"][key] for key in ("received_bytes", "sent_bytes", "received_packets", "sent_packets", "receive_errors", "send_errors")}
    print(json.dumps(summary, indent=2))
