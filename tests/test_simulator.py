import asyncio
import random

import pytest

from serial_gateway.rfc2217 import COM_PORT_OPTION, TelnetParser, escape_data, subnegotiation
from serial_gateway.simulator import Simulator


class WireClient:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.parser = TelnetParser()
        self.frames = []
        self.payload = bytearray()

    async def collect(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                data = await self.reader.read(4096)
                assert data, "unexpected simulator EOF"
                for kind, value in self.parser.feed(data):
                    if kind == "data":
                        self.payload.extend(value)
                    elif kind == "subnegotiation" and value[0] == COM_PORT_OPTION:
                        self.frames.append(value[1:])

    async def request(self, command, value=b""):
        self.frames = [frame for frame in self.frames if frame[0] != command + 100]
        self.writer.write(subnegotiation(command, value))
        await self.writer.drain()
        await self.collect(lambda: any(frame[0] == command + 100 for frame in self.frames))
        index = next(i for i, frame in enumerate(self.frames) if frame[0] == command + 100)
        return self.frames.pop(index)[1:]


async def open_wire(simulator):
    reader, writer = await asyncio.open_connection(simulator.bound_host, simulator.bound_port)
    return WireClient(reader, writer)


def test_raw_localhost_all_bytes_random_chunks():
    async def exercise():
        async with Simulator("raw", buffer_bytes=1024, chunk_bytes=137) as simulator:
            reader, writer = await asyncio.open_connection(simulator.bound_host, simulator.bound_port)
            payload = bytes(range(256)) * 256

            async def produce():
                rng = random.Random(17)
                offset = 0
                while offset < len(payload):
                    end = offset + rng.randint(1, 991)
                    writer.write(payload[offset:end])
                    await writer.drain()
                    offset = end

            sending = asyncio.create_task(produce())
            assert await asyncio.wait_for(reader.readexactly(len(payload)), 5) == payload
            await sending
            writer.close()
            await writer.wait_closed()
    asyncio.run(exercise())


def test_rfc2217_set_query_controls_masks_and_full_binary():
    async def exercise():
        async with Simulator() as simulator:
            wire = await open_wire(simulator)
            try:
                for command, desired in [(1, (1500000).to_bytes(4, "big")), (2, b"\x07"),
                                          (3, b"\x03"), (4, b"\x02")]:
                    assert await wire.request(command, desired) == desired
                    assert await wire.request(command, b"\0" * len(desired)) == desired
                for control, query in [(9, 7), (12, 10), (5, 4), (8, 7), (11, 10), (6, 4)]:
                    assert await wire.request(5, bytes((control,))) == bytes((control,))
                    assert await wire.request(5, bytes((query,))) == bytes((control,))
                assert await wire.request(10, b"\xff") == b"\xff"
                assert await wire.request(11, b"\xb0") == b"\xb0"
                assert await wire.request(6, b"\0") == b"\x60"
                assert await wire.request(7, b"\0") == b"\xb0"
                payload = bytes(range(256)) * 127
                encoded = escape_data(payload)
                for offset in range(0, len(encoded), 73):
                    wire.writer.write(encoded[offset:offset + 73])
                    await wire.writer.drain()
                await wire.collect(lambda: len(wire.payload) >= len(payload))
                assert wire.payload == payload
                snapshot = simulator.snapshot()
                assert snapshot["kind"] == "simulation"
                assert snapshot["serial_state"]["baudrate"] == 1500000
                assert snapshot["serial_state"]["parity"] == "E"
            finally:
                wire.writer.close()
                await wire.writer.wait_closed()
    asyncio.run(exercise())


def test_unsupported_flow_control_returns_actual_setting_not_success():
    async def exercise():
        async with Simulator() as simulator:
            wire = await open_wire(simulator)
            try:
                for requested in (2, 3, 17, 19):
                    assert await wire.request(5, bytes((requested,))) == b"\x01"
                for requested in (15, 16, 18):
                    assert await wire.request(5, bytes((requested,))) == b"\x0e"
                # Invalid line parameters also report the unchanged current value.
                assert await wire.request(2, b"\x01") == b"\x08"
                assert simulator.serial_state["flowcontrol"] == "none"
                assert simulator.unsupported_controls == 7
                assert simulator.supported["flowcontrol"] == ["none"]
                # An unknown control is ignored, not mirrored as an acknowledgement.
                wire.writer.write(subnegotiation(5, b"\x7f") + subnegotiation(1, b"\0" * 4))
                await wire.writer.drain()
                await wire.collect(lambda: any(frame[0] == 101 for frame in wire.frames))
                assert bytes((105, 127)) not in wire.frames
            finally:
                wire.writer.close()
                await wire.writer.wait_closed()
    asyncio.run(exercise())


def test_suspend_receive_purge_resume_and_transmit_purge():
    async def exercise():
        async with Simulator() as simulator:
            wire = await open_wire(simulator)
            try:
                assert await wire.request(8) == b""
                wire.writer.write(escape_data(b"discard-on-rx-purge\xff"))
                assert await wire.request(12, b"\x01") == b"\x01"
                assert await wire.request(9) == b""
                wire.writer.write(escape_data(b"fresh\xff"))
                await wire.writer.drain()
                await wire.collect(lambda: len(wire.payload) >= 6)
                assert wire.payload == b"fresh\xff"
                wire.payload.clear()
                await wire.request(8)
                wire.writer.write(escape_data(b"keep-rx\xff"))
                await wire.request(12, b"\x02")
                await wire.request(9)
                await wire.collect(lambda: len(wire.payload) >= 8)
                assert wire.payload == b"keep-rx\xff"
            finally:
                wire.writer.close()
                await wire.writer.wait_closed()
    asyncio.run(exercise())


def test_oversized_sb_is_bounded_and_following_query_works():
    async def exercise():
        async with Simulator(chunk_bytes=137) as simulator:
            wire = await open_wire(simulator)
            try:
                wire.writer.write(b"\xff\xfa\x2c\x01" + b"x" * (512 * 1024) + b"\xff\xf0")
                await wire.writer.drain()
                assert await wire.request(1, b"\0" * 4) == (9600).to_bytes(4, "big")
            finally:
                wire.writer.close()
                await wire.writer.wait_closed()
    asyncio.run(exercise())


def test_close_active_connections_and_repeat_start():
    async def exercise():
        simulator = Simulator("raw")
        for _ in range(5):
            await simulator.start()
            reader, writer = await asyncio.open_connection(simulator.bound_host, simulator.bound_port)
            writer.write(b"x")
            await writer.drain()
            assert await reader.readexactly(1) == b"x"
            await asyncio.wait_for(simulator.close(), 2)
            assert not simulator._tasks and not simulator._writers
            try:
                assert await reader.read(1) == b""
            except ConnectionResetError:
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass
        await simulator.close()
    asyncio.run(exercise())


def test_pyserial_direct_simulator_and_rejected_unsupported_control():
    serial = pytest.importorskip("serial")

    async def exercise():
        # pySerial's modem-line properties rely on unsolicited NOTIFY-MODEMSTATE,
        # so this test uses the chatty mode. The default quiet mode targets
        # drivers that mistake unsolicited Telnet bytes for serial data.
        async with Simulator(quiet=False) as simulator:
            def client_work():
                with serial.serial_for_url(f"rfc2217://127.0.0.1:{simulator.bound_port}?timeout=2", timeout=3) as client:
                    client.baudrate = 921600
                    client.bytesize = 7
                    client.parity = "O"
                    client.stopbits = 1.5
                    client.dtr = False
                    client.rts = False
                    client.break_condition = True
                    client.break_condition = False
                    client.bytesize, client.parity, client.stopbits = 8, "N", 1
                    client.reset_input_buffer()
                    client.reset_output_buffer()
                    assert client.cts and client.dsr and client.cd and not client.ri
                    payload = bytes(range(256)) * 256
                    client.write(payload)
                    assert client.read(len(payload)) == payload
                    with pytest.raises(ValueError, match="rejected"):
                        client.rtscts = True
            await asyncio.to_thread(client_work)
    asyncio.run(exercise())


def test_simulator_rejects_non_loopback_configuration():
    with pytest.raises(ValueError):
        Simulator(host="0.0.0.0")
