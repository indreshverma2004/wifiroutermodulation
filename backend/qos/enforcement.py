"""Opt-in Linux tc adapter. Simulation mode never invokes this adapter."""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from typing import Any

from .engine import CLASSES, DEFAULT_POLICIES


class LinuxTcAdapter:
    """Install an isolated HTB tree only on an interface without an existing root qdisc.

    We intentionally refuse to replace an existing qdisc: silently deleting another
    controller's configuration is difficult to roll back safely.
    """

    DSCP_TO_CLASS = {46: "Voice", 34: "Video call", 26: "Gaming", 8: "Streaming",
                     10: "Downloads", 12: "Cloud backup"}

    def __init__(self):
        self.interface: str | None = None
        self.applied = False
        self.commands: list[list[str]] = []
        self.capacity_mbps: float | None = None

    def status(self) -> dict[str, Any]:
        return {"supported": platform.system() == "Linux" and shutil.which("tc") is not None,
                "enabled": self.applied, "interface": self.interface,
                "capacity_mbps": self.capacity_mbps,
                "mode": "real interface" if self.applied else "simulation"}

    @staticmethod
    def _run(command: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)

    def _existing_root(self, interface: str) -> list[dict[str, Any]]:
        try:
            result = self._run(["tc", "-j", "qdisc", "show", "dev", interface])
            qdiscs = json.loads(result.stdout or "[]")
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not inspect existing qdiscs; no changes applied: {error}") from error
        return [q for q in qdiscs if q.get("root") and q.get("kind") != "noqueue"]

    def _delete_owned_root(self, interface: str) -> None:
        roots = self._existing_root(interface)
        if roots and not any(q.get("kind") == "htb" and q.get("handle") == "1:" for q in roots):
            raise ValueError("The interface root qdisc changed after QoS was applied; refusing to delete an unrecognized qdisc")
        if roots:
            self._run(["tc", "qdisc", "del", "dev", interface, "root"])

    @staticmethod
    def _commands_for(interface: str, capacity_mbps: float, policies: dict[str, dict]) -> list[list[str]]:
        rate = f"{capacity_mbps:g}mbit"
        unknown_minor = CLASSES.index("Unknown") + 10
        commands = [["tc", "qdisc", "add", "dev", interface, "root", "handle", "1:", "htb", "default", str(unknown_minor)],
                    ["tc", "class", "add", "dev", interface, "parent", "1:", "classid", "1:1", "htb", "rate", rate, "ceil", rate]]
        minima = {name: max(0.0, float(policies[name]["minimum"])) for name in CLASSES}
        min_total = sum(minima.values())
        if min_total > capacity_mbps:
            minima = {name: value * capacity_mbps / min_total for name, value in minima.items()}
        for index, name in enumerate(CLASSES, start=10):
            class_id = f"1:{index}"
            guarantee = max(0.1, minima[name])
            ceiling = max(guarantee, min(float(policies[name]["limit"]), capacity_mbps))
            commands.append(["tc", "class", "add", "dev", interface, "parent", "1:1", "classid", class_id,
                             "htb", "rate", f"{guarantee:g}mbit", "ceil", f"{ceiling:g}mbit",
                             "prio", str(int(policies[name]["priority"]))])
            commands.append(["tc", "qdisc", "add", "dev", interface, "parent", class_id,
                             "handle", f"{index}0:", "fq_codel"])
        # Match only explicitly marked DSCP values. Unmarked and unknown traffic uses
        # the default Unknown class rather than being guessed from ports or payload.
        class_ids = {name: f"1:{index}" for index, name in enumerate(CLASSES, start=10)}
        for dscp, name in LinuxTcAdapter.DSCP_TO_CLASS.items():
            for protocol in ("ip", "ipv6"):
                commands.append(["tc", "filter", "add", "dev", interface, "protocol", protocol,
                                 "parent", "1:", "prio", str(dscp), "flower", "ip_tos",
                                 f"0x{dscp << 2:02x}/0xfc", "classid", class_ids[name]])
        return commands

    def apply(self, interface: str, confirmed: bool, capacity_mbps: float = 100.0,
              policies: dict[str, dict] | None = None) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Explicit confirmation is required before changing a network interface")
        if platform.system() != "Linux" or shutil.which("tc") is None:
            raise ValueError("Linux tc is unavailable; simulation mode remains active")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
            raise ValueError("Invalid Linux interface name")
        if not 1 <= float(capacity_mbps) <= 1000:
            raise ValueError("Capacity must be between 1 and 1000 Mbps")
        policies = policies or DEFAULT_POLICIES
        if set(policies) != set(CLASSES):
            raise ValueError("A policy is required for every traffic class")
        if self.applied:
            raise ValueError("Roll back the currently managed interface before applying another policy")
        roots = self._existing_root(interface)
        if roots:
            kind = roots[0].get("kind", "unknown")
            raise ValueError(f"Interface {interface} already has root qdisc '{kind}'. No changes applied; use a dedicated lab interface or remove its qdisc yourself first.")

        commands = self._commands_for(interface, float(capacity_mbps), policies)
        created = False
        try:
            for command in commands:
                self._run(command)
                if command[1:3] == ["qdisc", "add"] and "root" in command:
                    created = True
        except (subprocess.SubprocessError, OSError) as error:
            if created:
                try:
                    self._delete_owned_root(interface)
                except (subprocess.SubprocessError, OSError, ValueError):
                    pass
            raise ValueError(f"tc configuration failed; cleanup of the newly created qdisc was attempted: {error}") from error
        self.commands, self.interface, self.applied = commands, interface, True
        self.capacity_mbps = float(capacity_mbps)
        return self.status()

    def rollback(self) -> dict[str, Any]:
        if self.applied and self.interface:
            try:
                self._delete_owned_root(self.interface)
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                raise ValueError(f"Could not remove the QoS qdisc; it may need manual inspection: {error}") from error
        self.applied, self.interface, self.commands, self.capacity_mbps = False, None, [], None
        return self.status()
