from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class GatewayProfile:
    name: str = "本机模拟测试"
    mode: str = "simulator"
    protocol: str = "rfc2217"
    listen_host: str = "127.0.0.1"
    listen_port: int = 7000
    remote_host: str = ""
    remote_port: int = 2217
    nodelay: bool = True
    buffer_bytes: int = 4096
    chunk_bytes: int = 1024
    connect_timeout_s: float = 5.0
    stall_timeout_s: float = 10.0
    keepalive_s: int = 10
    notes: str = ""

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValueError("配置名称应为 1–80 个字符。")
        if self.mode not in ("simulator", "device"):
            raise ValueError("模式只能是 simulator 或 device。")
        if self.protocol not in ("rfc2217", "raw"):
            raise ValueError("协议只能是 rfc2217 或 raw。")
        if self.listen_host not in ("127.0.0.1", "::1"):
            raise ValueError("电脑端入口仅监听本机回环地址 127.0.0.1 或 ::1。")
        for label, value, minimum in (("本地端口", self.listen_port, 0), ("远端端口", self.remote_port, 1)):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 65535:
                raise ValueError(f"{label}必须在 {minimum}–65535 范围内。")
        if not isinstance(self.remote_host, str) or len(self.remote_host) > 253 or any(c.isspace() for c in self.remote_host):
            raise ValueError("远端地址应为不含空白的 IP 或主机名。")
        if self.mode == "device" and not self.remote_host:
            raise ValueError("设备模式必须填写远端 IP 或主机名。")
        if not isinstance(self.nodelay, bool):
            raise ValueError("TCP_NODELAY 必须为布尔值。")
        for label, value in (("缓冲区", self.buffer_bytes), ("分块", self.chunk_bytes)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{label}大小必须为整数。")
        if not 512 <= self.buffer_bytes <= 1024 * 1024:
            raise ValueError("每方向缓冲区应在 512–1048576 字节范围内。")
        if not 1 <= self.chunk_bytes <= min(self.buffer_bytes, 16384):
            raise ValueError("单次读块必须大于零且不超过缓冲区与 16384 字节。")
        for label, value in (("连接超时", self.connect_timeout_s), ("发送停滞超时", self.stall_timeout_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.1 <= value <= 300:
                raise ValueError(f"{label}应在 0.1–300 秒范围内。")
        if isinstance(self.keepalive_s, bool) or not isinstance(self.keepalive_s, int) or not 1 <= self.keepalive_s <= 300:
            raise ValueError("保活间隔应在 1–300 秒范围内。")
        if not isinstance(self.notes, str) or len(self.notes) > 2000:
            raise ValueError("备注最长为 2000 个字符。")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayProfile:
        if not isinstance(data, dict):
            raise ValueError("配置档必须是 JSON 对象。")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError("未知配置字段：" + ", ".join(sorted(unknown)))
        obj = cls(**data)
        obj.validate()
        return obj
