import random

import pytest

from serial_gateway.rfc2217 import (BINARY, COM_PORT_OPTION, DO, IAC, SB, SE, WILL,
                                    RFC2217Observer, TelnetParser, escape_data, subnegotiation)


def fragments(data, seed=23, maximum=31):
    rng = random.Random(seed)
    while data:
        count = rng.randint(1, maximum)
        yield data[:count]
        data = data[count:]


def test_every_binary_byte_and_fragmented_iac():
    payload = bytes(range(256)) * 13
    parser = TelnetParser()
    events = [event for chunk in fragments(escape_data(payload), maximum=7) for event in parser.feed(chunk)]
    assert b"".join(value for kind, value in events if kind == "data") == payload
    assert all(kind == "data" for kind, _ in events)
    assert parser.buffered_bytes == 0


@pytest.mark.parametrize("split", range(1, 32))
def test_observer_requests_confirmations_and_binary_split(split):
    request = (bytes((IAC, WILL, BINARY, IAC, DO, COM_PORT_OPTION)) +
               subnegotiation(1, (1500000).to_bytes(4, "big")) + subnegotiation(2, b"\x07") +
               subnegotiation(3, b"\x03") + subnegotiation(4, b"\x02") +
               subnegotiation(5, b"\x09") + subnegotiation(5, b"\x0c") +
               subnegotiation(5, b"\x05") + subnegotiation(10, b"\xff") +
               subnegotiation(11, b"\xb0") + subnegotiation(12, b"\x03") + escape_data(b"\x00\xffhello"))
    observer = RFC2217Observer()
    for offset in range(0, len(request), split):
        observer.feed(request[offset:offset + split], "client")
    assert observer.payload_from_client == 7
    assert observer.serial_confirmed == {}
    expected = {"baudrate": 1500000, "bytesize": 7, "parity": "E", "stopbits": 2,
                "dtr": False, "rts": False, "break": True, "line_mask": 255,
                "modem_mask": 176, "purge": "both"}
    assert observer.serial_requested == expected
    assert observer.negotiation["client"] == {"binary_will": True, "rfc2217_do": True}
    response = b"".join(subnegotiation(command + 100, value) for command, value in
                        [(1, (1500000).to_bytes(4, "big")), (2, b"\x07"), (3, b"\x03"),
                         (4, b"\x02"), (5, b"\x09"), (5, b"\x0c"), (5, b"\x05"),
                         (10, b"\xff"), (11, b"\xb0"), (12, b"\x03")])
    for chunk in fragments(response, maximum=split):
        observer.feed(chunk, "remote")
    assert observer.serial_confirmed == expected
    assert observer.payload_from_remote == 0


def test_queries_and_wrong_direction_are_not_hardware_confirmations():
    observer = RFC2217Observer()
    observer.feed(subnegotiation(1, b"\0" * 4) + subnegotiation(5, b"\x07"))
    assert observer.serial_requested == {"baudrate": "query", "dtr": "query"}
    observer.feed(subnegotiation(101, (9600).to_bytes(4, "big")), "client")
    observer.feed(subnegotiation(1, (19200).to_bytes(4, "big")), "remote")
    assert observer.serial_confirmed == {}
    observer.feed(subnegotiation(105, b"\x08"), "remote")
    assert observer.serial_confirmed == {"dtr": True}


def test_bounded_oversized_and_malformed_sb_resynchronization():
    parser = TelnetParser(max_subnegotiation=16)
    assert list(parser.feed(bytes((IAC, SB, COM_PORT_OPTION, 1)))) == []
    for _ in range(1024):
        assert list(parser.feed(b"x" * 1024)) == []
        assert parser.buffered_bytes <= 16
    # Escaped IAC and a stray SE inside a discarded SB are not terminators.
    assert list(parser.feed(bytes((IAC, IAC, SE)))) == []
    events = list(parser.feed(bytes((IAC, SE)) + b"good"))
    assert events == [("data", b"good")]
    assert parser.oversized_subnegotiations == 1
    malformed = bytes((IAC, SB, 44, 5, IAC, WILL, 1, IAC, SE))
    assert list(parser.feed(malformed + b"next")) == [("data", b"next")]
    assert parser.malformed_subnegotiations == 1
    assert parser.buffered_bytes == 0


def test_all_control_categories_and_masks():
    observer = RFC2217Observer()
    for code, key, value in [(1, "flowcontrol", "none"), (2, "flowcontrol", "xonxoff"),
                             (3, "flowcontrol", "rtscts"), (14, "inbound_flowcontrol", "none"),
                             (15, "inbound_flowcontrol", "xonxoff"), (16, "inbound_flowcontrol", "rtscts"),
                             (17, "flowcontrol", "dcd"), (18, "inbound_flowcontrol", "dtr"),
                             (19, "flowcontrol", "dsr")]:
        observer.feed(subnegotiation(5, bytes((code,))))
        assert observer.serial_requested[key] == value
    observer.feed(subnegotiation(108) + subnegotiation(106, b"\x60") + subnegotiation(107, b"\xb0"), "remote")
    assert observer.serial_confirmed == {"flow_suspended": True, "line_state": 96, "modem_state": 176}
    observer.feed(subnegotiation(109), "remote")
    assert observer.serial_confirmed["flow_suspended"] is False
