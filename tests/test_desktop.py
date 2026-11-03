import time

import pytest

from serial_gateway import desktop, guides
from serial_gateway.asr_station import FlashStation, StationConfig


@pytest.mark.parametrize("raw, expected", [
    ("OSError (code 5)", "占用"),
    ("PermissionError: [Errno 13]", "占用"),
    ("FileNotFoundError (code 2)", "找不到虚拟串口"),
    ("ConnectionRefusedError (code 10061)", "拒绝连接"),
    ("TimeoutError: timed out", "超时"),
    ("RuntimeError: enter request failed (HTTP 409, relay_busy)", "正忙"),
    ("RuntimeError: com0com is not installed.", "com0com"),
])
def test_friendly_error_maps_known_failures(raw, expected):
    assert expected in desktop.friendly_error(raw)


def test_friendly_error_does_not_overmatch_codes():
    assert desktop.friendly_error("OSError (code 50)").startswith("出现了意外错误")
    assert desktop.friendly_error("") == ""


def test_friendly_error_english():
    assert desktop.friendly_error("OSError (code 5)", "en").startswith("The virtual COM is in use")
    assert desktop.friendly_error("weird", "en") == "Unexpected error: weird"


def test_single_instance_rings_first_copy():
    shown = []
    first = desktop.SingleInstance(lambda: shown.append(1), port=47898)
    assert first.acquire()
    try:
        assert not desktop.SingleInstance(lambda: None, port=47898).acquire()
        deadline = time.monotonic() + 2
        while not shown and time.monotonic() < deadline:
            time.sleep(0.02)
        assert shown == [1]
    finally:
        first.release()
    again = desktop.SingleInstance(lambda: None, port=47898)
    assert again.acquire()
    again.release()


def test_guides_have_both_languages():
    for name in ("wiring", "driver", "examples"):
        zh, en = guides.guide(name, "zh-CN"), guides.guide(name, "en")
        assert zh.startswith("## ") and en.startswith("## ") and zh != en
    assert "GPIO9" in guides.guide("wiring", "en") and "PB6" in guides.guide("wiring", "zh-CN")


class _Bridge:
    def __init__(self, *args, **kwargs):
        self.kwargs, self.state, self.error = kwargs, "starting", ""

    def start(self):
        self.state = "waiting_app"

    def stop(self):
        pass

    def snapshot(self):
        return {"state": self.state, "app_to_device": 2048 if self.kwargs.get("_ran") else 0,
                "device_to_app": 10, "settings": {}, "last_error": self.error, "message": ""}


def test_station_keeps_history_and_ignores_pre_session_errors():
    made = []

    def factory(*args, **kwargs):
        bridge = _Bridge(*args, **kwargs)
        made.append(bridge)
        return bridge

    station = FlashStation(StationConfig(host="192.168.50.20"), "CNCB0", "COM8", bridge_factory=factory,
                           enter=lambda: {"maintenance": {"lastAppAck": True, "caught": True, "lastCatchMs": 120}})
    station.start()
    try:
        deadline = time.monotonic() + 3
        while not made and time.monotonic() < deadline:
            time.sleep(0.02)
        made[0].error, made[0].state = "OSError (code 5)", "needs_reset"   # port busy before any tool opened it
        while station.snapshot()["state"] != "error" and time.monotonic() < deadline:
            time.sleep(0.02)
        snap = station.snapshot()
        assert snap["state"] == "error" and snap["history"] == [] and snap["sessions"] == 0
        while len(made) < 2 and time.monotonic() < deadline + 4:
            time.sleep(0.05)
        second = made[1]
        second.kwargs["before_connect"]()
        second.kwargs["_ran"] = True
        second.state = "needs_reset"
        while station.snapshot()["sessions"] < 1 and time.monotonic() < deadline + 6:
            time.sleep(0.02)
        snap = station.snapshot()
        assert snap["error"] == ""
        assert snap["history"][0]["result"] == "done" and snap["history"][0]["sent"] == 2048
        assert snap["history"][0]["prepare"]["caught"] is True
    finally:
        station.stop()
