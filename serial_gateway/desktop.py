"""Desktop integration for the flashing assistant: single instance, autostart, friendly errors.

Single instance uses a loopback TCP port as a lock and a "show" doorbell: the
first copy listens on 127.0.0.1:<port>; a second copy connects, sends ``show``
and exits. Autostart is the per-user ``HKCU\\...\\Run`` value (no admin).
"""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Callable

INSTANCE_PORT = 47821
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "ASRWirelessFlashAssistant"


class SingleInstance:
    """Holds the instance lock; ``on_show`` is called (from a thread) when another copy starts."""

    def __init__(self, on_show: Callable[[], None], port: int = INSTANCE_PORT) -> None:
        self.port = port
        self._on_show = on_show
        self._sock: socket.socket | None = None

    def acquire(self) -> bool:
        """True when this is the first copy. Otherwise the running copy is asked to show itself."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: no other socket may share the port
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:  # POSIX: rebinding right after release would otherwise hit TIME_WAIT
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", self.port))
        except OSError:
            sock.close()
            ring(self.port)
            return False
        sock.listen(4)
        self._sock = sock
        threading.Thread(target=self._serve, name="single-instance", daemon=True).start()
        return True

    def _serve(self) -> None:
        sock = self._sock
        while sock is not None:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(1.0)
                try:
                    if conn.recv(16).strip() == b"show":
                        self._on_show()
                except OSError:
                    pass

    def release(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                # On Linux close() alone does not wake a thread blocked in accept(),
                # which keeps the port bound.
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def ring(port: int = INSTANCE_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as conn:
            conn.sendall(b"show")
        return True
    except OSError:
        return False


def launch_command() -> str:
    """Command line that starts this program the same way it was started now."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else exe
    # Run keys start in System32; the source checkout must be importable from there.
    source = Path(__file__).resolve().parents[1]
    return (f'"{runner}" -c "import sys; sys.path.insert(0, r\'{source}\'); '
            f'from serial_gateway.assistant import main; main(start_minimized=True)"')


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        raise RuntimeError("Autostart is only supported on Windows.")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_VALUE)
            except FileNotFoundError:
                pass


# Error text in the reader's words, with what to do next. Matched against
# "ExceptionClass: message (code N)" strings produced by the bridge/station.
_FRIENDLY = [
    (r"\bcode 5\b|PermissionError|Access is denied|拒绝访问",
     "虚拟串口正被另一个程序占用。请关掉多开的本程序或其他串口工具后再试。",
     "The virtual COM is in use by another program. Close the other copy of this program or other serial tools and try again."),
    (r"\bcode 2\b|FileNotFoundError.*CNC|CNCB\d+.*not found",
     "找不到虚拟串口。请点“准备虚拟串口”重新准备一次。",
     "The virtual COM was not found. Click \"Prepare virtual COM\" to set it up again."),
    (r"ConnectionRefusedError|code 10061",
     "ESP32 拒绝连接。请确认 ESP32 已开机、固件支持无线烧录，并且地址没填错。",
     "The ESP32 refused the connection. Make sure it is on, its firmware supports wireless flashing and the address is right."),
    (r"timed out|TimeoutError|socket\.timeout|code 10060",
     "连不上 ESP32（超时）。请检查 ESP32 是否在线、电脑和它是否在同一个网络。",
     "The ESP32 did not answer in time. Check that it is online and on the same network as this PC."),
    (r"HTTP 401|bad_token", "ESP32 要求令牌。请在设置里填写令牌所在的环境变量名。",
     "The ESP32 requires a token. Set the token environment variable in Settings."),
    (r"HTTP 403|ip_not_allowed", "ESP32 拒绝了这台电脑的地址。请在 ESP32 的允许列表里加上本机。",
     "The ESP32 rejected this PC's address. Add it to the ESP32's allow list."),
    (r"HTTP 409|relay_busy", "ESP32 正忙（例如在切换继电器），稍后会自动重试。",
     "The ESP32 is busy; it will be retried shortly."),
    (r"HTTP 404|not_found", "ESP32 上没有“进入下载模式”接口。请确认 ESP32 固件版本，或在设置里改接口地址。",
     "The ESP32 has no \"enter download mode\" endpoint. Check its firmware or the URL in Settings."),
    (r"URLError|getaddrinfo|code 11001", "找不到这个 ESP32 地址。请检查设置里的地址。",
     "That ESP32 address could not be found. Check the address in Settings."),
    (r"com0com is not installed", "没有安装 com0com 虚拟串口驱动。请先安装官方签名版 com0com。",
     "The com0com virtual COM driver is not installed. Install the signed com0com build first."),
    (r"needs close detection", "虚拟串口还没准备完整。请点“准备虚拟串口”重新准备一次。",
     "The virtual COM is not fully prepared. Click \"Prepare virtual COM\" again."),
    (r"cancelled|Administrator approval", "管理员确认被取消了。需要准备虚拟串口时请在弹窗里点“是”。",
     "Administrator approval was cancelled. Click Yes in the prompt to prepare the virtual COM."),
]


def friendly_error(text: Any, language: str = "zh-CN") -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    english = language == "en"
    for pattern, zh, en in _FRIENDLY:
        if re.search(pattern, raw, re.I):
            return en if english else zh
    return ("Unexpected error: " if english else "出现了意外错误：") + raw[:160]
