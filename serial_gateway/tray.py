"""Notification-area icon (pystray). Callbacks run on the tray thread; callers post to Tk."""
from __future__ import annotations

import threading
from typing import Callable

import pystray
from PIL import Image, ImageDraw

_COLORS = {"ready": (15, 138, 95), "preparing": (31, 111, 178), "flashing": (31, 111, 178),
           "setup": (178, 106, 0), "error": (181, 62, 62)}
_STATE_TEXT = {"ready": "就绪", "preparing": "正在进入下载模式", "flashing": "正在烧录", "setup": "还差一步",
               "error": "出错了", "stopped": "已暂停", "checking": "正在检查"}


def _icon(color: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(23, 54, 80, 255))
    draw.polygon([(36, 10), (20, 36), (31, 36), (27, 54), (44, 26), (33, 26)], fill=(255, 255, 255, 255))
    draw.ellipse((42, 42, 60, 60), fill=color + (255,), outline=(255, 255, 255, 255), width=3)
    return image


class Tray:
    def __init__(self, title: str, *, on_show: Callable[[], None], on_quit: Callable[[], None],
                 translate: Callable[..., str]) -> None:
        self._title, self._t = title, translate
        self._state, self._com = "", ""
        self._notified = False
        menu = pystray.Menu(
            pystray.MenuItem(lambda _item: self._status_line(), None, enabled=False),
            pystray.MenuItem(translate("打开窗口"), lambda: on_show(), default=True),
            pystray.MenuItem(translate("退出"), lambda: on_quit()),
        )
        self._icon = pystray.Icon("asr-wireless-flash", _icon((138, 151, 166)), title, menu)
        self._thread: threading.Thread | None = None

    def _status_line(self) -> str:
        text = self._t(_STATE_TEXT.get(self._state, "正在检查"))
        return f"{self._com} · {text}" if self._com else text

    def start(self) -> None:
        self._thread = threading.Thread(target=self._icon.run, name="tray", daemon=True)
        self._thread.start()

    def update(self, state: str, com: str) -> None:
        if (state, com) == (self._state, self._com):
            return
        self._state, self._com = state, com
        try:
            self._icon.icon = _icon(_COLORS.get(state, (138, 151, 166)))
            self._icon.title = f"{self._title} — {self._status_line()}"
            self._icon.update_menu()
        except Exception:  # noqa: BLE001 - cosmetic
            pass

    def notify_once(self, message: str) -> None:
        if self._notified:
            return
        self._notified = True
        try:
            self._icon.notify(message, self._title)
        except Exception:  # noqa: BLE001 - not all shells support balloons
            pass

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001
            pass
