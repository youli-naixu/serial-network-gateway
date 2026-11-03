"""ASR 无线烧录助手: the single-page main window.

Shows one thing a beginner needs: which COM to pick in the flashing tool and
whether everything is ready. Everything else lives in the 高级工具 window.
Closing the window hides it to the tray; the tray menu quits.
"""
from __future__ import annotations

import os
import queue
import socket
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font, messagebox, ttk
from typing import Any

from . import asr_station, desktop
from ._gui_runtime import Reply, clean_text, run_background
from .i18n import Translator

TITLE = "ASR 无线烧录助手"
REPO_URL = "https://github.com/youli-naixu/serial-network-gateway"
_BG, _CARD, _NAVY, _MUTED = "#F2F5F9", "#FFFFFF", "#173650", "#607286"
_GREEN, _BLUE, _AMBER, _RED, _GREY = "#0F8A5F", "#1F6FB2", "#B26A00", "#B53E3E", "#8A97A6"
_STATE_LOOK = {
    "ready": (_GREEN, "就绪", "在天问Block 里选择下面这个串口，然后点“下载”。"),
    "preparing": (_BLUE, "正在让 ASR 进入下载模式…", "通常不到 1 秒。"),
    "flashing": (_BLUE, "正在烧录…", "请等烧录软件提示完成，期间不要断开 ESP32。"),
    "setup": (_AMBER, "还差一步", "按下面“准备情况”里标红的那一项操作。"),
    "error": (_RED, "出错了", ""),
    "stopped": (_GREY, "已暂停", "点“开始”恢复待命。"),
    "checking": (_GREY, "正在检查…", ""),
}
_RESULT_TEXT = {
    "done": ("烧录成功", _GREEN),
    "closed_without_data": ("没有开始（烧录软件打开串口后就关了）", _AMBER),
    "target_not_caught": ("ASR 没进入下载模式", _RED),
}


class AssistantApp:
    def __init__(self, root: tk.Tk, *, start_minimized: bool = False, instance: Any = None) -> None:
        self.root = root
        self._t = Translator()
        self._instance = instance
        self._replies: queue.Queue[Reply] = queue.Queue()
        self._pending: set[str] = set()
        self._config = asr_station.load_config()
        self._station: asr_station.FlashStation | None = None
        self._paused = False
        self._quitting = False
        self._c0c_installed: bool | None = None
        self._pair: dict[str, Any] | None = None
        self._esp_ok: bool | None = None
        self._esp_detail = ""
        self._advanced: Any = None
        self._tray: Any = None
        self._last_error = ""
        self._history_key: Any = None
        self._setup_style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._start_tray()
        if start_minimized and self._tray is not None:
            self.root.withdraw()
        self.root.after(50, self._poll)
        self.root.after(200, self.refresh_checks)

    # ------------------------------------------------------------ layout
    def _setup_style(self) -> None:
        root = self.root
        root.title(self._t(TITLE))
        icon = Path(__file__).with_name("assets") / "app.ico"
        if os.name == "nt" and icon.is_file():
            try:
                root.iconbitmap(default=str(icon))  # also used by dialogs and the advanced window
            except tk.TclError:
                pass
        root.geometry("600x700")
        root.minsize(500, 560)
        root.configure(background=_BG)
        family = "Microsoft YaHei UI" if os.name == "nt" else "TkDefaultFont"
        self._family = family
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font.nametofont(name).configure(family=family, size=10)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=(family, 10), foreground=_NAVY, background=_BG)
        style.configure("TFrame", background=_BG)
        style.configure("Card.TFrame", background=_CARD)
        style.configure("TLabel", background=_BG, foreground=_NAVY)
        style.configure("Card.TLabel", background=_CARD)
        style.configure("Muted.TLabel", background=_CARD, foreground=_MUTED, font=(family, 9))
        style.configure("Head.TLabel", background=_CARD, font=(family, 11, "bold"))
        style.configure("Big.TLabel", background=_CARD, font=(family, 18, "bold"))
        style.configure("Com.TLabel", background=_CARD, font=(family, 22, "bold"))
        style.configure("TButton", background="#E6ECF3", padding=(10, 6), borderwidth=0)
        style.map("TButton", background=[("active", "#D8E4ED")], foreground=[("disabled", "#95A1AE")])
        style.configure("Primary.TButton", background=_NAVY, foreground="white", padding=(16, 7))
        style.map("Primary.TButton", background=[("disabled", "#CCD6DF"), ("active", "#245774")],
                  foreground=[("disabled", "#687C8D"), ("!disabled", "white")])
        style.configure("Small.TButton", background="#E6ECF3", padding=(8, 4), borderwidth=0)
        style.map("Small.TButton", background=[("active", "#D8E4ED")])
        style.configure("Link.TButton", background=_CARD, foreground=_BLUE, padding=(4, 2))
        style.map("Link.TButton", background=[("active", "#EEF4FA")])
        style.configure("TCheckbutton", background=_CARD)
        style.map("TCheckbutton", background=[("active", _CARD)])
        style.configure("TEntry", padding=5, fieldbackground=_CARD)

    @staticmethod
    def _wrap_to(container: tk.Misc, label: ttk.Label, inset: int) -> None:
        """Wrap ``label`` to the container's real width (DPI scaling changes it)."""
        container.bind("<Configure>", lambda event: label.configure(wraplength=max(160, event.width - inset)), add="+")

    def _card(self, row: int, pady: tuple[int, int] = (0, 10)) -> ttk.Frame:
        card = ttk.Frame(self._outer, style="Card.TFrame", padding=(16, 12))
        card.grid(row=row, column=0, sticky="nsew", pady=pady)
        card.columnconfigure(1, weight=1)
        return card

    def _build(self) -> None:
        tr = self._t
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer = ttk.Frame(self.root, padding=(16, 12, 16, 12))
        outer.grid(sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        self._outer = outer

        bar = ttk.Frame(outer)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        bar.columnconfigure(4, weight=1)
        ttk.Label(bar, text=tr(TITLE), font=(self._family, 15, "bold")).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 6))
        for column, (text, command) in enumerate((("接线说明", self.show_wiring), ("示例代码", self.open_examples),
                                                  ("设置", self.show_settings), ("高级工具", self.show_advanced))):
            ttk.Button(bar, text=tr(text), style="Small.TButton", command=command).grid(row=1, column=column, padx=(0, 6), sticky="w")

        status = self._card(1)
        self._dot = tk.Canvas(status, width=22, height=22, highlightthickness=0, background=_CARD)
        self._dot.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 10), pady=(6, 0))
        self._dot_item = self._dot.create_oval(3, 3, 19, 19, fill=_GREY, outline="")
        self._state_var = tk.StringVar(self.root)
        self._hint_var = tk.StringVar(self.root)
        ttk.Label(status, textvariable=self._state_var, style="Big.TLabel").grid(row=0, column=1, sticky="w")
        self._hint = ttk.Label(status, textvariable=self._hint_var, style="Card.TLabel", justify="left")
        self._wrap_to(status, self._hint, 70)
        self._hint.grid(row=1, column=1, sticky="w", pady=(2, 8))
        com_row = ttk.Frame(status, style="Card.TFrame")
        com_row.grid(row=2, column=0, columnspan=2, sticky="ew")
        com_row.columnconfigure(1, weight=1)
        self._com_var = tk.StringVar(self.root, "—")
        self._label_var = tk.StringVar(self.root)
        ttk.Label(com_row, textvariable=self._com_var, style="Com.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(com_row, textvariable=self._label_var, style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self._copy_button = ttk.Button(com_row, text=tr("复制"), command=self._copy_com)
        self._copy_button.grid(row=0, column=2, sticky="e")
        self._progress_var = tk.StringVar(self.root)
        self._progress = ttk.Label(status, textvariable=self._progress_var, style="Muted.TLabel")
        self._progress.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._progress.grid_remove()

        checks = self._card(2)
        checks.columnconfigure(1, weight=1)
        ttk.Label(checks, text=tr("准备情况"), style="Head.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self._recheck_button = ttk.Button(checks, text=tr("重新检查"), command=self.refresh_checks)
        self._recheck_button.grid(row=0, column=2, sticky="e")
        self._check_rows: dict[str, tuple[tk.StringVar, tk.StringVar, ttk.Label, ttk.Button]] = {}
        for row, key in enumerate(("driver", "port", "esp", "asr"), start=1):
            mark, text = tk.StringVar(self.root), tk.StringVar(self.root)
            mark_label = ttk.Label(checks, textvariable=mark, style="Card.TLabel", font=(self._family, 11, "bold"), width=2)
            mark_label.grid(row=row, column=0, sticky="nw", pady=(6, 0))
            text_label = ttk.Label(checks, textvariable=text, style="Card.TLabel", justify="left")
            text_label.grid(row=row, column=1, sticky="w", pady=(6, 0))
            self._wrap_to(checks, text_label, 170)
            fix = ttk.Button(checks, style="Link.TButton")
            fix.grid(row=row, column=2, sticky="e", pady=(6, 0))
            self._check_rows[key] = (mark, text, mark_label, fix)

        history = self._card(3)
        history.rowconfigure(1, weight=1)
        history.columnconfigure(0, weight=1)
        ttk.Label(history, text=tr("烧录记录"), style="Head.TLabel").grid(row=0, column=0, sticky="w")
        self._history = tk.Text(history, height=5, wrap="word", relief="flat", background=_CARD, foreground=_NAVY,
                                font=(self._family, 9), padx=0, pady=4, cursor="arrow")
        self._history.grid(row=1, column=0, sticky="nsew")
        for name, color in (("ok", _GREEN), ("warn", _AMBER), ("bad", _RED), ("muted", _MUTED)):
            self._history.tag_configure(name, foreground=color)
        self._history.configure(state="disabled")

        actions = ttk.Frame(outer)
        actions.grid(row=4, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self._toggle_button = ttk.Button(actions, style="Primary.TButton", command=self.toggle)
        self._toggle_button.grid(row=0, column=1, sticky="e")
        self._render()

    # ------------------------------------------------------------ background work
    def _run(self, task: str, work: Any) -> None:
        if task in self._pending:
            return
        self._pending.add(task)
        run_background(task, work, self._replies)

    def refresh_checks(self) -> None:
        config = self._config

        def work() -> dict[str, Any]:
            from .vcom import Com0comBackend
            backend = Com0comBackend()
            pairs = backend.pairs() if backend.setupc else []
            esp_ok, detail = None, ""
            if config.host:
                try:
                    # Port 80 (its web server) is always up; the flashing port only opens in download mode.
                    with socket.create_connection((config.host, 80), timeout=2.5):
                        esp_ok = True
                except OSError as exc:
                    esp_ok, detail = False, f"{type(exc).__name__}: {exc}"
            return {"installed": bool(backend.setupc), "pairs": pairs, "esp_ok": esp_ok, "esp_detail": detail}
        self._run("checks", work)
        self._render()

    def prepare_port(self) -> None:
        if self._station is not None:
            self._stop_station()
        label = self._config.label
        if not messagebox.askokcancel(
                self._t("准备虚拟串口"),
                self._t("将准备一个名为“{label}”的虚拟串口，让天问Block 能在串口列表里看到它。\n\n点“确定”后 Windows 会弹出一次管理员确认，请点“是”。整个过程最长约 2 分钟。", label=label),
                parent=self.root):
            return

        def work() -> dict[str, Any]:
            from .vcom import Com0comBackend
            backend = Com0comBackend()
            if not backend.setupc:
                raise RuntimeError("com0com is not installed.")
            reuse = asr_station.reusable_pair(backend.pairs())
            return backend.label_port(reuse["pair"] if reuse else None, label)
        self._run("prepare", work)
        self._render()

    def _start_station(self) -> None:
        if self._station is not None or self._pair is None or not self._config.host or self._quitting:
            return
        try:
            station = asr_station.FlashStation(self._config, self._pair["endpoint"], self._pair["com"])
        except ValueError as exc:
            self._last_error = str(exc)
            return
        station.start()
        self._station = station
        self._last_error = ""

    def _stop_station(self) -> None:
        station, self._station = self._station, None
        if station is not None:
            run_background("station_stop", lambda: station.stop(timeout=3.0), self._replies)

    def toggle(self) -> None:
        if self._station is not None:
            self._paused = True
            self._stop_station()
        else:
            self._paused = False
            if self._pair is None or self._c0c_installed is None:
                self.refresh_checks()
            else:
                self._start_station()
        self._render()

    def _handle(self, reply: Reply) -> None:
        self._pending.discard(reply.task)
        result = reply.result if isinstance(reply.result, dict) else {}
        if reply.task == "checks":
            if reply.error:
                self._last_error = reply.error
            else:
                self._c0c_installed = result["installed"]
                self._pair = asr_station.find_station_pair(result["pairs"], self._config.label)
                self._esp_ok, self._esp_detail = result["esp_ok"], result["esp_detail"]
                if self._station is not None and (self._pair is None or self._pair["com"] != self._station._status.com):
                    self._stop_station()
                if not self._paused:
                    self._start_station()
        elif reply.task == "prepare":
            if reply.error or not result.get("ok"):
                self._last_error = reply.error or str(result.get("error") or "prepare failed")
            else:
                self._last_error = ""
            self.refresh_checks()
        self._render()

    def _poll(self) -> None:
        for _ in range(30):
            try:
                reply = self._replies.get_nowait()
            except queue.Empty:
                break
            if reply.task == "show":
                self.show()
            elif reply.task == "quit":
                self.quit()
                return
            elif reply.task != "station_stop":
                self._handle(reply)
        if not self._quitting:
            self._render()
            self.root.after(300, self._poll)

    # ------------------------------------------------------------ rendering
    def _overall(self, snap: dict[str, Any] | None) -> tuple[str, str]:
        if "checks" in self._pending and self._c0c_installed is None:
            return "checking", ""
        if "prepare" in self._pending:
            return "setup", self._t("正在等待管理员确认并准备虚拟串口…")
        if not self._c0c_installed or self._pair is None or not self._config.host or self._esp_ok is False:
            return "setup", ""
        if snap is None:
            return ("stopped", "") if self._paused else ("checking", "")
        if snap.get("error"):
            return "error", self._friendly(snap["error"])
        state = snap.get("state")
        if state == "preparing":
            return "preparing", ""
        if state == "flashing":
            return "flashing", ""
        return "ready", ""

    def _set_check(self, key: str, ok: bool | None, text: str, fix: tuple[str, Any] | None = None) -> None:
        mark, label, mark_label, button = self._check_rows[key]
        mark.set("✓" if ok else ("…" if ok is None else "✗"))
        mark_label.configure(foreground=_GREEN if ok else (_GREY if ok is None else _RED))
        label.set(text)
        if fix:
            button.configure(text=self._t(fix[0]), command=fix[1])
            button.grid()
        else:
            button.grid_remove()

    def _render(self) -> None:
        tr = self._t
        snap = self._station.snapshot() if self._station is not None else None
        state, extra = self._overall(snap)
        color, title, hint = _STATE_LOOK[state]
        self._dot.itemconfigure(self._dot_item, fill=color)
        self._state_var.set(tr(title))
        message = extra or (self._friendly(self._last_error) if self._last_error else "") or tr(hint)
        self._hint_var.set(message)
        if self._pair:
            self._com_var.set(self._pair["com"])
            self._label_var.set(self._pair.get("label") or "")
            self._copy_button.state(["!disabled"])
        else:
            self._com_var.set("—")
            self._label_var.set(tr("虚拟串口还没准备"))
            self._copy_button.state(["disabled"])
        bridge = (snap or {}).get("bridge") or {}
        if state == "flashing":
            sent = int(bridge.get("app_to_device") or 0)
            baud = (bridge.get("settings") or {}).get("baudrate")
            self._progress_var.set(tr("已发送 {kb} KB", kb=f"{sent / 1024:,.0f}") + (f" · {baud:,} bps" if baud else ""))
            self._progress.grid()
        else:
            self._progress.grid_remove()
        self._render_checks(snap)
        self._recheck_button.state(["disabled"] if "checks" in self._pending else ["!disabled"])
        self._toggle_button.configure(text=tr("暂停") if self._station is not None else tr("开始"))
        self._toggle_button.state(["disabled"] if self._station is None and state == "setup" else ["!disabled"])
        self._render_history((snap or {}).get("history") or [])
        if self._tray is not None:
            self._tray.update(state, self._pair["com"] if self._pair else "")

    def _render_checks(self, snap: dict[str, Any] | None) -> None:
        tr = self._t
        installed = self._c0c_installed
        self._set_check("driver", installed, tr("虚拟串口驱动 com0com") + (tr("：未安装") if installed is False else ""),
                        ("怎么安装", self.show_driver_help) if installed is False else None)
        if installed is False:
            self._set_check("port", False, tr("虚拟串口：需要先安装驱动"))
        elif self._pair:
            self._set_check("port", True, tr("虚拟串口 {com}（{label}）", com=self._pair["com"], label=self._pair.get("label") or ""))
        else:
            fix = None if installed is None or "prepare" in self._pending else ("准备虚拟串口", self.prepare_port)
            self._set_check("port", None if installed is None else False, tr("虚拟串口：还没准备"), fix)
        host = self._config.host
        if not host:
            self._set_check("esp", False, tr("ESP32：还没填地址"), ("填写地址", self.show_settings))
        elif self._esp_ok is None:
            self._set_check("esp", None, tr("ESP32 {host}：检查中…", host=host))
        elif self._esp_ok:
            self._set_check("esp", True, tr("ESP32 {host} 在线", host=host))
        else:
            self._set_check("esp", False, tr("ESP32 {host} 连不上。", host=host) + self._friendly(self._esp_detail),
                            ("修改地址", self.show_settings))
        prepare = (snap or {}).get("last_prepare") or {}
        if not prepare:
            self._set_check("asr", None, tr("ASR：第一次烧录时检查"))
        elif prepare.get("caught"):
            self._set_check("asr", True, tr("ASR 能进入下载模式（{ms} ms）", ms=prepare.get("lastCatchMs", "?")))
        elif prepare.get("lastAppAck"):
            self._set_check("asr", False, tr("ASR 收到了重启命令，但下载口没响应。请检查 ESP32 GPIO9/10 与 ASR PB6/PB5 的接线，并拔掉 ASR 的 USB。"),
                            ("接线说明", self.show_wiring))
        else:
            self._set_check("asr", False, tr("ASR 没回应重启命令。请检查业务串口接线，或确认 ASR 程序里有 asr_isp。也可以手动给 ASR 重新上电再烧录。"),
                            ("接线说明", self.show_wiring))

    def _render_history(self, items: list[dict[str, Any]]) -> None:
        key = tuple((i.get("time"), i.get("result")) for i in items)
        if key == self._history_key:
            return
        self._history_key = key
        tr = self._t
        box = self._history
        box.configure(state="normal")
        box.delete("1.0", "end")
        if not items:
            box.insert("end", tr("还没有烧录过。"), "muted")
        for item in items:
            result = str(item.get("result") or "")
            if result in _RESULT_TEXT:
                text, color = _RESULT_TEXT[result]
                text = tr(text)
            else:
                text, color = tr("失败：") + self._friendly(result.removeprefix("error: ")), _RED
            tag = {_GREEN: "ok", _AMBER: "warn"}.get(color, "bad")
            box.insert("end", f"{item.get('time', '')}  ", "muted")
            box.insert("end", text, tag)
            if result == "done":
                seconds = item.get("seconds")
                box.insert("end", f"  {int(item.get('sent') or 0) / 1048576:.1f} MB" + (f" · {seconds:.0f} s" if seconds else ""), "muted")
            box.insert("end", "\n")
        box.configure(state="disabled")

    def _friendly(self, text: Any) -> str:
        return desktop.friendly_error(text, self._t.language)

    def _copy_com(self) -> None:
        if self._pair:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._pair["com"])

    # ------------------------------------------------------------ dialogs
    def _dialog(self, title: str, width: int = 520) -> tuple[tk.Toplevel, ttk.Frame]:
        top = tk.Toplevel(self.root)
        top.title(self._t(title))
        top.configure(background=_BG)
        top.transient(self.root)
        top.resizable(True, True)
        body = ttk.Frame(top, style="Card.TFrame", padding=(18, 14))
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(0, weight=1)
        top.minsize(width, 200)
        return top, body

    def show_settings(self) -> None:
        tr = self._t
        top, body = self._dialog("设置", 460)
        config = self._config
        values = {key: tk.StringVar(top, str(getattr(config, key))) for key in ("host", "label")}
        values["port"] = tk.StringVar(top, str(config.port))
        values["enter_url"] = tk.StringVar(top, config.enter_url)
        values["token_env"] = tk.StringVar(top, config.token_env)
        auto_flash = tk.BooleanVar(top, config.auto_start)
        boot = tk.BooleanVar(top, desktop.autostart_enabled())
        rows = (("host", "ESP32 地址", "例如 192.168.50.20"),
                ("label", "虚拟串口名字", "天问Block 串口列表里显示的名字"))
        row = 0
        for key, title, hint in rows:
            ttk.Label(body, text=tr(title), style="Card.TLabel").grid(row=row, column=0, sticky="w")
            ttk.Entry(body, textvariable=values[key]).grid(row=row + 1, column=0, sticky="ew")
            ttk.Label(body, text=tr(hint), style="Muted.TLabel").grid(row=row + 2, column=0, sticky="w", pady=(0, 8))
            row += 3
        ttk.Checkbutton(body, text=tr("打开程序时自动开始待命"), variable=auto_flash).grid(row=row, column=0, sticky="w")
        ttk.Checkbutton(body, text=tr("开机自动运行（缩在右下角托盘）"), variable=boot).grid(row=row + 1, column=0, sticky="w", pady=(2, 8))
        row += 2
        more = ttk.Frame(body, style="Card.TFrame")
        shown = tk.BooleanVar(top, False)
        for index, (key, title) in enumerate((("port", "ESP32 端口（默认 3333）"),
                                              ("enter_url", "进入下载模式的地址（留空自动使用 ESP32 地址）"),
                                              ("token_env", "令牌所在的环境变量名（ESP32 不要求令牌就留空）"))):
            ttk.Label(more, text=tr(title), style="Card.TLabel").grid(row=index * 2, column=0, sticky="w")
            ttk.Entry(more, textvariable=values[key]).grid(row=index * 2 + 1, column=0, sticky="ew", pady=(0, 6))
        more.columnconfigure(0, weight=1)

        def toggle_more() -> None:
            shown.set(not shown.get())
            if shown.get():
                more.grid(row=row + 1, column=0, sticky="ew")
            else:
                more.grid_remove()
        ttk.Button(body, text=tr("更多设置"), style="Link.TButton", command=toggle_more).grid(row=row, column=0, sticky="w")
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=row + 2, column=0, sticky="e", pady=(12, 0))

        def save() -> None:
            try:
                port = int(values["port"].get().strip())
            except ValueError:
                messagebox.showerror(tr("设置有误"), tr("ESP32 端口应为整数。"), parent=top)
                return
            new = asr_station.StationConfig(label=values["label"].get().strip(), host=values["host"].get().strip(),
                                            port=port, enter_url=values["enter_url"].get().strip(),
                                            token_env=values["token_env"].get().strip(),
                                            token_header=config.token_header, auto_start=auto_flash.get())
            try:
                asr_station.save_config(new)
            except (ValueError, OSError) as exc:
                messagebox.showerror(tr("设置有误"), self._friendly(exc) if isinstance(exc, OSError) else self._t.error(exc), parent=top)
                return
            try:
                if boot.get() != desktop.autostart_enabled():
                    desktop.set_autostart(boot.get())
            except (OSError, RuntimeError) as exc:
                messagebox.showwarning(tr("开机自动运行"), tr("没能修改开机自动运行：{error}", error=exc), parent=top)
            relabel = new.label != config.label
            moved = (new.host, new.port, new.enter_url, new.token_env) != (config.host, config.port, config.enter_url, config.token_env)
            self._config = new
            top.destroy()
            if relabel or moved:
                self._stop_station()
                self._pair = None if relabel else self._pair
                self._esp_ok = None
            self.refresh_checks()
            if relabel:
                self.prepare_port()
        ttk.Button(buttons, text=tr("取消"), command=top.destroy).grid(row=0, column=0)
        ttk.Button(buttons, text=tr("保存"), style="Primary.TButton", command=save).grid(row=0, column=1, padx=(8, 0))
        top.grab_set()

    def _text_dialog(self, title: str, text: str, links: tuple[tuple[str, Any], ...] = ()) -> None:
        top, body = self._dialog(title, 560)
        body.rowconfigure(0, weight=1)
        box = tk.Text(body, wrap="char", relief="flat", background=_CARD, foreground=_NAVY,
                      font=(self._family, 10), height=22, width=64, padx=2, pady=2)
        box.grid(row=0, column=0, sticky="nsew")
        box.tag_configure("h", font=(self._family, 11, "bold"), spacing1=8, spacing3=2)
        box.tag_configure("mono", font=("Consolas", 10), background="#F3F6FA")
        for line in text.splitlines():
            if line.startswith("## "):
                box.insert("end", line[3:] + "\n", "h")
            elif line.startswith("    "):
                box.insert("end", line[4:] + "\n", "mono")
            else:
                box.insert("end", line + "\n")
        box.configure(state="disabled")
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=1, column=0, sticky="e", pady=(10, 0))
        for column, (label, command) in enumerate(links):
            ttk.Button(buttons, text=self._t(label), command=command).grid(row=0, column=column, padx=(0, 8))
        ttk.Button(buttons, text=self._t("关闭"), style="Primary.TButton", command=top.destroy).grid(row=0, column=len(links))

    def show_wiring(self) -> None:
        from .guides import guide
        self._text_dialog("接线说明", guide("wiring", self._t.language), (("在网页上查看", lambda: webbrowser.open(REPO_URL + ("/blob/main/docs/wiring.md" if self._t.language == "en" else "/blob/main/docs/wiring.zh-CN.md"))),))

    def show_driver_help(self) -> None:
        from .guides import guide
        self._text_dialog("安装 com0com", guide("driver", self._t.language), (("打开下载页", lambda: webbrowser.open("https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/")),
                                                     ("装好了，重新检查", self.refresh_checks)))

    def open_examples(self) -> None:
        from .guides import examples_dir, guide
        folder = examples_dir()
        links: list[tuple[str, Any]] = [("在网页上查看", lambda: webbrowser.open(REPO_URL + "/tree/main/examples"))]
        if folder is not None:
            links.insert(0, ("打开示例文件夹", lambda: os.startfile(str(folder))))  # noqa: S606 - local folder
        self._text_dialog("示例代码", guide("examples", self._t.language), tuple(links))

    def show_advanced(self) -> None:
        if self._advanced is not None:
            try:
                self._advanced.root.deiconify()
                self._advanced.root.lift()
                return
            except tk.TclError:
                self._advanced = None
        from .gui import _GatewayApp
        top = tk.Toplevel(self.root)

        def closed() -> None:
            self._advanced = None
            self.refresh_checks()
        self._advanced = _GatewayApp(top, None, None, on_closed=closed)

    # ------------------------------------------------------------ window / tray
    def show(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        if self._tray is None:
            self.quit()
            return
        self.root.withdraw()
        self._tray.notify_once(self._t("ASR 无线烧录助手仍在后台待命，可在右下角托盘图标里打开或退出。"))

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        station, self._station = self._station, None
        if station is not None:
            station.stop(timeout=3.0)
        if self._tray is not None:
            self._tray.stop()
        if self._instance is not None:
            self._instance.release()
        self.root.destroy()

    def _start_tray(self) -> None:
        try:
            from .tray import Tray
        except ImportError:
            self._tray = None
            return
        post = self._replies.put
        try:
            self._tray = Tray(self._t(TITLE), on_show=lambda: post(Reply("show")), on_quit=lambda: post(Reply("quit")),
                              translate=self._t)
            self._tray.start()
        except Exception:  # noqa: BLE001 - the window still works without a tray icon
            self._tray = None


def main(start_minimized: bool = False) -> None:
    holder: dict[str, Any] = {}
    instance = desktop.SingleInstance(lambda: holder["replies"].put(Reply("show")) if "replies" in holder else None)
    if not instance.acquire():
        return
    root = tk.Tk()
    app = AssistantApp(root, start_minimized=start_minimized, instance=instance)
    holder["replies"] = app._replies
    try:
        root.mainloop()
    finally:
        instance.release()
