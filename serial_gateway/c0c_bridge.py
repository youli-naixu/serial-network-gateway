"""Bridge a com0com pair to an RFC2217 endpoint.

Data path::

    application -> COMx | com0com | CNCBn -> this bridge -> TCP/RFC2217 -> device

Unlike a redirector that only forwards bytes, the bridge watches the
application's settings through the driver's in-band records and translates
each change into an RFC2217 request:

* baud rate / data bits / parity / stop bits, including changes made after
  the port is opened;
* DTR and RTS (seen on CNCBn as DSR and CTS with the default pinout).

The bridge is client-quiet: it never sends unsolicited Telnet option
negotiation, only COM-PORT-OPTION requests. It answers server negotiation so
standard RFC2217 servers keep working, and strips all Telnet framing from
data delivered to the application.

A session ends on EOF or error from either side; queued bytes are discarded
and are never replayed into a new session.
"""
from __future__ import annotations

import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .c0c_port import LineSettings, PairEnd
from .rfc2217 import (BINARY, COM_PORT_OPTION, DO, DONT, IAC, SGA, WILL, WONT,
                      TelnetParser, escape_data, subnegotiation)

LSR_BREAK = 0x10
_PARITY_CODE = {"N": 1, "O": 2, "E": 3, "M": 4, "S": 5}
_STOP_CODE = {1: 1, 2: 2, 1.5: 3}
_ACCEPTED_OPTIONS = (BINARY, SGA, COM_PORT_OPTION)


@dataclass
class BridgeStats:
    state: str = "stopped"
    message: str = ""
    endpoint: str = ""
    remote: str = ""
    session_id: int = 0
    app_to_device: int = 0
    device_to_app: int = 0
    settings: dict = field(default_factory=dict)
    lines: dict = field(default_factory=dict)
    acknowledged: dict = field(default_factory=dict)
    requests_sent: int = 0
    events: deque = field(default_factory=lambda: deque(maxlen=100))
    last_error: str = ""


class Com0comBridge:
    """Thread-based bridge. start() returns quickly; stop() joins all threads."""

    def __init__(self, endpoint: str, host: str, port: int, *, connect_timeout_s: float = 5.0,
                 keepalive_s: int = 10, poll_s: float = 0.02, end_on_app_close: bool = True,
                 connect_on_app_open: bool = False,
                 before_connect: Callable[[], None] | None = None,
                 connect_retry_s: float = 0.0) -> None:
        """``connect_on_app_open`` delays the TCP connection until the application
        opens the COM port (needs close detection, ``ri=ropen``); bytes it writes
        meanwhile wait in the com0com buffer. ``before_connect`` runs right before
        connecting, e.g. to put the device into maintenance mode; an exception
        ends the session. ``connect_retry_s`` keeps retrying a refused connection
        for that long, for devices that open their listener on demand."""
        self.end_on_app_close = end_on_app_close
        self.connect_on_app_open = connect_on_app_open
        self.before_connect = before_connect
        self.connect_retry_s = connect_retry_s
        if not endpoint.startswith("CNCB") or not endpoint[4:].isdigit():
            raise ValueError("Endpoint must be the hidden CNCBn end of a com0com pair.")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("TCP port must be in the range 1..65535.")
        self.endpoint, self.host, self.port = endpoint, host, port
        self.connect_timeout_s, self.keepalive_s, self.poll_s = connect_timeout_s, keepalive_s, poll_s
        self._lock = threading.RLock()
        self._stats = BridgeStats(endpoint=endpoint, remote=f"{host}:{port}")
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._pair: PairEnd | None = None
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._graceful = False

    # ------------------------------------------------------------ status
    def _event(self, level: str, message: str) -> None:
        with self._lock:
            self._stats.events.append({"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                                       "level": level, "message": message})
            self._stats.message = message

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            s = self._stats
            return {"state": s.state, "message": s.message, "endpoint": s.endpoint, "remote": s.remote,
                    "session_id": s.session_id, "app_to_device": s.app_to_device,
                    "device_to_app": s.device_to_app, "settings": dict(s.settings), "lines": dict(s.lines),
                    "acknowledged": dict(s.acknowledged), "requests_sent": s.requests_sent,
                    "last_error": s.last_error, "events": list(s.events)}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._threads:
            raise RuntimeError("Bridge already running; stop it first.")
        self._stop.clear()
        self._graceful = False
        with self._lock:
            self._stats.state = "starting"
            self._stats.session_id += 1
            self._stats.last_error = ""
        thread = threading.Thread(target=self._run, name="c0c-bridge", daemon=True)
        self._threads = [thread]
        thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._close_io()
        for thread in self._threads:
            thread.join(timeout)
        alive = [t for t in self._threads if t.is_alive()]
        self._threads = alive
        with self._lock:
            self._stats.state = "stopped" if not alive else "error"
        if alive:
            raise TimeoutError("Bridge threads did not stop in time.")

    def _close_io(self, graceful: bool = False) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                if graceful:
                    # Normal end: FIN instead of RST, so the device sees a clean close.
                    sock.shutdown(socket.SHUT_WR)
                else:
                    # Abort: discard anything still queued so it can never reach a target.
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            except OSError:
                pass
            sock.close()
        pair, self._pair = self._pair, None
        if pair is not None:
            pair.close()

    # ------------------------------------------------------------ session
    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise ConnectionError("not connected")
        with self._send_lock:
            sock.sendall(data)

    def _request(self, command: int, value: bytes) -> None:
        self._send(subnegotiation(command, value))
        with self._lock:
            self._stats.requests_sent += 1

    def _apply_settings(self, settings: LineSettings) -> None:
        with self._lock:
            current = self._stats.settings
            if settings.baudrate is not None and current.get("baudrate") != settings.baudrate:
                current["baudrate"] = settings.baudrate
                self._request(1, settings.baudrate.to_bytes(4, "big"))
            if settings.bytesize is not None and current.get("bytesize") != settings.bytesize:
                current["bytesize"] = settings.bytesize
                self._request(2, bytes((settings.bytesize,)))
            if settings.parity is not None and current.get("parity") != settings.parity:
                current["parity"] = settings.parity
                self._request(3, bytes((_PARITY_CODE[settings.parity],)))
            if settings.stopbits is not None and current.get("stopbits") != settings.stopbits:
                current["stopbits"] = settings.stopbits
                self._request(4, bytes((_STOP_CODE.get(settings.stopbits, 1),)))

    def _apply_break(self, on: bool) -> None:
        with self._lock:
            lines = self._stats.lines
            if lines.get("break", False) != on:
                lines["break"] = on
                self._request(5, bytes((5 if on else 6,)))

    def _apply_lines(self, dtr: bool, rts: bool) -> None:
        with self._lock:
            lines = self._stats.lines
            if lines.get("dtr") != dtr:
                lines["dtr"] = dtr
                self._request(5, bytes((8 if dtr else 9,)))
            if lines.get("rts") != rts:
                lines["rts"] = rts
                self._request(5, bytes((11 if rts else 12,)))

    def _run(self) -> None:
        try:
            self._pair = PairEnd(self.endpoint)
            if self.connect_on_app_open:
                if not self._pair.open_wired:
                    raise RuntimeError("connect_on_app_open needs close detection (ri=ropen) on the pair")
                with self._lock:
                    self._stats.state = "waiting_app"
                self._event("info", f"Opened {self.endpoint}; waiting for the application to open the COM port.")
                while not self._stop.is_set() and not self._pair.app_open():
                    time.sleep(0.01)
                if self._stop.is_set():
                    return
            with self._lock:
                self._stats.state = "connecting"
            if self.before_connect is not None:
                self.before_connect()
            self._event("info", f"Connecting to {self.host}:{self.port}.")
            deadline = time.monotonic() + self.connect_retry_s
            while True:
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout_s)
                    break
                except ConnectionRefusedError:
                    if self._stop.is_set() or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                try:
                    sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, self.keepalive_s * 1000, self.keepalive_s * 1000))
                except OSError:
                    pass
            sock.settimeout(None)
            self._sock = sock
            with self._lock:
                self._stats.state = "active"
            self._event("info", "Connected. Forwarding application settings, DTR/RTS and data.")
            for kind, value in self._pair.initial_events:
                if kind == "settings":
                    self._apply_settings(value)
            self._apply_lines(*self._pair.modem_lines())
            from_device = threading.Thread(target=self._device_to_app, name="c0c-from-device", daemon=True)
            from_device.start()
            self._threads.append(from_device)
            self._app_to_device()
        except Exception as exc:  # noqa: BLE001 - report class/code only
            if not self._stop.is_set():
                code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
                text = f"{type(exc).__name__}" + (f" (code {code})" if code else "")
                with self._lock:
                    self._stats.last_error = text
                self._event("error", f"Session ended: {text}.")
        finally:
            ended_by_user = self._stop.is_set()
            self._stop.set()
            self._close_io(graceful=self._graceful)
            with self._lock:
                self._stats.state = "stopped" if ended_by_user else "needs_reset"

    def _app_to_device(self) -> None:
        pair = self._pair
        last_lines = 0.0
        app_seen_open = False
        while not self._stop.is_set() and pair is not None:
            data = pair.read(4096)
            for kind, value in pair.parser.feed(data) if data else ():
                if kind == "data":
                    self._send(escape_data(value))
                    with self._lock:
                        self._stats.app_to_device += len(value)
                elif kind == "settings":
                    self._apply_settings(value)
                elif kind == "line":
                    self._apply_break(bool(value & LSR_BREAK))
            now = time.monotonic()
            if now - last_lines >= self.poll_s:
                last_lines = now
                # Only the dedicated open state ends the session. DTR alone is
                # not a close signal: flashing tools pulse it to reset targets.
                opened = pair.app_open()
                if opened:
                    app_seen_open = True
                elif opened is False and app_seen_open and self.end_on_app_close:
                    self._event("info", "Application closed the COM port; session ended.")
                    self._graceful = True
                    return
                self._apply_lines(*pair.modem_lines())

    def _device_to_app(self) -> None:
        parser = TelnetParser()
        sock, pair = self._sock, self._pair
        try:
            while not self._stop.is_set() and sock is not None and pair is not None:
                chunk = sock.recv(4096)
                if not chunk:
                    self._event("warning", "Device closed the connection.")
                    break
                for kind, value in parser.feed(chunk):
                    if kind == "data":
                        # The driver inserts escape records only on the read side;
                        # writes to CNCBn are delivered to the application verbatim.
                        pair.write(value)
                        with self._lock:
                            self._stats.device_to_app += len(value)
                    elif kind == "negotiation":
                        verb, option = value
                        accepted = option in _ACCEPTED_OPTIONS
                        if verb == DO:
                            self._send(bytes((IAC, WILL if accepted else WONT, option)))
                        elif verb == WILL:
                            self._send(bytes((IAC, DO if accepted else DONT, option)))
                    elif kind == "subnegotiation" and len(value) >= 2 and value[0] == COM_PORT_OPTION:
                        command = value[1]
                        with self._lock:
                            self._stats.acknowledged[str(command)] = value[2:].hex()
        except OSError as exc:
            if not self._stop.is_set():
                with self._lock:
                    self._stats.last_error = type(exc).__name__
        finally:
            self._stop.set()
            self._close_io()
