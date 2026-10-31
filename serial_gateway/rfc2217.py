"""Bounded Telnet decoding for observation and the local RFC2217 simulator.

Nothing in this module is applied to the gateway's forwarded byte stream.
``serial_confirmed`` means a server response was observed, not hardware proof.
"""
from __future__ import annotations

from typing import Iterator

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
BINARY, SGA, COM_PORT_OPTION = 0, 3, 44
PARITIES = {1: "N", 2: "O", 3: "E", 4: "M", 5: "S"}
STOPBITS = {1: 1, 2: 2, 3: 1.5}


def escape_data(data: bytes) -> bytes:
    return data.replace(b"\xff", b"\xff\xff")


def subnegotiation(command: int, value: bytes = b"") -> bytes:
    return bytes((IAC, SB, COM_PORT_OPTION, command)) + escape_data(value) + bytes((IAC, SE))


class TelnetParser:
    """Incremental decoder; an oversized SB is discarded through its IAC SE.

    Events are ("data", bytes), ("negotiation", (verb, option)),
    ("subnegotiation", bytes including the option), or ("command", int).
    The caller owns the input chunk size; persistent SB storage is capped.
    """

    def __init__(self, max_subnegotiation: int = 256) -> None:
        if max_subnegotiation < 6:
            raise ValueError("子协商缓冲区至少需要 6 字节。")
        self.max_subnegotiation = max_subnegotiation
        self.oversized_subnegotiations = 0
        self.malformed_subnegotiations = 0
        self._state = "data"
        self._verb = 0
        self._sb = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._sb)

    def _append_sb(self, byte: int) -> None:
        if len(self._sb) >= self.max_subnegotiation:
            self._sb.clear()
            self.oversized_subnegotiations += 1
            self._state = "discard"
        else:
            self._sb.append(byte)
            self._state = "sb"

    def feed(self, data: bytes) -> Iterator[tuple[str, object]]:
        payload = bytearray()
        for byte in data:
            if self._state == "data":
                if byte == IAC:
                    if payload:
                        yield "data", bytes(payload)
                        payload.clear()
                    self._state = "iac"
                else:
                    payload.append(byte)
            elif self._state == "iac":
                self._state = "data"
                if byte == IAC:
                    payload.append(IAC)
                elif byte in (WILL, WONT, DO, DONT):
                    self._verb = byte
                    self._state = "negotiate"
                elif byte == SB:
                    self._sb.clear()
                    self._state = "sb"
                else:
                    yield "command", byte
            elif self._state == "negotiate":
                yield "negotiation", (self._verb, byte)
                self._state = "data"
            elif self._state == "sb":
                if byte == IAC:
                    self._state = "sb_iac"
                else:
                    self._append_sb(byte)
            elif self._state == "sb_iac":
                if byte == SE:
                    yield "subnegotiation", bytes(self._sb)
                    self._sb.clear()
                    self._state = "data"
                elif byte == IAC:
                    self._append_sb(IAC)
                else:
                    self._sb.clear()
                    self.malformed_subnegotiations += 1
                    self._state = "discard"
            elif self._state == "discard":
                if byte == IAC:
                    self._state = "discard_iac"
            elif self._state == "discard_iac":
                self._state = "data" if byte == SE else "discard"
        if payload:
            yield "data", bytes(payload)


def _setting(command: int, value: bytes, request: bool) -> dict:
    if command == 1 and len(value) == 4:
        baud = int.from_bytes(value, "big")
        return {"baudrate": "query" if baud == 0 and request else baud} if baud or request else {}
    if command in (2, 3, 4) and len(value) == 1:
        key, options = {
            2: ("bytesize", {i: i for i in range(5, 9)}),
            3: ("parity", PARITIES),
            4: ("stopbits", STOPBITS),
        }[command]
        if value[0] == 0 and request:
            return {key: "query"}
        return {key: options[value[0]]} if value[0] in options else {}
    if command == 5 and len(value) == 1:
        controls = {
            1: ("flowcontrol", "none"), 2: ("flowcontrol", "xonxoff"),
            3: ("flowcontrol", "rtscts"), 5: ("break", True), 6: ("break", False),
            8: ("dtr", True), 9: ("dtr", False), 11: ("rts", True), 12: ("rts", False),
            14: ("inbound_flowcontrol", "none"), 15: ("inbound_flowcontrol", "xonxoff"),
            16: ("inbound_flowcontrol", "rtscts"), 17: ("flowcontrol", "dcd"),
            18: ("inbound_flowcontrol", "dtr"), 19: ("flowcontrol", "dsr"),
        }
        if request:
            controls.update({0: ("flowcontrol", "query"), 4: ("break", "query"),
                             7: ("dtr", "query"), 10: ("rts", "query"),
                             13: ("inbound_flowcontrol", "query")})
        return dict([controls[value[0]]]) if value[0] in controls else {}
    if command in (6, 7) and len(value) == 1:
        return {"line_state" if command == 6 else "modem_state": "query" if request else value[0]}
    if command in (8, 9) and not value:
        return {"flow_suspended": command == 8}
    if command in (10, 11) and len(value) == 1:
        return {"line_mask" if command == 10 else "modem_mask": value[0]}
    if command == 12 and len(value) == 1 and value[0] in (1, 2, 3):
        return {"purge": {1: "receive", 2: "transmit", 3: "both"}[value[0]]}
    return {}


class RFC2217Observer:
    """Two independent parsers; feed returns this fragment's payload count."""

    def __init__(self, max_subnegotiation: int = 256) -> None:
        self.parsers = {direction: TelnetParser(max_subnegotiation) for direction in ("client", "remote")}
        self.serial_requested: dict = {}
        self.serial_confirmed: dict = {}
        self.negotiation: dict = {"client": {}, "remote": {}}
        self.payload_from_client = 0
        self.payload_from_remote = 0

    def feed(self, data: bytes, direction: str = "client") -> int:
        if direction not in self.parsers:
            raise ValueError("观察方向必须是 client 或 remote。")
        payload = 0
        for kind, value in self.parsers[direction].feed(data):
            if kind == "data":
                payload += len(value)
            elif kind == "negotiation":
                verb, option = value
                if option in (BINARY, COM_PORT_OPTION):
                    name = "binary" if option == BINARY else "rfc2217"
                    side = "will" if verb in (WILL, WONT) else "do"
                    self.negotiation[direction][f"{name}_{side}"] = verb in (WILL, DO)
            elif kind == "subnegotiation" and len(value) >= 2 and value[0] == COM_PORT_OPTION:
                command = value[1]
                if direction == "client" and 1 <= command <= 12:
                    self.serial_requested.update(_setting(command, value[2:], True))
                elif direction == "remote" and 101 <= command <= 112:
                    self.serial_confirmed.update(_setting(command - 100, value[2:], False))
        if direction == "client":
            self.payload_from_client += payload
        else:
            self.payload_from_remote += payload
        return payload
