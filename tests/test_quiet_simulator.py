import asyncio

from serial_gateway.rfc2217 import IAC, SB, TelnetParser, subnegotiation
from serial_gateway.simulator import Simulator


async def _exchange(quiet: bool, request: bytes, payload: bytes) -> bytes:
    async with Simulator(quiet=quiet) as simulator:
        reader, writer = await asyncio.open_connection(simulator.bound_host, simulator.bound_port)
        writer.write(request + payload)
        await writer.drain()
        output = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), 0.6)
                if not chunk:
                    break
                output += chunk
        except asyncio.TimeoutError:
            pass
        writer.close()
        return output


def test_quiet_mode_only_answers_requests():
    request = subnegotiation(1, (115200).to_bytes(4, "big"))
    output = asyncio.run(_exchange(True, request, b"AB\xff\xff"))
    events = list(TelnetParser().feed(output))
    assert [k for k, _ in events if k == "negotiation"] == []
    subs = [v for k, v in events if k == "subnegotiation"]
    assert subs == [bytes((44, 101)) + (115200).to_bytes(4, "big")]
    assert b"".join(v for k, v in events if k == "data") == b"AB\xff"


def test_chatty_mode_still_negotiates_for_standard_clients():
    output = asyncio.run(_exchange(False, b"", b""))
    events = list(TelnetParser().feed(output))
    assert any(k == "negotiation" for k, _ in events)
    assert output.startswith(bytes((IAC,))) and bytes((IAC, SB)) in output
