from __future__ import annotations

import re
import subprocess
import threading
from collections import deque
from pathlib import Path


def _executable(value: str | Path, expected_name: str) -> Path:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.name.casefold() != expected_name.casefold():
        raise ValueError(f"请指定真实的 {expected_name} 文件。")
    return path


def parse_com0com_ports(text: str) -> dict[str, str]:
    ports: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(CNC[AB][0-9]+)\s+(.+?)\s*$", line)
        if match:
            ports[match.group(1)] = match.group(2)
    return ports


def list_com0com_ports(setupc_path: str | Path) -> dict[str, str]:
    executable = _executable(setupc_path, "setupc.exe")
    completed = subprocess.run(
        [str(executable), "--no-update-fnames", "--detail-prms", "list"],
        capture_output=True, text=True, errors="replace", timeout=10,
        shell=False, check=True,
    )
    if len(completed.stdout) > 256 * 1024:
        raise ValueError("端口枚举结果异常过长。")
    return parse_com0com_ports(completed.stdout)


def hub4com_argv(hub_path: str | Path, endpoint: str, tcp_port: int) -> list[str]:
    executable = _executable(hub_path, "hub4com.exe")
    if not re.fullmatch(r"CNCB[0-9]+", endpoint):
        raise ValueError("桥接端只接受已枚举的 CNCBn 虚拟端点，不能指定物理COM或任意设备路径。")
    if isinstance(tcp_port, bool) or not isinstance(tcp_port, int) or not 1 <= tcp_port <= 65535:
        raise ValueError("本机TCP端口应在1–65535范围内。")
    return [
        str(executable),
        "--create-filter=escparse,com,parse",
        "--create-filter=pinmap,com,pinmap:--rts=cts --dtr=dsr",
        "--create-filter=linectl,com,lc:--br=local --lc=local",
        "--add-filters=0:com",
        "--create-filter=telnet,tcp,telnet:--comport=client",
        "--create-filter=pinmap,tcp,pinmap:--rts=cts --dtr=dsr --break=break",
        "--create-filter=linectl,tcp,lc:--br=remote --lc=remote",
        "--add-filters=1:tcp",
        "--share-mode=on",
        "--octs=off",
        "\\\\.\\" + endpoint,
        "--use-driver=tcp",
        "--reconnect=no",
        f"*127.0.0.1:{tcp_port}",
    ]


class Hub4ComBridge:
    """Explicit adapter for an existing virtual pair; never installs drivers."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._lines: deque[str] = deque(maxlen=50)
        self._lock = threading.Lock()

    def start(self, setupc_path: str | Path, hub_path: str | Path, endpoint: str, tcp_port: int) -> None:
        if self.process is not None:
            raise RuntimeError("桥接器尚未停止。")
        argv = hub4com_argv(hub_path, endpoint, tcp_port)
        ports = list_com0com_ports(setupc_path)
        number = endpoint[4:]
        if endpoint not in ports or "CNCA" + number not in ports:
            raise ValueError("在com0com枚举中未找到完整端口对，拒绝打开。")
        with self._lock:
            self._lines.clear()
        self.process = subprocess.Popen(
            argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_output, args=(self.process,), daemon=True, name="hub4com-output")
        self._reader.start()

    def _read_output(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        try:
            while True:
                line = process.stdout.readline(2048)
                if not line:
                    break
                with self._lock:
                    self._lines.append(line.rstrip())
        finally:
            process.stdout.close()

    def snapshot(self) -> dict:
        with self._lock:
            lines = list(self._lines)
        process = self.process
        return {"running": bool(process and process.poll() is None), "exit_code": process.poll() if process else None, "events": lines}

    def stop(self, timeout: float = 3.0) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout)
        if self._reader is not None:
            self._reader.join(timeout)
            if self._reader.is_alive():
                raise TimeoutError("hub4com输出线程尚未退出。")
        self.process = None
        self._reader = None
