"""One-click wireless flashing station: labelled virtual COM -> ESP32 RFC2217.

The station keeps one com0com pair whose application end sits in the Windows
Ports class under a recognisable name (default ``ASR 无线烧录 (COMn)``), so
ordinary COM pickers such as TianwenBlock list it. While running it waits for
a flashing tool to open that COM, asks the ESP32 to put the target into its
bootloader (HTTP POST), connects to the ESP32's RFC2217 port and relays the
session. After the tool closes the port it re-arms for the next download.

Settings live next to the profiles in ``station.json``. No token is stored;
when the ESP32 needs one, it is read from an environment variable at run time.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

DEFAULT_LABEL = "ASR 无线烧录"
_HOST_RE = re.compile(r"(?:\d{1,3}(?:\.\d{1,3}){3}|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)")
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


@dataclass
class StationConfig:
    label: str = DEFAULT_LABEL
    host: str = ""
    port: int = 3333
    # Empty: POST http://<host>/api/maintenance/enter?mode=isp directly.
    enter_url: str = ""
    token_env: str = ""
    token_header: str = "X-Gateway-Token"
    auto_start: bool = True

    def validate(self) -> None:
        from .vcom import validate_label
        validate_label(self.label)
        if self.host and not _HOST_RE.fullmatch(self.host):
            raise ValueError("ESP32 host must be an IPv4 address or a plain host name.")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("ESP32 port must be in the range 1..65535.")
        if self.enter_url and not re.fullmatch(r"https?://[^\s\"'<>]{1,500}", self.enter_url):
            raise ValueError("Enter URL must be an http(s) URL without spaces.")
        if self.token_env and not _ENV_RE.fullmatch(self.token_env):
            raise ValueError("Token variable must be an environment variable NAME.")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.token_header or ""):
            raise ValueError("Token header must be a plain header name.")

    def effective_enter_url(self) -> str:
        return self.enter_url or (f"http://{self.host}/api/maintenance/enter?mode=isp" if self.host else "")


def config_path() -> Path:
    from .profiles import default_config_path
    return default_config_path().with_name("station.json")


def load_config(path: str | Path | None = None) -> StationConfig:
    target = Path(path) if path is not None else config_path()
    try:
        if target.stat().st_size > 16384:
            return StationConfig()
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        return StationConfig()
    if not isinstance(data, dict):
        return StationConfig()
    known = {k: v for k, v in data.items() if k in StationConfig.__dataclass_fields__}
    try:
        config = StationConfig(**known)
        config.validate()
    except (TypeError, ValueError):
        return StationConfig()
    return config


def save_config(config: StationConfig, path: str | Path | None = None) -> None:
    config.validate()
    target = Path(path) if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, prefix=".station-",
                                     suffix=".tmp", delete=False) as handle:
        json.dump(asdict(config), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, target)


def find_station_pair(pairs: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    """The pair already prepared for the station: visible, named ``label`` and close-detecting."""
    for pair in pairs:
        if pair.get("visible") and pair.get("label") == label and pair.get("close_detect") and pair.get("com"):
            return pair
    return None


def reusable_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """An existing pair that can be relabelled instead of installing another one."""
    return next((p for p in pairs if p.get("com")), None)


def enter_maintenance(url: str, token: str = "", header: str = "X-Gateway-Token",
                      timeout_s: float = 15.0) -> dict[str, Any]:
    """POST the enter URL and return the device's JSON reply; raises on failure."""
    request = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    if token:
        request.add_header(header, token)
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read(8192).decode("utf-8", "replace")
        status = response.status
    try:
        data = json.loads(body)
    except ValueError:
        data = {}
    if status != 200 or data.get("ok") is False:
        raise RuntimeError(f"enter request failed (HTTP {status}, {data.get('error', 'no detail')})")
    return data


def summarize_enter(reply: dict[str, Any]) -> dict[str, Any]:
    """Pick the target-preparation fields the ESP32 firmware reports."""
    m = reply.get("maintenance") if isinstance(reply.get("maintenance"), dict) else reply
    return {key: m.get(key) for key in ("lastAppAck", "lastReplyMs", "caught", "lastCatchMs") if key in m}


@dataclass
class StationStatus:
    state: str = "stopped"          # stopped | waiting_app | preparing | flashing | error
    com: str = ""
    sessions: int = 0
    last_result: str = ""
    last_prepare: dict = field(default_factory=dict)
    bridge: dict = field(default_factory=dict)
    error: str = ""
    # Newest first: {"time": "HH:MM:SS", "result", "sent", "received", "seconds", "prepare"}.
    history: list = field(default_factory=list)


HISTORY_LIMIT = 20


class FlashStation:
    """Runs com0com bridges back to back so each download gets a fresh session."""

    def __init__(self, config: StationConfig, endpoint: str, com: str, *,
                 bridge_factory: Callable[..., Any] | None = None,
                 enter: Callable[[], dict[str, Any]] | None = None) -> None:
        config.validate()
        if not config.host:
            raise ValueError("Set the ESP32 host first.")
        self.config, self.endpoint = config, endpoint
        self._status = StationStatus(com=com)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bridge: Any = None
        if bridge_factory is None:
            from .c0c_bridge import Com0comBridge
            bridge_factory = Com0comBridge
        self._factory = bridge_factory
        self._enter = enter or self._default_enter
        self._session_started: float | None = None

    def _default_enter(self) -> dict[str, Any]:
        url = self.config.effective_enter_url()
        token = os.environ.get(self.config.token_env, "") if self.config.token_env else ""
        return enter_maintenance(url, token, self.config.token_header)

    def _prepare(self) -> None:
        with self._lock:
            self._status.state = "preparing"
            self._session_started = time.monotonic()
        reply = self._enter()
        summary = summarize_enter(reply)
        with self._lock:
            self._status.last_prepare = summary

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        with self._lock:
            self._status.state = "waiting_app"
            self._status.error = ""
        self._thread = threading.Thread(target=self._run, name="asr-station", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        bridge = self._bridge
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:  # noqa: BLE001 - stopping is best effort
                pass
        if self._thread is not None:
            self._thread.join(timeout)
        with self._lock:
            self._status.state = "stopped"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = asdict(self._status)
        bridge = self._bridge
        if bridge is not None:
            snap = bridge.snapshot()
            data["bridge"] = {k: snap.get(k) for k in ("state", "app_to_device", "device_to_app", "settings",
                                                       "last_error", "message")}
            if data["state"] not in ("stopped", "error"):
                if snap.get("state") == "active":
                    data["state"] = "flashing"
                elif snap.get("state") in ("waiting_app", "needs_reset", "stopped"):
                    data["state"] = "waiting_app"
        return data

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                bridge = self._factory(self.endpoint, self.config.host, self.config.port,
                                       connect_on_app_open=True, before_connect=self._prepare,
                                       connect_retry_s=5.0)
                self._bridge = bridge
                bridge.start()
                with self._lock:
                    self._status.state = "waiting_app"
                    self._status.error = ""
                while not self._stop.is_set():
                    snap = bridge.snapshot()
                    if snap.get("state") in ("needs_reset", "stopped", "error"):
                        break
                    time.sleep(0.1)
                if self._stop.is_set():
                    break
                snap = bridge.snapshot()
                bridge.stop()
                error = snap.get("last_error") or ""
                if error and self._session_started is None:
                    # Failed before any flashing tool opened the port (e.g. the COM is held
                    # by another program): a station problem, not a download attempt.
                    with self._lock:
                        self._status.state = "error"
                        self._status.error = error
                    if self._stop.wait(3.0):
                        break
                    continue
                with self._lock:
                    self._status.sessions += 1
                    moved = int(snap.get("app_to_device") or 0)
                    if error:
                        result = f"error: {error}"
                    elif not moved:
                        result = "closed_without_data"
                    else:
                        result = "done"
                    self._status.last_result = result
                    started, self._session_started = self._session_started, None
                    self._status.history.insert(0, {
                        "time": time.strftime("%H:%M:%S"), "result": result, "sent": moved,
                        "received": int(snap.get("device_to_app") or 0),
                        "seconds": round(time.monotonic() - started, 1) if started else None,
                        "prepare": dict(self._status.last_prepare)})
                    del self._status.history[HISTORY_LIMIT:]
                time.sleep(0.5)
            except Exception as exc:  # noqa: BLE001 - surface class only
                with self._lock:
                    self._status.state = "error"
                    self._status.error = type(exc).__name__ + (f": {exc}" if str(exc) else "")
                if self._stop.wait(3.0):
                    break
        with self._lock:
            self._status.state = "stopped"
