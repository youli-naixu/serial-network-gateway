import pytest

from serial_gateway import c0c_port
from serial_gateway.c0c_port import C0C_PIN_ROPEN, InsertParser, SERIAL_LSRMST_LSR_DATA, SERIAL_LSRMST_LSR_NODATA


def test_break_byte_is_a_line_event_not_data():
    parser = InsertParser(0xF7)
    events = parser.feed(b"A" + bytes((0xF7, SERIAL_LSRMST_LSR_DATA, 0x10, 0x00)) + b"B"
                         + bytes((0xF7, SERIAL_LSRMST_LSR_NODATA, 0x60)))
    assert b"".join(v for k, v in events if k == "data") == b"AB"
    assert [v for k, v in events if k == "line"] == [0x10, 0x60]


class _FakeKey:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("stored, expected", [
    (C0C_PIN_ROPEN, True), ("ropen", True), ("ROPEN", True),
    (0x02, False), ("rdtr", False),
])
def test_ri_wired_to_open_reads_dword_or_string(monkeypatch, stored, expected):
    winreg = pytest.importorskip("winreg")
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _FakeKey(stored))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (key.value, 4))
    assert c0c_port.ri_wired_to_open("CNCB0") is expected


def test_ri_wired_to_open_missing_key(monkeypatch):
    winreg = pytest.importorskip("winreg")

    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", missing)
    assert c0c_port.ri_wired_to_open("CNCB9") is False


@pytest.mark.parametrize("name", ["COM3", "CNCA0", "CNCB", "CNCBx", "\\\\.\\COM3"])
def test_pair_end_refuses_non_hidden_endpoints(name):
    with pytest.raises(ValueError):
        c0c_port.PairEnd(name)
