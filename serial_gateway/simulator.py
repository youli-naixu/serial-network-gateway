"""Loopback-only raw or RFC2217 echo simulator; never opens a serial device."""
from __future__ import annotations

import asyncio
import copy

from ._transport import abort_writer, drain_with_progress, start_tcp_server
from .rfc2217 import (BINARY, COM_PORT_OPTION, DO, DONT, IAC, PARITIES, SGA,
                      STOPBITS, WILL, WONT, TelnetParser, escape_data, subnegotiation)


class Simulator:
    """An async context manager with start()/close() and bound_host/bound_port.

    Parameters, DTR/RTS and BREAK are virtual state only. Modem inputs are fixed
    at CTS/DSR/CD high, RI low (0xb0); line status is empty TX, no errors (0x60).
    Only 'none' UART flow control is implemented. Other flow-control requests
    receive the actual unchanged setting, allowing RFC2217 clients to reject
    them instead of mistaking an echo of their request for success.
    """

    SUPPORTED = {
        "kind": "simulation",
        "protocols": ["raw", "rfc2217"],
        "baudrate": "1..4294967295（仅模拟参数，无物理定时）",
        "bytesize": [5, 6, 7, 8], "parity": ["N", "O", "E", "M", "S"],
        "stopbits": [1, 1.5, 2], "controls": ["dtr", "rts", "break"],
        "flowcontrol": ["none"], "inbound_flowcontrol": ["none"],
        "telnet_options": ["BINARY", "SGA", "COM-PORT-OPTION"],
        "commands": ["set/query parameters", "set/query controls", "purge",
                     "line mask", "modem mask", "line/modem query", "suspend/resume"],
        "modem_state": 0xB0, "line_state": 0x60,
        "limitations": ["不接触 UART 或 COM", "不模拟波特率延迟、错误注入或电气线路",
                        "不支持 XON/XOFF、RTS/CTS、DCD、DSR、DTR 流控"],
    }

    def __init__(self, protocol: str = "rfc2217", host: str = "127.0.0.1", port: int = 0,
                 *, buffer_bytes: int = 4096, chunk_bytes: int = 1024,
                 stall_timeout_s: float = 10.0, quiet: bool = True) -> None:
        # quiet: never send unsolicited Telnet negotiation or modem/line
        # notifications. Some virtual COM drivers (HW VSP3 Single) only parse
        # replies to their own COM-PORT-OPTION requests and pass any other
        # option bytes to the application as serial data.
        if protocol not in ("raw", "rfc2217"):
            raise ValueError("模拟器协议必须为 raw 或 rfc2217。")
        if host not in ("127.0.0.1", "::1"):
            raise ValueError("模拟器只能监听本机回环地址。")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("模拟器端口无效。")
        if not 1 <= chunk_bytes <= buffer_bytes or not 0.1 <= stall_timeout_s <= 300:
            raise ValueError("模拟器缓冲区或超时无效。")
        self.protocol, self.host, self.port = protocol, host, port
        self.buffer_bytes, self.chunk_bytes = buffer_bytes, chunk_bytes
        self.stall_timeout_s = stall_timeout_s
        self.quiet = quiet
        self.bound_host, self.bound_port = host, 0
        self._server = None
        self._tasks: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._closing = False
        self.serial_state = self._defaults()
        self.connections = 0
        self.unsupported_controls = 0

    @staticmethod
    def _defaults():
        return {"baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
                "dtr": True, "rts": True, "break": False,
                "flowcontrol": "none", "inbound_flowcontrol": "none",
                "line_mask": 255, "modem_mask": 255, "line_state": 0x60,
                "modem_state": 0xB0, "flow_suspended": False, "purge": None}

    @property
    def supported(self) -> dict:
        return copy.deepcopy(self.SUPPORTED)

    def snapshot(self) -> dict:
        return {"kind": "simulation", "bound_host": self.bound_host, "bound_port": self.bound_port,
                "connections": self.connections, "serial_state": copy.deepcopy(self.serial_state),
                "unsupported_controls": self.unsupported_controls, "supported": self.supported}

    async def start(self) -> Simulator:
        if self._server is not None:
            raise RuntimeError("模拟器已经启动。")
        self._closing = False
        self._server = await start_tcp_server(self._accept, self.host, self.port,
                                               buffer_bytes=self.buffer_bytes)
        address = self._server.sockets[0].getsockname()
        self.bound_host, self.bound_port = address[0], address[1]
        return self

    async def close(self) -> None:
        self._closing = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        writers = list(self._writers)
        for writer in writers:
            writer.transport.abort()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if writers:
            await asyncio.gather(*(abort_writer(writer) for writer in writers))
        self._tasks.clear()
        self._writers.clear()
        if server is not None:
            await server.wait_closed()

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.close()

    def _accept(self, reader, writer):
        self._writers.add(writer)
        if self._closing:
            task = asyncio.create_task(abort_writer(writer), name="simulator-reject")
        else:
            self.connections += 1
            task = asyncio.create_task(self._serve(reader, writer), name="simulator-session")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda _: self._writers.discard(writer))

    async def _serve(self, reader, writer):
        parser = TelnetParser()
        state = self._defaults()
        self.serial_state = state
        pending = bytearray()
        local_options, remote_options = set(), set()

        async def send(data):
            if data:
                writer.write(data)
                await drain_with_progress(writer, self.stall_timeout_s)

        async def notify(command):
            key = "line" if command == 6 else "modem"
            await send(subnegotiation(command + 100, bytes((state[f"{key}_state"] & state[f"{key}_mask"],))))

        async def handle_sb(frame):
            if len(frame) < 2 or frame[0] != COM_PORT_OPTION:
                return
            command, value = frame[1], frame[2:]
            response = None
            if command == 1 and len(value) == 4:
                number = int.from_bytes(value, "big")
                if number:
                    state["baudrate"] = number
                response = state["baudrate"].to_bytes(4, "big")
            elif command in (2, 3, 4) and len(value) == 1:
                key, mapping = {2: ("bytesize", {n: n for n in range(5, 9)}),
                                3: ("parity", PARITIES), 4: ("stopbits", STOPBITS)}[command]
                if value[0] in mapping:
                    state[key] = mapping[value[0]]
                response = bytes((next(code for code, actual in mapping.items() if actual == state[key]),))
            elif command == 5 and len(value) == 1:
                control = value[0]
                if control in (0, 1, 2, 3, 17, 19):
                    response = b"\x01"  # The actual implemented outbound mode.
                    self.unsupported_controls += int(control not in (0, 1))
                elif control in (13, 14, 15, 16, 18):
                    response = b"\x0e"
                    self.unsupported_controls += int(control not in (13, 14))
                else:
                    for key, query, on, off in (("break", 4, 5, 6), ("dtr", 7, 8, 9), ("rts", 10, 11, 12)):
                        if control in (query, on, off):
                            if control != query:
                                state[key] = control == on
                            response = bytes((on if state[key] else off,))
                            break
                    if response is None:
                        self.unsupported_controls += 1  # Unknown controls receive no fake acknowledgement.
            elif command in (6, 7) and len(value) in (0, 1):
                await notify(command)
                return
            elif command in (8, 9) and not value:
                state["flow_suspended"] = command == 8
                response = b""
            elif command in (10, 11) and len(value) == 1:
                state["line_mask" if command == 10 else "modem_mask"] = value[0]
                response = value
            elif command == 12 and len(value) == 1 and value[0] in (1, 2, 3):
                # Echo data waiting for the client is the virtual UART RX queue.
                # TX is processed immediately; already transmitted bytes cannot
                # be retroactively purged, just as with an actual serial port.
                if value[0] in (1, 3):
                    pending.clear()
                state["purge"] = {1: "receive", 2: "transmit", 3: "both"}[value[0]]
                response = value
            if response is not None:
                await send(subnegotiation(command + 100, response))
                if command in (10, 11) and not self.quiet:
                    await notify(command - 4)
                elif command == 9 and pending:
                    output = bytes(pending)
                    pending.clear()
                    await send(escape_data(output))

        try:
            if self.protocol == "rfc2217" and not self.quiet:
                for option in (BINARY, SGA, COM_PORT_OPTION):
                    local_options.add(option)
                    remote_options.add(option)
                    await send(bytes((IAC, WILL, option, IAC, DO, option)))
                await notify(7)
            while True:
                data = await reader.read(self.chunk_bytes)
                if not data:
                    return
                if self.protocol == "raw":
                    await send(data)
                    continue
                for kind, value in parser.feed(data):
                    if kind == "data":
                        if state["flow_suspended"]:
                            if len(pending) + len(value) > self.buffer_bytes:
                                raise BufferError("模拟接收缓冲区已满。")
                            pending.extend(value)
                        else:
                            await send(escape_data(value))
                    elif kind == "subnegotiation":
                        await handle_sb(value)
                    elif kind == "negotiation":
                        verb, option = value
                        enabled = option in (BINARY, SGA, COM_PORT_OPTION)
                        if verb == DO:
                            if not enabled or option not in local_options:
                                await send(bytes((IAC, WILL if enabled else WONT, option)))
                            if enabled:
                                local_options.add(option)
                        elif verb == WILL:
                            if not enabled or option not in remote_options:
                                await send(bytes((IAC, DO if enabled else DONT, option)))
                            if enabled:
                                remote_options.add(option)
                        elif verb == DONT and option in local_options:
                            local_options.discard(option)
                            await send(bytes((IAC, WONT, option)))
                        elif verb == WONT and option in remote_options:
                            remote_options.discard(option)
                            await send(bytes((IAC, DONT, option)))
        except (OSError, ConnectionError, TimeoutError, BufferError):
            pass
        finally:
            pending.clear()
            await abort_writer(writer)
