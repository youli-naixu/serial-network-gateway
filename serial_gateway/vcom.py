"""Virtual COM backends: inspect and configure an installed signed driver.

The project does not ship a kernel driver. Port creation is delegated to an
already installed product (HW VSP3 Single, or com0com). Changes that need
administrator rights run through one Windows UAC prompt in a separate,
short-lived helper process; nothing here disables driver signing or other
Windows security features.
"""
from __future__ import annotations

import configparser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HWVSP_SERVICE = "HW_VSP3s_Service"
_COM_RE = re.compile(r"COM([1-9][0-9]{0,3})")
_SAFE_HOST = re.compile(r"(?:\d{1,3}(?:\.\d{1,3}){3}|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)")
_VIRTUAL_HWID = re.compile(r"(?:^|[\\& _])(?:COM0COM|EVSERIAL|EVSBC)(?:$|[\\& _])", re.I)


@dataclass(frozen=True)
class VcomPlan:
    """Desired virtual COM channel. Validation happens before any change."""

    com: str
    host: str = "127.0.0.1"
    port: int = 7000
    rfc2217: bool = True
    auto_create: bool = True

    def validate(self) -> None:
        if not isinstance(self.com, str) or not _COM_RE.fullmatch(self.com):
            raise ValueError("COM name must look like COM10 (COM1–COM9999).")
        if not isinstance(self.host, str) or not _SAFE_HOST.fullmatch(self.host):
            raise ValueError("Host must be an IPv4 address or a plain host name.")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("TCP port must be in the range 1..65535.")


@dataclass
class BackendStatus:
    name: str
    installed: bool
    running: bool = False
    location: str = ""
    channel: dict[str, str] = field(default_factory=dict)
    ports: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "installed": self.installed, "running": self.running,
                "location": self.location, "channel": dict(self.channel),
                "ports": list(self.ports), "notes": list(self.notes)}


def system_com_ports() -> dict[str, dict[str, str]]:
    """Map COM name -> metadata, without opening any port."""
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return {}
    ports = {}
    for entry in comports():
        name = (entry.device or "").upper()
        if _COM_RE.fullmatch(name):
            ports[name] = {"description": entry.description or "", "hwid": entry.hwid or "",
                           "virtual": bool(_VIRTUAL_HWID.search(entry.hwid or ""))}
    return ports


def registered_com_names() -> set[str]:
    """Names Windows has reserved (includes disconnected devices); read-only."""
    names: set[str] = set()
    if os.name != "nt":
        return names
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                value = str(winreg.EnumValue(key, index)[1]).upper()
                if _COM_RE.fullmatch(value):
                    names.add(value)
    except OSError:
        pass
    return names


def suggest_free_com(start: int = 10) -> str:
    used = set(system_com_ports()) | registered_com_names()
    for number in range(max(1, start), 256):
        name = f"COM{number}"
        if name not in used:
            return name
    raise RuntimeError("No free COM name found below COM256.")


def _service_running(name: str) -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(["sc.exe", "query", name], capture_output=True, text=True,
                            errors="replace", timeout=10, shell=False)
    return result.returncode == 0 and "RUNNING" in result.stdout


class HwVspBackend:
    """HW VSP3 Single (vendor-signed driver installed separately by the user)."""

    name = "HW VSP3 Single"

    def __init__(self, install_dir: str | Path | None = None) -> None:
        candidates = [Path(install_dir)] if install_dir else [
            Path(os.environ.get(var, "")) / "HW group" / "HW VSP3s"
            for var in ("ProgramFiles(x86)", "ProgramFiles") if os.environ.get(var)
        ]
        self.directory = next((p for p in candidates if (p / "HW_VSP3s_srv.exe").is_file()), None)

    @property
    def ini_path(self) -> Path | None:
        return self.directory / "HW_VSP3s.ini" if self.directory else None

    def read_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        if self.ini_path and self.ini_path.is_file():
            parser.read_string(self.ini_path.read_text(encoding="utf-8", errors="replace"))
        return parser

    def status(self) -> BackendStatus:
        if not self.directory:
            return BackendStatus(self.name, installed=False,
                                 notes=["Not installed. Download HW VSP3 Single from the vendor and install it with administrator rights."])
        config = self.read_config()
        channel = {}
        if config.has_section("CHANNEL0"):
            for key in ("COM", "IP", "NvtPortSetup", "NvtFilter", "CreateAutomatically", "AutoRenew", "ConnectIfPortClosed", "PurgeBuffers"):
                if config.has_option("CHANNEL0", key):
                    channel[key] = config.get("CHANNEL0", key)
        ports = system_com_ports()
        present = [name for name, meta in ports.items() if meta["virtual"]]
        notes = []
        com = channel.get("COM", "").upper()
        if com and com in ports and not ports[com]["virtual"]:
            notes.append(f"Configured {com} collides with a physical port; choose another name.")
        if channel.get("NAME") == "Demo" and channel.get("IP", "").endswith(":23"):
            notes.append("Vendor sample channel is still configured.")
        return BackendStatus(self.name, installed=True, running=_service_running(HWVSP_SERVICE),
                             location=str(self.directory), channel=channel, ports=present, notes=notes)

    @staticmethod
    def channel_values(plan: VcomPlan) -> dict[str, str]:
        """CHANNEL0 keys verified in the vendor's 3.1.2 template."""
        plan.validate()
        return {
            "NAME": "SerialNetworkGateway", "COM": plan.com,
            "CreateAutomatically": "1" if plan.auto_create else "0",
            "LogEnabled": "0", "ConnectIfPortClosed": "0", "ExtControlEnabled": "0",
            "ExtControlPort": "2003", "StrictBaudrate": "0", "PurgeBuffers": "1",
            "IP": f"{plan.host}:{plan.port}",
            "NvtFilter": "1" if plan.rfc2217 else "0", "NvtPortSetup": "1" if plan.rfc2217 else "0",
            "KeepConnection": "0", "NopToKeep": "0", "AutoRenew": "0", "Server": "0",
            "InactiveEnable": "0", "InactiveTimeout": "10",
        }

    def preview(self, plan: VcomPlan) -> str:
        """Channel section only; the vendor GENERAL section (password) is never copied."""
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["CHANNEL0"] = self.channel_values(plan)
        output = io.StringIO()
        parser.write(output, space_around_delimiters=False)
        return output.getvalue()

    def apply(self, plan: VcomPlan, timeout_s: float = 60.0) -> dict[str, Any]:
        """Replace CHANNEL0 through one elevated helper, then wait for the port."""
        if os.name != "nt" or not self.directory:
            raise RuntimeError("HW VSP3 Single is not installed on this Windows system.")
        plan.validate()
        ports = system_com_ports()
        if plan.com in ports and not ports[plan.com]["virtual"]:
            raise ValueError(f"{plan.com} is already used by a physical device.")
        job = {"backend": "hwvsp", "ini": str(self.ini_path), "service": HWVSP_SERVICE,
               "channel": self.channel_values(plan)}
        result = run_elevated(job, timeout_s=timeout_s)
        result["port_present"] = wait_for_port(plan.com, timeout_s=20)
        return result


class Com0comBackend:
    """com0com pair; requires setupc.exe from an installed signed build."""

    name = "com0com"

    def __init__(self, setupc: str | Path | None = None) -> None:
        candidates = [Path(setupc)] if setupc else [
            Path(os.environ.get(var, "")) / "com0com" / "setupc.exe"
            for var in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(var)
        ]
        found = shutil.which("setupc.exe")
        if found:
            candidates.append(Path(found))
        self.setupc = next((p for p in candidates if p.is_file()), None)

    def status(self) -> BackendStatus:
        if not self.setupc:
            return BackendStatus(self.name, installed=False, notes=["setupc.exe not found."])
        pairs = self.pairs()
        notes = [] if pairs else ["No port pair yet. Create one to use full control-line and baud forwarding."]
        for pair in pairs:
            if not pair["close_detect"]:
                notes.append(f"Pair {pair['pair']} cannot report when the application closes its port; enable close detection.")
        return BackendStatus(self.name, installed=True, running=bool(pairs), location=str(self.setupc.parent),
                             channel={p["endpoint"]: p["com"] for p in pairs},
                             ports=[p["com"] for p in pairs if p["com"]], notes=notes)

    def pairs(self) -> list[dict[str, Any]]:
        """Pairs from the device tree: visible COM name and hidden CNCBn endpoint. Read-only."""
        if os.name != "nt":
            return []
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
             "Where-Object { $_.InstanceId -like 'COM0COM\\PORT\\CNC*' } | "
             "ForEach-Object { $_.InstanceId + '|' + $_.FriendlyName }"],
            capture_output=True, text=True, errors="replace", timeout=20, shell=False)
        ends: dict[int, dict[str, str]] = {}
        for line in result.stdout.splitlines():
            match = re.match(r"COM0COM\\PORT\\CNC([AB])(\d+)\|.*?\((COM\d+|CNC[AB]\d+)\)", line.strip(), re.I)
            if match:
                side, number, name = match.group(1).upper(), int(match.group(2)), match.group(3).upper()
                ends.setdefault(number, {})[side] = name
        from .c0c_port import ri_wired_to_open
        pairs = []
        for number, sides in sorted(ends.items()):
            app_name = sides.get("A", "")
            endpoint = f"CNCB{number}"
            pairs.append({"pair": number, "com": app_name if _COM_RE.fullmatch(app_name) else "",
                          "endpoint": endpoint, "close_detect": ri_wired_to_open(endpoint)})
        return pairs

    def create_pair(self, com: str, timeout_s: float = 120.0) -> dict[str, Any]:
        if not self.setupc:
            raise RuntimeError("com0com is not installed.")
        VcomPlan(com).validate()
        if com in system_com_ports() or com in registered_com_names():
            raise ValueError(f"{com} is already in use.")
        result = run_elevated({"backend": "com0com", "action": "install", "setupc": str(self.setupc), "com": com},
                              timeout_s=timeout_s)
        result["port_present"] = wait_for_port(com, timeout_s=20)
        return result

    def wire_open_signal(self, number: int, timeout_s: float = 120.0) -> dict[str, Any]:
        """Configure an existing pair so the bridge can see the application closing its port."""
        if not self.setupc:
            raise RuntimeError("com0com is not installed.")
        if not isinstance(number, int) or not any(p["pair"] == number for p in self.pairs()):
            raise ValueError("Unknown pair number.")
        return run_elevated({"backend": "com0com", "action": "wire-open", "setupc": str(self.setupc), "pair": number},
                            timeout_s=timeout_s)

    def remove_pair(self, number: int, timeout_s: float = 120.0) -> dict[str, Any]:
        if not self.setupc:
            raise RuntimeError("com0com is not installed.")
        if not isinstance(number, int) or not any(p["pair"] == number for p in self.pairs()):
            raise ValueError("Unknown pair number.")
        return run_elevated({"backend": "com0com", "action": "remove", "setupc": str(self.setupc), "pair": number},
                            timeout_s=timeout_s)


def detect_backends() -> list[BackendStatus]:
    return [HwVspBackend().status(), Com0comBackend().status()]


def capability_matrix() -> list[dict[str, str]]:
    """Measured on Windows 11 23H2 with HW VSP3 Single 3.1.2 and signed com0com 3.0.0.0."""
    return [
        {"feature": "binary data", "hwvsp": "yes", "com0com": "yes"},
        {"feature": "settings at open", "hwvsp": "yes", "com0com": "yes"},
        {"feature": "baud change after open", "hwvsp": "<=57600 only", "com0com": "yes (incl. 1.5M)"},
        {"feature": "data bits / parity / stop change", "hwvsp": "only with a baud change", "com0com": "yes"},
        {"feature": "DTR / RTS", "hwvsp": "no", "com0com": "yes"},
        {"feature": "BREAK", "hwvsp": "no", "com0com": "yes"},
        {"feature": "PURGE", "hwvsp": "no", "com0com": "no"},
        {"feature": "statistics and session lock", "hwvsp": "via local gateway", "com0com": "built in"},
    ]


def wait_for_port(com: str, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        meta = system_com_ports().get(com)
        if meta and meta["virtual"]:
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- elevation

def run_elevated(job: dict[str, Any], timeout_s: float = 60.0) -> dict[str, Any]:
    """Run ``python -m serial_gateway.vcom --apply-job`` once via UAC.

    The job file lives in a private temporary directory and is deleted after
    use. The helper only accepts the fixed job schema validated below.
    """
    if os.name != "nt":
        raise RuntimeError("Elevation is only supported on Windows.")
    import ctypes
    workdir = Path(tempfile.mkdtemp(prefix="sng-vcom-"))
    job_path, result_path = workdir / "job.json", workdir / "result.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    if getattr(sys, "frozen", False):
        executable, params = sys.executable, ["--vcom-apply-job", str(job_path), str(result_path)]
    else:
        executable = sys.executable
        params = ["-m", "serial_gateway.vcom", "--apply-job", str(job_path), str(result_path)]
    try:
        handle = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable,
                                                    subprocess.list2cmdline(params), None, 0)
        if handle <= 32:
            raise PermissionError("Administrator approval was cancelled or failed.")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if result_path.exists():
                try:
                    return json.loads(result_path.read_text(encoding="utf-8"))
                except (PermissionError, json.JSONDecodeError):
                    pass  # helper may still hold or be replacing the file
            time.sleep(0.3)
        raise TimeoutError("The elevated helper did not finish in time.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class _Done(Exception):
    """Internal: early successful exit from the elevated job."""


def _apply_job(job_path: str, result_path: str) -> int:
    """Elevated side. Accept only a fixed schema; never run arbitrary commands."""
    result: dict[str, Any] = {"ok": False}
    try:
        job = json.loads(Path(job_path).read_text(encoding="utf-8"))
        if job.get("backend") == "com0com":
            setupc = Path(job["setupc"]).resolve()
            if setupc.name.lower() != "setupc.exe" or not (setupc.parent / "com0com.inf").is_file():
                raise ValueError("Unexpected setupc path.")
            action = job.get("action")
            if action == "install":
                com = str(job["com"])
                VcomPlan(com).validate()
                # Hidden end: RI reports "application port open" for clean session ends.
                argv = [str(setupc), "install", f"PortName={com},EmuBR=no", "ri=ropen"]
            elif action == "wire-open":
                number = int(job["pair"])
                if not 0 <= number <= 255:
                    raise ValueError("Unexpected pair number.")
                argv = [str(setupc), "change", f"CNCB{number}", "ri=ropen"]
            elif action == "remove":
                number = int(job["pair"])
                if not 0 <= number <= 255:
                    raise ValueError("Unexpected pair number.")
                argv = [str(setupc), "remove", str(number)]
            else:
                raise ValueError("Unsupported com0com action.")
            done = subprocess.run(argv, cwd=str(setupc.parent), capture_output=True, text=True,
                                  errors="replace", timeout=120, shell=False)
            result.update(ok=done.returncode == 0, action=action)
            raise _Done
        if job.get("backend") != "hwvsp" or job.get("service") != HWVSP_SERVICE:
            raise ValueError("Unsupported job.")
        ini = Path(job["ini"]).resolve()
        if ini.name != "HW_VSP3s.ini" or not (ini.parent / "HW_VSP3s_srv.exe").is_file():
            raise ValueError("Unexpected configuration path.")
        channel = job.get("channel")
        allowed = set(HwVspBackend.channel_values(VcomPlan("COM10")))
        if not isinstance(channel, dict) or set(channel) != allowed:
            raise ValueError("Unexpected channel fields.")
        plan = VcomPlan(com=str(channel["COM"]), host=str(channel["IP"]).rsplit(":", 1)[0],
                        port=int(str(channel["IP"]).rsplit(":", 1)[1]))
        plan.validate()
        if any(not re.fullmatch(r"[A-Za-z0-9:._-]{0,64}", str(v)) for v in channel.values()):
            raise ValueError("Unexpected channel value.")
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read_string(ini.read_text(encoding="utf-8", errors="replace"))
        if parser.has_section("CHANNEL0"):
            parser.remove_section("CHANNEL0")
        parser["CHANNEL0"] = {k: str(v) for k, v in channel.items()}
        buffer = io.StringIO()
        parser.write(buffer, space_around_delimiters=False)
        content = buffer.getvalue()
        backup = ini.with_name(f"HW_VSP3s.ini.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(ini, backup)
        subprocess.run(["sc.exe", "stop", HWVSP_SERVICE], capture_output=True, timeout=20, shell=False)
        for _ in range(40):
            if not _service_running(HWVSP_SERVICE):
                break
            time.sleep(0.25)
        temp = ini.with_suffix(".ini.new")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, ini)
        started = subprocess.run(["sc.exe", "start", HWVSP_SERVICE], capture_output=True, text=True,
                                 errors="replace", timeout=20, shell=False)
        result.update(ok=started.returncode == 0, backup=backup.name,
                      service_started=started.returncode == 0)
    except _Done:
        pass
    except Exception as exc:  # report class only; content may contain local paths
        result["error"] = type(exc).__name__
    temporary = Path(result_path).with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    os.replace(temporary, result_path)
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] in ("--apply-job", "--vcom-apply-job"):
        return _apply_job(args[1], args[2])
    print(json.dumps([backend.to_dict() for backend in detect_backends()], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
