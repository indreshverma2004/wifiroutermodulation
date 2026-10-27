"""Opt-in Linux tc adapter. It is never invoked by simulation mode."""
import platform
import re
import shutil
import subprocess


class LinuxTcAdapter:
    def __init__(self):
        self.interface = None
        self.applied = False
        self.commands = []

    def status(self):
        return {"supported": platform.system() == "Linux" and shutil.which("tc") is not None,
                "enabled": self.applied, "interface": self.interface, "mode": "real interface" if self.applied else "simulation"}

    def apply(self, interface: str, confirmed: bool):
        if not confirmed:
            raise ValueError("Explicit confirmation is required before changing a network interface")
        if platform.system() != "Linux" or shutil.which("tc") is None:
            raise ValueError("Linux tc is unavailable; simulation mode remains active")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
            raise ValueError("Invalid Linux interface name")
        # HTB root with fq_codel child; all args are constants or the validated name.
        commands = [["tc", "qdisc", "replace", "dev", interface, "root", "handle", "1:", "htb", "default", "1"],
                    ["tc", "class", "replace", "dev", interface, "parent", "1:", "classid", "1:1", "htb", "rate", "100mbit", "ceil", "100mbit"],
                    ["tc", "qdisc", "replace", "dev", interface, "parent", "1:1", "handle", "10:", "fq_codel"]]
        completed = []
        try:
            for command in commands:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)
                completed.append(command)
        except (subprocess.SubprocessError, OSError) as e:
            subprocess.run(["tc", "qdisc", "del", "dev", interface, "root"], capture_output=True, timeout=5)
            raise ValueError(f"tc configuration failed and cleanup was attempted: {e}") from e
        self.commands, self.interface, self.applied = commands, interface, True
        return self.status()

    def rollback(self):
        if self.applied:
            subprocess.run(["tc", "qdisc", "del", "dev", self.interface, "root"], capture_output=True, timeout=5)
        self.applied, self.interface, self.commands = False, None, []
        return self.status()
