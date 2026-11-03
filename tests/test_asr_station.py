import time

import pytest

from serial_gateway import asr_station, vcom
from serial_gateway.asr_station import FlashStation, StationConfig


def test_parse_pnp_pairs_reads_class_and_label(monkeypatch):
    monkeypatch.setattr("serial_gateway.c0c_port.ri_wired_to_open", lambda endpoint: endpoint == "CNCB0")
    text = "\n".join([
        "COM0COM\\PORT\\CNCA0|Ports|ASR 无线烧录 (COM11)",
        "COM0COM\\PORT\\CNCB0|CNCPorts|com0com - serial port emulator CNCB0 (CNCB0)",
        "COM0COM\\PORT\\CNCA1|CNCPorts|com0com - serial port emulator CNCA1 (COM20)",
        "COM0COM\\PORT\\CNCB1|CNCPorts|com0com - serial port emulator CNCB1 (CNCB1)",
    ])
    pairs = vcom.parse_pnp_pairs(text)
    assert pairs[0] == {"pair": 0, "com": "COM11", "endpoint": "CNCB0", "close_detect": True,
                        "visible": True, "label": "ASR 无线烧录"}
    assert pairs[1]["com"] == "COM20" and not pairs[1]["visible"] and not pairs[1]["close_detect"]
    assert asr_station.find_station_pair(pairs, "ASR 无线烧录") is pairs[0]
    assert asr_station.find_station_pair(pairs, "其他") is None
    assert asr_station.reusable_pair(pairs) is pairs[0]


def test_parse_setupc_list():
    text = "       CNCA0 PortName=COM#,RealPortName=COM11\n       CNCB0 PortName=-,RI=ROPEN\n"
    ports = vcom.parse_setupc_list(text)
    assert ports["CNCA0"]["RealPortName"] == "COM11"
    assert ports["CNCB0"]["RI"] == "ROPEN"


@pytest.mark.parametrize("label", ["ASR 无线烧录", "Flash-1", "ESP_32 gateway"])
def test_label_accepts_plain_names(label):
    assert vcom.validate_label(label) == label


@pytest.mark.parametrize("label", ["", " lead", "has (paren)", "a" * 41, "semi;colon", "quote\""])
def test_label_rejects_unsafe_names(label):
    with pytest.raises(ValueError):
        vcom.validate_label(label)


def test_config_roundtrip_and_defaults(tmp_path):
    path = tmp_path / "station.json"
    assert asr_station.load_config(path) == StationConfig()
    config = StationConfig(host="192.168.50.20", port=3333, token_env="SNG_TOKEN")
    asr_station.save_config(config, path)
    loaded = asr_station.load_config(path)
    assert loaded == config
    assert loaded.effective_enter_url() == "http://192.168.50.20/api/maintenance/enter?mode=isp"
    import json
    assert set(json.loads(path.read_text(encoding="utf-8"))) == set(StationConfig.__dataclass_fields__)


def test_config_rejects_bad_values():
    for bad in (StationConfig(host="bad host"), StationConfig(port=0), StationConfig(enter_url="ftp://x"),
                StationConfig(token_env="1abc"), StationConfig(label="x(y)")):
        with pytest.raises(ValueError):
            bad.validate()


def test_summarize_enter_picks_firmware_fields():
    reply = {"ok": True, "maintenance": {"mode": "isp", "lastAppAck": True, "caught": True, "lastCatchMs": 135}}
    assert asr_station.summarize_enter(reply) == {"lastAppAck": True, "caught": True, "lastCatchMs": 135}


class _FakeBridge:
    created = []

    def __init__(self, endpoint, host, port, **kwargs):
        self.kwargs = kwargs
        self.state = "starting"
        self.moved = 0
        _FakeBridge.created.append(self)

    def start(self):
        self.state = "waiting_app"

    def stop(self):
        if self.state not in ("needs_reset",):
            self.state = "stopped"

    def snapshot(self):
        return {"state": self.state, "app_to_device": self.moved, "device_to_app": 0, "settings": {},
                "last_error": "", "message": ""}

    def finish(self):
        self.kwargs["before_connect"]()
        self.state = "active"
        self.moved = 1024
        self.state = "needs_reset"


def _wait(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_station_rearms_after_each_session():
    _FakeBridge.created = []
    calls = []

    def enter():
        calls.append(1)
        return {"ok": True, "maintenance": {"lastAppAck": True, "caught": True}}

    station = FlashStation(StationConfig(host="192.168.50.20"), "CNCB0", "COM11",
                           bridge_factory=_FakeBridge, enter=enter)
    station.start()
    try:
        assert _wait(lambda: len(_FakeBridge.created) == 1)
        first = _FakeBridge.created[0]
        assert first.kwargs["connect_on_app_open"] is True
        first.finish()
        assert _wait(lambda: len(_FakeBridge.created) == 2)
        snap = station.snapshot()
        assert snap["sessions"] == 1 and snap["last_result"] == "done"
        assert snap["last_prepare"] == {"lastAppAck": True, "caught": True}
        assert calls == [1]
    finally:
        station.stop()
    assert station.snapshot()["state"] == "stopped"


def test_station_reports_uncaught_target():
    _FakeBridge.created = []
    station = FlashStation(StationConfig(host="192.168.50.20"), "CNCB0", "COM11", bridge_factory=_FakeBridge,
                           enter=lambda: {"maintenance": {"lastAppAck": False, "caught": False}})
    station.start()
    try:
        assert _wait(lambda: len(_FakeBridge.created) == 1)
        _FakeBridge.created[0].finish()
        assert _wait(lambda: station.snapshot()["sessions"] == 1)
        assert station.snapshot()["last_prepare"]["caught"] is False
    finally:
        station.stop()


def test_station_requires_host():
    with pytest.raises(ValueError):
        FlashStation(StationConfig(), "CNCB0", "COM11", bridge_factory=_FakeBridge, enter=dict)
