"""串口网络网关的标准库 Tkinter 界面。导入与打开界面不会启动网关。"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import tempfile
import time
import tkinter as tk
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font, messagebox, simpledialog, ttk
from typing import Any

from ._gui_runtime import (
    GatewayWorker, Reply, SERIAL_FIELDS, bridge_args, bridge_settings_text, build_vcom_plan, clean_text,
    default_token_header, device_check_args, diagnostic_document, extend_diagnostics, format_device_report,
    normalize_com, pair_label, run_background, start_auxiliary,
)
from .i18n import LANGUAGES, Translator, save_language
from .locales import HELP_ZH
from .models import GatewayProfile
from .profiles import ProfileStore


_TITLE = "高级工具 · 串口网络网关"
_MODES = {"本机模拟": "simulator", "远端设备": "device"}
_PROTOCOLS = {"RFC2217": "rfc2217", "原始 TCP": "raw"}
_STATES = {
    "stopped": ("已停止", "#63758A"),
    "starting": ("正在启动", "#146A91"),
    "ready": ("等待客户端", "#087F83"),
    "connecting": ("正在连接远端", "#146A91"),
    "active": ("已连接", "#087F83"),
    "needs_reset": ("需要重新就绪", "#A45B08"),
    "error": ("运行出错", "#B53E3E"),
}
_BG = "#F2F5F9"
_CARD = "#FFFFFF"
_NAVY = "#173650"
_TEAL = "#087F83"
_MUTED = "#607286"
_RED = "#B53E3E"
_RESET_HELP = "先关闭官方软件的 COM，再重新就绪；网络断线不自动续传。"
_BRIDGE_RESET_HELP = "桥接会话已结束（应用关闭了 COM，或网络断开）。点击“重新开始”开始新会话；断线不自动续传。"
_HWVSP_LIMIT = "HW VSP 不转发 DTR/RTS，打开后无法切到高于 57600 的波特率；需要这些功能请用 com0com 桥接。"
_EXT_TASKS = {
    "vcom_detect": "正在检测虚拟 COM 驱动", "vcom_apply": "正在应用 HW VSP 配置",
    "pair_create": "正在新建端口对", "pair_remove": "正在删除端口对", "pair_wire": "正在配置关闭检测",
    "bridge_start": "正在启动桥接", "bridge_stop": "正在停止桥接", "bridge_restart": "正在重新开始桥接",
    "bridge_shutdown": "正在停止桥接", "device_check": "正在检测设备", "device_scan": "正在扫描局域网",
}
_BRIDGE_STATES = {
    "stopped": ("已停止", "#63758A"),
    "starting": ("正在启动", "#146A91"),
    "connecting": ("正在连接远端", "#146A91"),
    "active": ("已连接", "#087F83"),
    "needs_reset": ("需要重新开始", "#A45B08"),
    "error": ("运行出错", "#B53E3E"),
}
_BACKEND_NOTES = {
    "Not installed. Download HW VSP3 Single from the vendor and install it with administrator rights.": "未安装。请从厂商下载 HW VSP3 Single，并以管理员权限安装。",
    "setupc.exe not found.": "未找到 setupc.exe（com0com 未安装）。",
    "No port pair yet. Create one to use full control-line and baud forwarding.": "尚无端口对。新建一个即可使用完整的控制线与波特率转发。",
    "Vendor sample channel is still configured.": "仍是厂商示例通道配置。",
}
_MATRIX_ZH = {
    "binary data": "二进制数据", "settings at open": "打开时的串口设置", "baud change after open": "打开后改波特率",
    "data bits / parity / stop change": "改数据位 / 校验 / 停止位", "DTR / RTS": "DTR / RTS", "BREAK / PURGE": "BREAK / PURGE",
    "statistics and session lock": "统计与会话锁", "yes": "支持", "no": "不支持", "<=57600 only": "仅 ≤57600",
    "yes (incl. 1.5M)": "支持（含 1.5M）", "only with a baud change": "仅随波特率变化", "not yet": "暂不支持",
    "via local gateway": "经本机网关", "built in": "内置",
}


def _short(value: Any, limit: int = 160) -> str:
    return clean_text(value, limit).replace("\r", " ").replace("\n", " ")


def _byte_count(value: Any) -> str:
    try:
        return f"{max(0, int(value)):,} B"
    except (TypeError, ValueError, OverflowError):
        return "—"


def _address(host: Any, port: Any) -> str:
    host = str(host or "127.0.0.1")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def _serial_text(value: Any, language: str = "zh-CN") -> str:
    tr = Translator(language)
    if not isinstance(value, dict) or not value:
        return tr("等待客户端设置")
    parts = []
    labels_seen = set()
    for key, label in SERIAL_FIELDS:
        item = value.get(key)
        if item is None or label in labels_seen:
            continue
        labels_seen.add(label)
        if isinstance(item, bool):
            text = "开" if item else "关"
        else:
            text = _short(item, 32)
            if key == "parity":
                text = {"N": "无", "E": "偶", "O": "奇", "M": "标记", "S": "空格"}.get(text, text)
        parts.append(f"{tr(label)} {tr(text)}")
    return "  ·  ".join(parts) if parts else tr("等待客户端设置")


class _DisplayVar(tk.StringVar):
    def __init__(self, app: "_GatewayApp", value: str = "") -> None:
        self.app = app
        self.source = value
        super().__init__(app.root, value=app._t.event(value))

    def set(self, value: Any) -> None:
        self.source = str(value)
        super().set(self.app._t.event(value))

    def refresh(self) -> None:
        super().set(self.app._t.event(self.source))


class _Dialogs:
    """Translate dialog presentation while leaving selected filenames/names intact."""
    def __init__(self, app: "_GatewayApp", module: Any) -> None:
        self.app, self.module = app, module

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            args = tuple(self.app._t.event(value) if isinstance(value, str) else value for value in args)
            for key in ("title", "message", "prompt"):
                if key in kwargs:
                    kwargs[key] = self.app._t.event(kwargs[key])
            if "filetypes" in kwargs:
                kwargs["filetypes"] = [(self.app._t(label), pattern) for label, pattern in kwargs["filetypes"]]
            return getattr(self.module, name)(*args, **kwargs)
        return call


class _ScrollablePage(ttk.Frame):
    """高 DPI 或小窗口下只滚动当前页，底部服务按钮始终可见。"""

    def __init__(self, master: ttk.Notebook) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background=_CARD, highlightthickness=0, borderwidth=0, width=1, height=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = ttk.Frame(self.canvas, style="Card.TFrame", padding=(16, 12))
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.canvas.bind("<Configure>", self._layout)
        self.content.bind("<Configure>", self._layout)

    def _layout(self, _event: tk.Event | None = None) -> None:
        if getattr(self, "_layout_pending", False):
            return
        self._layout_pending = True
        # Coalesce: wrapped labels re-flow after a width change, so the final
        # requested height is only known once pending geometry work settles.
        self.after_idle(self._apply_layout)

    def _apply_layout(self) -> None:
        self._layout_pending = False
        width = max(1, self.canvas.winfo_width())
        viewport = max(1, self.canvas.winfo_height())
        # Fix only the width; the window item then follows the content's own
        # requested height, so the scroll region can shrink as well as grow.
        self.canvas.itemconfigure(self._window, width=width)
        self.update_idletasks()
        needed = self.content.winfo_reqheight()
        height = max(viewport, needed)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        if needed > viewport:
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()
            self.canvas.yview_moveto(0)

    def bind_navigation(self) -> None:
        def attach(widget: tk.Misc) -> None:
            if not isinstance(widget, (tk.Text, ttk.Combobox, tk.Listbox, ttk.Treeview)):
                widget.bind("<MouseWheel>", self._wheel, add="+")
                widget.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-3, "units"), add="+")
                widget.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(3, "units"), add="+")
            widget.bind("<FocusIn>", self._show_focus, add="+")
            for child in widget.winfo_children():
                attach(child)
        attach(self.content)
        self.canvas.bind("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, event: tk.Event) -> str:
        if self.content.winfo_height() > self.canvas.winfo_height() and event.delta:
            amount = -max(1, abs(event.delta) // 120) if event.delta > 0 else max(1, abs(event.delta) // 120)
            self.canvas.yview_scroll(amount, "units")
        return "break"

    def _show_focus(self, event: tk.Event) -> None:
        top = event.widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + event.widget.winfo_height()
        visible_top = self.canvas.canvasy(0)
        viewport = self.canvas.winfo_height()
        height = max(1, self.content.winfo_height())
        if top < visible_top:
            self.canvas.yview_moveto(max(0, top - 8) / height)
        elif bottom > visible_top + viewport:
            self.canvas.yview_moveto(max(0, bottom - viewport + 8) / height)


class _GatewayApp:
    def __init__(self, root: tk.Misc, config_path: str | None, language: str | None = None,
                 on_closed: Any = None) -> None:
        """``root`` may be a Toplevel; ``on_closed`` runs after the window is destroyed."""
        self.root = root
        self._on_closed = on_closed
        self._t = Translator(language)
        self._language_var = tk.StringVar(root, LANGUAGES[self._t.language])
        self._messagebox = _Dialogs(self, messagebox)
        self._filedialog = _Dialogs(self, filedialog)
        self._simpledialog = _Dialogs(self, simpledialog)
        self._modes = {self._t(label): code for label, code in _MODES.items()}
        self._protocols = {self._t(label): code for label, code in _PROTOCOLS.items()}
        self.store = ProfileStore(config_path)
        self._load_error: str | None = None
        self._protected_paths: set[Path] = set()
        try:
            self.profiles, selected = self.store.load()
        except Exception as exc:
            self._load_error = clean_text(exc)
            self._protected_paths.add(self.store.path.resolve())
            default = GatewayProfile()
            self.profiles, selected = [default], default.name
        self._disk_revision = self._revision(self.store.path)
        self._selected_name = selected
        self._base_profile = next(p for p in self.profiles if p.name == selected)
        self._form_baseline: dict[str, Any] = {}
        self._loading_form = False
        self._ui_ready = False
        self._running = False
        self._gateway_busy: str | None = None
        self._closing = False
        self._shutdown_complete = False
        self._close_deadline = 0.0
        self._close_warning_shown = False
        self._aux_pending: set[str] = set()
        self._snapshot_pending = False
        self._next_snapshot = 0.0
        self._snapshot: dict[str, Any] = {"state": "stopped", "events": []}
        self._run_profile: GatewayProfile | None = None
        self._ui_error = ""
        self._last_engine_events: list[tuple[str, str, str]] = []
        self._events: deque[tuple[str, str, str]] = deque(maxlen=160)
        self._events_dirty = False
        self._check_raw: dict[str, Any] = {}
        self._check_results = {"environment": "尚未执行环境检查。", "selftest": "尚未运行自检。"}
        self._editable: list[tuple[ttk.Widget, str]] = []
        self._profile_controls: list[ttk.Widget] = []
        self._wrap_labels: list[tuple[ttk.Label, int]] = []
        self._last_width = 0
        self._replies: queue.Queue[Reply] = queue.Queue()
        self.vars: dict[str, tk.Variable] = {
            key: tk.StringVar(root) for key in (
                "name", "mode", "protocol", "listen_host", "listen_port", "remote_host", "remote_port",
                "buffer_bytes", "chunk_bytes", "connect_timeout_s", "stall_timeout_s", "keepalive_s",
            )
        }
        self.vars["nodelay"] = tk.BooleanVar(root)
        self._selected_var = tk.StringVar(root)
        self._config_status = _DisplayVar(self)
        self._state_text = _DisplayVar(self, "已停止")
        self._endpoint_text = _DisplayVar(self)
        self._message_text = _DisplayVar(self, "选择配置后点击“启动网关”。")
        self._error_text = _DisplayVar(self)
        self._session_text = _DisplayVar(self, "会话 —  ·  接入 0  ·  断开 0  ·  拒绝 0")
        self._runtime_text = _DisplayVar(self, "尚未启动")
        self._task_text = _DisplayVar(self)
        self._vcom_endpoint = _DisplayVar(self)
        self._vcom_steps = _DisplayVar(self)
        self._serial_requested = _DisplayVar(self, "等待客户端设置")
        self._serial_confirmed = _DisplayVar(self, "等待客户端设置")
        self._metric_vars = {key: _DisplayVar(self, "0 B") for key in (
            "bytes_from_client", "bytes_to_remote", "bytes_from_remote", "bytes_to_client",
        )}
        self._buffer_vars = {key: _DisplayVar(self, "当前 0 B  /  峰值 0 B") for key in ("remote", "client")}
        self._init_extensions()
        self._configure_style()
        self._build_window()
        self._ui_ready = True
        self._apply_profile(self._base_profile)
        self._fill_extension_defaults()
        for variable in self.vars.values():
            variable.trace_add("write", self._form_changed)
        self._worker = GatewayWorker(self._replies)
        self.root.protocol("WM_DELETE_WINDOW", self._request_close)
        self.root.bind("<Configure>", self._on_resize, add="+")
        self.root.after(50, self._poll)
        if os.name == "nt":  # read-only driver detection; fills the pair list for the bridge tab
            self.root.after(400, self._refresh_vcom)
        if self._load_error:
            self.root.after_idle(self._show_initial_error)

    def _translated_check(self, task: str) -> str:
        if task in self._check_raw:
            report = self._environment_report if task == "environment" else self._selftest_report
            return report(self._check_raw[task])
        return self._t.event(self._check_results[task])

    def _change_language(self, _event: tk.Event | None = None) -> None:
        language = next((code for code, label in LANGUAGES.items() if label == self._language_var.get()), "zh-CN")
        if language == self._t.language:
            return
        self._set_language(language)
        try:
            save_language(language)
        except OSError:
            self._messagebox.showwarning("语言偏好未保存", "本次语言已切换，但无法保存偏好。下次启动可能恢复原语言。", parent=self.root)

    def _set_language(self, language: str) -> None:
        """Rebuild presentation only; keep edits, queue, worker and service alive."""
        selected_tab = self._notebook.index(self._notebook.select())
        mode = self._modes.get(str(self.vars["mode"].get()), "simulator")
        protocol = self._protocols.get(str(self.vars["protocol"].get()), "rfc2217")
        baseline_mode = self._modes.get(str(self._form_baseline.get("mode")), "simulator")
        baseline_protocol = self._protocols.get(str(self._form_baseline.get("protocol")), "rfc2217")
        self._loading_form = True
        self._ui_ready = False
        try:
            self._t = Translator(language)
            self._language_var.set(LANGUAGES[self._t.language])
            self.root.title(self._t(_TITLE))
            self._modes = {self._t(label): code for label, code in _MODES.items()}
            self._protocols = {self._t(label): code for label, code in _PROTOCOLS.items()}
            for key, mapping, code, baseline in (("mode", self._modes, mode, baseline_mode), ("protocol", self._protocols, protocol, baseline_protocol)):
                self.vars[key].set(next(label for label, value in mapping.items() if value == code))
                self._form_baseline[key] = next(label for label, value in mapping.items() if value == baseline)
            for child in self.root.winfo_children():
                if isinstance(child, tk.Toplevel):
                    child.destroy()
            self._outer.destroy()
            self._editable.clear()
            self._profile_controls.clear()
            self._wrap_labels.clear()
            self._last_width = 0
            self._build_window()
            self._profile_combo.configure(values=tuple(p.name for p in self.profiles))
            self._notebook.select(selected_tab)
            self._events_dirty = True
        finally:
            self._loading_form = False
            self._ui_ready = True
        self._form_changed()
        self._render_snapshot()
        self._render_events()
        self._render_checks()

    @staticmethod
    def _revision(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _configure_style(self) -> None:
        self.root.title(self._t(_TITLE))
        self.root.geometry("960x800")
        self.root.minsize(880, 680)
        self.root.configure(background=_BG)
        family = "Microsoft YaHei UI" if os.name == "nt" else "TkDefaultFont"
        if os.name != "nt":
            available = set(font.families(self.root))
            family = next((name for name in ("Microsoft YaHei UI", "Noto Sans CJK SC", "PingFang SC", "Arial") if name in available), "sans-serif")
        self._font_family = family
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font.nametofont(name).configure(family=family, size=10)
        self.root.option_add("*Font", (family, 10))
        self.style = ttk.Style(self.root)
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")
        self.style.configure(".", font=(family, 10), foreground=_NAVY, background=_BG)
        self.style.configure("TFrame", background=_BG)
        self.style.configure("Card.TFrame", background=_CARD)
        self.style.configure("TLabel", background=_BG, foreground=_NAVY)
        self.style.configure("Card.TLabel", background=_CARD)
        self.style.configure("Muted.TLabel", foreground=_MUTED)
        self.style.configure("CardMuted.TLabel", background=_CARD, foreground=_MUTED, font=(family, 9))
        self.style.configure("Title.TLabel", font=(family, 20, "bold"))
        self.style.configure("Heading.TLabel", background=_CARD, font=(family, 11, "bold"))
        self.style.configure("State.TLabel", background=_CARD, font=(family, 11, "bold"))
        self.style.configure("Error.TLabel", background=_CARD, foreground=_RED, font=(family, 9))
        self.style.configure("Reset.TLabel", foreground="#925308", font=(family, 9))
        self.style.configure("Value.TLabel", background=_CARD, foreground=_TEAL, font=(family, 15, "bold"))
        self.style.configure("TButton", background="#E6ECF3", padding=(11, 7), borderwidth=0)
        self.style.map("TButton", background=[("active", "#D8E4ED")], foreground=[("disabled", "#95A1AE")])
        self.style.configure("Primary.TButton", background=_NAVY, foreground="white", padding=(18, 8))
        self.style.map("Primary.TButton", background=[("disabled", "#CCD6DF"), ("active", "#245774")], foreground=[("disabled", "#687C8D"), ("!disabled", "white")])
        self.style.configure("TEntry", padding=6, fieldbackground=_CARD, bordercolor="#CFDAE4")
        self.style.map("TEntry", fieldbackground=[("disabled", "#EEF2F6")], foreground=[("disabled", "#8493A2")])
        self.style.configure("TCombobox", padding=5, fieldbackground=_CARD, arrowsize=13)
        self.style.map("TCombobox", fieldbackground=[("readonly", _CARD), ("disabled", "#EEF2F6")], foreground=[("disabled", "#8493A2")])
        self.style.configure("TCheckbutton", background=_CARD, padding=3)
        self.style.map("TCheckbutton", background=[("active", _CARD)])
        self.style.configure("TNotebook", background=_BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        self.style.configure("TNotebook.Tab", padding=(17, 9), background="#E6ECF3")
        self.style.map("TNotebook.Tab", background=[("selected", _CARD)], foreground=[("selected", _TEAL)])
        self.style.configure("Warn.TLabel", background=_CARD, foreground="#925308", font=(family, 9, "bold"))
        self.style.configure("Treeview", background=_CARD, fieldbackground=_CARD, foreground=_NAVY, rowheight=24, font=(family, 9))
        self.style.configure("Treeview.Heading", background="#E6ECF3", foreground=_NAVY, font=(family, 9, "bold"))
        self.style.configure("Horizontal.TProgressbar", background=_TEAL, troughcolor="#E7EEF3", borderwidth=0, thickness=5)

    def _wrap(self, label: ttk.Label, inset: int = 92) -> ttk.Label:
        label.configure(wraplength=max(180, 920 - inset))
        self._wrap_labels.append((label, inset))
        def fit(event: tk.Event) -> None:
            # Each column receives its own available width, including at high DPI.
            info = label.grid_info()
            column, row = int(info.get("column", 0)), int(info.get("row", 0))
            span = int(info.get("columnspan", 1))
            bounds = label.master.grid_bbox(column, row, column + span - 1, row)
            width = max(100, min(event.width - 24, bounds[2] - 12 if bounds[2] > 1 else event.width - 24))
            if abs(int(float(label.cget("wraplength"))) - width) > 2:
                label.configure(wraplength=width)
        label.master.bind("<Configure>", fit, add="+")
        return label

    def _build_window(self) -> None:
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer = ttk.Frame(self.root, padding=(22, 16, 22, 12))
        self._outer = outer
        outer.grid(sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=self._t(_TITLE), style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self._wrap(ttk.Label(header, text=self._t("配置连接 · 查看运行状态 · 接入虚拟串口"), style="Muted.TLabel"), 420).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        ttk.Label(header, text=self._t("语言"), style="Muted.TLabel").grid(row=0, column=1, sticky="e", padx=(8, 4))
        language_box = ttk.Combobox(header, textvariable=self._language_var,
                                    values=tuple(LANGUAGES.values()), state="readonly", width=10)
        language_box.grid(row=0, column=2, sticky="e")
        language_box.bind("<<ComboboxSelected>>", self._change_language)
        ttk.Button(header, text=self._t("使用说明"), command=self._open_help).grid(row=0, column=3, padx=(8, 0))

        profiles = ttk.Frame(outer)
        profiles.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        profiles.columnconfigure(1, weight=1)
        ttk.Label(profiles, text=self._t("配置档")).grid(row=0, column=0, padx=(0, 10))
        self._profile_combo = ttk.Combobox(profiles, textvariable=self._selected_var, state="readonly", width=28)
        self._profile_combo.grid(row=0, column=1, sticky="ew")
        self._profile_combo.bind("<<ComboboxSelected>>", self._switch_profile)
        for column, text, command in (
            (2, "保存", self._save_current), (3, "另存配置", self._save_as_profile),
            (4, "删除", self._delete_profile), (5, "导入", self._import_profiles), (6, "导出", self._export_profiles),
        ):
            button = ttk.Button(profiles, text=self._t(text), command=command)
            button.grid(row=0, column=column, padx=(6, 0))
            self._profile_controls.append(button)
        ttk.Label(profiles, textvariable=self._config_status, style="Muted.TLabel", font=(self._font_family, 9)).grid(row=1, column=1, columnspan=6, sticky="w", pady=(5, 0))

        status = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10))
        status.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        status.columnconfigure(1, weight=1)
        self._state_label = ttk.Label(status, textvariable=self._state_text, style="State.TLabel")
        self._state_label.grid(row=0, column=0, sticky="w", padx=(0, 22))
        ttk.Label(status, textvariable=self._endpoint_text, style="CardMuted.TLabel").grid(row=0, column=1, sticky="e")
        self._wrap(ttk.Label(status, textvariable=self._message_text, style="Card.TLabel")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self._error_label = self._wrap(ttk.Label(status, textvariable=self._error_text, style="Error.TLabel"))
        self._error_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self._error_label.grid_remove()

        self._notebook = ttk.Notebook(outer)
        self._notebook.grid(row=3, column=0, sticky="nsew")
        pages = [_ScrollablePage(self._notebook) for _ in range(4)]
        gateway_page, vcom_page, device_page, diag_page = [page.content for page in pages]
        for page, label in zip(pages, ("网关", "虚拟串口", "ESP32 设备", "诊断")):
            self._notebook.add(page, text=self._t(label))
        # Each builder lays out its own frame from row 0; the pages stack those frames.
        self._basic_tab, self._stats_tab, self._advanced_tab = self._sections(
            gateway_page, ("连接设置", "运行状态与流量", "高级参数"))
        self._vcom_tab, self._bridge_tab = self._sections(vcom_page, ("虚拟串口驱动", "com0com 桥接"))
        self._device_tab, = self._sections(device_page, (None,))
        self._diag_tab, = self._sections(diag_page, (None,))
        self._build_basic()
        self._build_statistics()
        self._build_advanced()
        self._build_virtual_com()
        self._build_bridge()
        self._build_device()
        self._build_diagnostics()
        for page in pages:
            page.bind_navigation()

        actions = ttk.Frame(outer)
        actions.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(3, weight=1)
        self._start_button = ttk.Button(actions, text=self._t("启动网关"), style="Primary.TButton", command=self._start)
        self._start_button.grid(row=0, column=0, sticky="w")
        self._stop_button = ttk.Button(actions, text=self._t("停止"), command=self._stop)
        self._stop_button.grid(row=0, column=1, padx=(8, 0))
        self._rearm_button = ttk.Button(actions, text=self._t("重新就绪"), command=self._rearm)
        self._rearm_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Label(actions, textvariable=self._runtime_text, style="Muted.TLabel").grid(row=0, column=3, sticky="e", padx=(12, 0))
        self._reset_label = self._wrap(ttk.Label(actions, text=self._t(_RESET_HELP), style="Reset.TLabel"), 64)
        self._reset_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._reset_label.grid_remove()

        footer = ttk.Frame(outer)
        footer.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self._wrap(ttk.Label(footer, text=self._t("高级工具：通用串口网关、虚拟串口驱动管理、ESP32 检测与诊断。日常烧录用主窗口即可。"), style="Muted.TLabel", font=(self._font_family, 9))).grid(row=0, column=0, sticky="ew")
        self._wrap(ttk.Label(footer, textvariable=self._task_text, style="Muted.TLabel", font=(self._font_family, 9))).grid(row=1, column=0, sticky="ew")

    def _field(self, parent: ttk.Frame, row: int, column: int, title: str, key: str,
               choices: tuple[str, ...] | None = None, span: int = 1) -> ttk.Widget:
        holder = ttk.Frame(parent, style="Card.TFrame")
        holder.grid(row=row, column=column, columnspan=span, sticky="ew", padx=(0, 16 if column == 0 and span == 1 else 0), pady=(0, 11))
        holder.columnconfigure(0, weight=1)
        ttk.Label(holder, text=self._t(title), style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        if choices is not None:
            widget = ttk.Combobox(holder, textvariable=self.vars[key], values=choices, state="readonly", width=15)
            kind = "readonly"
        else:
            widget = ttk.Entry(holder, textvariable=self.vars[key], width=15)
            kind = "normal"
        widget.grid(row=1, column=0, sticky="ew")
        self._editable.append((widget, kind))
        return widget

    def _sections(self, page: ttk.Frame, titles: tuple[str | None, ...]) -> list[ttk.Frame]:
        page.columnconfigure(0, weight=1)
        frames, row = [], 0
        for index, title in enumerate(titles):
            if title:
                ttk.Label(page, text=self._t(title), style="Heading.TLabel").grid(
                    row=row, column=0, sticky="w", pady=(0 if index == 0 else 18, 6))
                row += 1
            frame = ttk.Frame(page, style="Card.TFrame")
            frame.grid(row=row, column=0, sticky="nsew")
            frames.append(frame)
            row += 1
        return frames

    def _build_diagnostics(self) -> None:
        tab = self._diag_tab
        tab.columnconfigure(0, weight=1)
        self._wrap(ttk.Label(tab, text=self._t("环境检查只列出本机串口和驱动信息，不会打开串口；自检使用独立的临时本机模拟，不会连接真实设备。导出诊断时会去掉令牌、路径和串口数据。"), style="CardMuted.TLabel"), 140).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        buttons = ttk.Frame(tab, style="Card.TFrame")
        buttons.grid(row=1, column=0, sticky="w", pady=(0, 8))
        self._environment_button = ttk.Button(buttons, text=self._t("检查本机环境"), command=lambda: self._run_check("environment"))
        self._environment_button.grid(row=0, column=0, sticky="w")
        self._selftest_button = ttk.Button(buttons, text=self._t("运行自检"), command=lambda: self._run_check("selftest"))
        self._selftest_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._diagnostic_button = ttk.Button(buttons, text=self._t("导出诊断"), command=self._export_diagnostics)
        self._diagnostic_button.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self._checks_text = self._readonly_text(tab, row=2, height=14)
        self._render_checks()

    def _build_basic(self) -> None:
        tab = self._basic_tab
        tab.columnconfigure((0, 1), weight=1, uniform="basic")
        self._field(tab, 0, 0, "配置名称", "name", span=2)
        self._field(tab, 1, 0, "模式", "mode", tuple(self._modes))
        self._field(tab, 1, 1, "协议", "protocol", tuple(self._protocols))
        self._field(tab, 2, 0, "本机监听端口", "listen_port")
        self._remote_host_entry = self._field(tab, 2, 1, "远端主机 / IP", "remote_host")
        self._remote_port_entry = self._field(tab, 3, 1, "远端端口", "remote_port")
        options = ttk.Frame(tab, style="Card.TFrame")
        options.grid(row=3, column=0, sticky="nw", padx=(0, 16), pady=(3, 0))
        nodelay = ttk.Checkbutton(options, text=self._t("低延迟发送（TCP_NODELAY）"), variable=self.vars["nodelay"])
        nodelay.grid(row=0, column=0, sticky="w")
        self._editable.append((nodelay, "normal"))
        options.columnconfigure(0, weight=1)
        options.grid_configure(sticky="new")
        self._wrap(ttk.Label(options, text=self._t("监听端口填 0 时，由系统分配空闲端口。"), style="CardMuted.TLabel"), 550).grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self._mode_hint = self._wrap(ttk.Label(tab, style="CardMuted.TLabel"))
        self._mode_hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))
        tab.rowconfigure(5, weight=1)

    def _build_advanced(self) -> None:
        tab = self._advanced_tab
        tab.columnconfigure((0, 1), weight=1, uniform="advanced")
        self._field(tab, 0, 0, "本机入口地址", "listen_host", ("127.0.0.1", "::1"))
        self._field(tab, 0, 1, "每方向缓冲区（字节）", "buffer_bytes")
        self._field(tab, 1, 0, "单次读块（字节）", "chunk_bytes")
        self._field(tab, 1, 1, "保活间隔（秒）", "keepalive_s")
        self._field(tab, 2, 0, "连接超时（秒）", "connect_timeout_s")
        self._field(tab, 2, 1, "发送停滞超时（秒）", "stall_timeout_s")
        text = (
            "默认参数适合本机模拟。缓冲区范围 512–1,048,576 字节，单次读块不得超过缓冲区或 16,384 字节。\n"
            "超时范围 0.1–300 秒，保活间隔 1–300 秒。本机入口仅允许回环地址。"
        )
        self._wrap(ttk.Label(tab, text=self._t(text), style="CardMuted.TLabel")).grid(row=3, column=0, columnspan=2, sticky="nw", pady=(8, 0))
        tab.rowconfigure(4, weight=1)

    def _build_statistics(self) -> None:
        tab = self._stats_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(4, weight=1)
        metrics = ttk.Frame(tab, style="Card.TFrame")
        metrics.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for column, (title, key) in enumerate((
            ("客户端 → 网关", "bytes_from_client"), ("网关 → 远端", "bytes_to_remote"),
            ("远端 → 网关", "bytes_from_remote"), ("网关 → 客户端", "bytes_to_client"),
        )):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
            card = ttk.Frame(metrics, style="Card.TFrame")
            card.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            ttk.Label(card, text=self._t(title), style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(card, textvariable=self._metric_vars[key], style="Value.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        buffers = ttk.Frame(tab, style="Card.TFrame")
        buffers.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        buffers.columnconfigure((0, 1), weight=1, uniform="buffers")
        self._buffer_bars = {}
        for column, key, title in ((0, "remote", "发往远端缓冲"), (1, "client", "发往客户端缓冲")):
            frame = ttk.Frame(buffers, style="Card.TFrame")
            frame.grid(row=0, column=column, sticky="ew", padx=(0, 18 if column == 0 else 0))
            frame.columnconfigure(1, weight=1)
            ttk.Label(frame, text=self._t(title), style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(frame, textvariable=self._buffer_vars[key], style="CardMuted.TLabel").grid(row=0, column=1, sticky="e", padx=(6, 0))
            bar = ttk.Progressbar(frame, maximum=4096, mode="determinate")
            bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
            self._buffer_bars[key] = bar
        serial = ttk.Frame(tab, style="Card.TFrame")
        serial.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        serial.columnconfigure(1, weight=1)
        for row, title, variable in ((0, "客户端请求", self._serial_requested), (1, "协议层确认", self._serial_confirmed)):
            ttk.Label(serial, text=self._t(title), style="Card.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=2)
            self._wrap(ttk.Label(serial, textvariable=variable, style="Card.TLabel"), 205).grid(row=row, column=1, sticky="w", pady=2)
        self._wrap(ttk.Label(serial, text=self._t("请求与确认仅表示协议交互，不代表真实硬件串口状态。"), style="CardMuted.TLabel")).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        log_header = ttk.Frame(tab, style="Card.TFrame")
        log_header.grid(row=3, column=0, sticky="ew", pady=(0, 5))
        log_header.columnconfigure(1, weight=1)
        ttk.Label(log_header, text=self._t("事件记录"), style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(log_header, textvariable=self._session_text, style="CardMuted.TLabel").grid(row=0, column=1, sticky="e")
        self._log_text = self._readonly_text(tab, row=4, height=5)
        self._log_text.tag_configure("ERROR", foreground=_RED)
        self._log_text.tag_configure("WARNING", foreground="#925308")
        self._log_text.tag_configure("INFO", foreground=_MUTED)

    # ------------------------------------------------------------ extension state
    def _init_extensions(self) -> None:
        """State for the 虚拟 COM / 桥接 / ESP32 tabs; survives language rebuilds."""
        root = self.root
        self._ext_pending: set[str] = set()
        self._vcom_raw: list[dict[str, Any]] | None = None
        self._vcom_matrix: list[dict[str, str]] = []
        self._vcom_suggest = ""
        self._pairs: list[dict[str, Any]] = []
        self._preview_text = ""
        self._bridge: Any = None
        self._bridge_state: str | None = None
        self._bridge_event_key: Any = None
        self._next_bridge_render = 0.0
        self._device_raw: dict[str, Any] | None = None
        self._device_error = ""
        self._scan_hosts: list[str] = []
        self.ext: dict[str, tk.StringVar] = {key: tk.StringVar(root) for key in (
            "vcom_com", "vcom_host", "vcom_port", "bridge_pair", "bridge_host", "bridge_port",
            "dev_host", "dev_port", "dev_http", "dev_path", "dev_header", "dev_baud", "scan_net",
        )}
        self.ext["vcom_host"].set("127.0.0.1")
        self.ext["dev_port"].set("3333")
        self.ext["dev_http"].set("80")
        self.ext["dev_path"].set("/api/maintenance/status")
        self.ext["dev_baud"].set("115200")
        try:
            self.ext["dev_header"].set(default_token_header())
        except Exception:
            self.ext["dev_header"].set("")
        self._vcom_rfc = tk.BooleanVar(root, True)
        self._probe_var = tk.BooleanVar(root, False)
        # Memory only: never written to profiles, preferences, logs or diagnostics.
        self._token_var = tk.StringVar(root)
        self._vcom_result = _DisplayVar(self, "尚未检测虚拟 COM 驱动。")
        self._device_status = _DisplayVar(self)
        self._bridge_vars = {key: _DisplayVar(self, value) for key, value in (
            ("state", "未启动"), ("message", ""), ("session", "—"), ("app_to_device", "0 B"),
            ("device_to_app", "0 B"), ("settings", "等待应用设置"), ("lines", "—"), ("error", ""),
        )}

    def _listen_port_default(self) -> str:
        port = str(self.vars["listen_port"].get()).strip()
        if self._running and self._snapshot.get("listen_port"):
            port = str(self._snapshot.get("listen_port"))
        return port if port not in ("", "0") else "7000"

    def _fill_extension_defaults(self, force: bool = False) -> None:
        if force or not self.ext["vcom_port"].get():
            self.ext["vcom_port"].set(self._listen_port_default())
        if force or not self.ext["bridge_host"].get():
            self._fill_bridge_from_profile()

    def _fill_bridge_from_profile(self) -> None:
        if self._modes.get(str(self.vars["mode"].get())) == "device" and str(self.vars["remote_host"].get()).strip():
            self.ext["bridge_host"].set(str(self.vars["remote_host"].get()).strip())
            self.ext["bridge_port"].set(str(self.vars["remote_port"].get()).strip())
        else:
            self.ext["bridge_host"].set("127.0.0.1")
            self.ext["bridge_port"].set(self._listen_port_default())

    def _ext_entry(self, parent: ttk.Frame, row: int, column: int, title: str, variable: tk.Variable,
                   width: int = 15, show: str | None = None) -> ttk.Entry:
        holder = ttk.Frame(parent, style="Card.TFrame")
        holder.grid(row=row, column=column, sticky="ew", padx=(0, 12), pady=(0, 8))
        holder.columnconfigure(0, weight=1)
        ttk.Label(holder, text=self._t(title), style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        entry = ttk.Entry(holder, textvariable=variable, width=width, **({"show": show} if show else {}))
        entry.grid(row=1, column=0, sticky="ew")
        return entry

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _start_ext(self, task: str, work: Any) -> bool:
        if self._closing or task in self._ext_pending:
            return False
        self._ext_pending.add(task)
        self._sync_controls()
        run_background(task, work, self._replies)
        return True

    # ------------------------------------------------------------ tab: 虚拟 COM
    def _build_virtual_com(self) -> None:
        tab = self._vcom_tab
        tab.columnconfigure(0, weight=1)
        head = ttk.Frame(tab, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text=self._t("检测到的虚拟 COM 驱动"), style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        self._vcom_refresh_button = ttk.Button(head, text=self._t("刷新检测"), command=self._refresh_vcom)
        self._vcom_refresh_button.grid(row=0, column=1, sticky="e")
        self._drivers_text = self._readonly_text(tab, row=1, height=6)
        self._wrap(ttk.Label(tab, text=self._t(_HWVSP_LIMIT), style="Warn.TLabel")).grid(row=2, column=0, sticky="ew", pady=(8, 6))
        self._matrix_tree = ttk.Treeview(tab, columns=("feature", "hwvsp", "com0com"), show="headings", height=7, selectmode="none")
        for column, title, width in (("feature", "功能", 230), ("hwvsp", "HW VSP3 Single", 190), ("com0com", "com0com 桥接", 190)):
            self._matrix_tree.heading(column, text=self._t(title))
            self._matrix_tree.column(column, width=width, stretch=True)
        self._matrix_tree.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        hw = ttk.Frame(tab, style="Card.TFrame")
        hw.grid(row=4, column=0, sticky="ew", pady=(0, 4))
        hw.columnconfigure((0, 1, 2), weight=1, uniform="hw")
        ttk.Label(hw, text=self._t("HW VSP3 Single 通道"), style="Heading.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self._ext_entry(hw, 1, 0, "COM 名称", self.ext["vcom_com"])
        self._ext_entry(hw, 1, 1, "目标主机", self.ext["vcom_host"])
        self._ext_entry(hw, 1, 2, "目标端口", self.ext["vcom_port"])
        ttk.Checkbutton(hw, text=self._t("RFC2217（NVT 串口设置）"), variable=self._vcom_rfc).grid(row=1, column=3, sticky="sw", pady=(0, 10))
        buttons = ttk.Frame(hw, style="Card.TFrame")
        buttons.grid(row=2, column=0, columnspan=4, sticky="w")
        self._vcom_preview_button = ttk.Button(buttons, text=self._t("预览配置"), command=self._preview_vcom)
        self._vcom_preview_button.grid(row=0, column=0)
        self._vcom_apply_button = ttk.Button(buttons, text=self._t("应用到 HW VSP（需要管理员确认）"), command=self._apply_hwvsp)
        self._vcom_apply_button.grid(row=0, column=1, padx=(8, 0))
        self._preview_box = self._readonly_text(tab, row=5, height=4)
        self._preview_box.master.grid_configure(pady=(6, 10))

        c0c = ttk.Frame(tab, style="Card.TFrame")
        c0c.grid(row=6, column=0, sticky="ew", pady=(0, 6))
        c0c.columnconfigure(0, weight=1)
        ttk.Label(c0c, text=self._t("com0com 端口对"), style="Heading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self._pairs_tree = ttk.Treeview(c0c, columns=("pair", "com", "endpoint"), show="headings", height=3, selectmode="browse")
        for column, title, width in (("pair", "编号", 70), ("com", "应用端 COM", 200), ("endpoint", "桥接端点", 200)):
            self._pairs_tree.heading(column, text=self._t(title))
            self._pairs_tree.column(column, width=width, stretch=True)
        self._pairs_tree.grid(row=1, column=0, sticky="ew")
        self._pairs_tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_controls(), add="+")
        pair_buttons = ttk.Frame(c0c, style="Card.TFrame")
        pair_buttons.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._pair_create_button = ttk.Button(pair_buttons, text=self._t("新建端口对…"), command=self._create_pair)
        self._pair_create_button.grid(row=0, column=0)
        self._pair_remove_button = ttk.Button(pair_buttons, text=self._t("删除端口对"), command=self._remove_pair)
        self._pair_remove_button.grid(row=0, column=1, padx=(8, 0))
        self._pair_wire_button = ttk.Button(pair_buttons, text=self._t("启用关闭检测"), command=self._wire_pair)
        self._pair_wire_button.grid(row=0, column=2, padx=(8, 0))
        self._wrap(ttk.Label(tab, textvariable=self._vcom_result, style="Card.TLabel")).grid(row=7, column=0, sticky="ew", pady=(4, 10))

        endpoint = ttk.Frame(tab, style="Card.TFrame")
        endpoint.grid(row=8, column=0, sticky="ew", pady=(0, 8))
        endpoint.columnconfigure(0, weight=1)
        ttk.Label(endpoint, textvariable=self._vcom_endpoint, style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self._copy_endpoint_button = ttk.Button(endpoint, text=self._t("复制入口"), command=self._copy_endpoint)
        self._copy_endpoint_button.grid(row=0, column=1, sticky="e")
        self._wrap(ttk.Label(tab, text=self._t("驱动由用户自行安装（厂商签名）；本程序只读取状态，修改通道或端口对时弹出一次 Windows 管理员确认。"), style="CardMuted.TLabel")).grid(row=9, column=0, sticky="w", pady=(0, 8))
        self._render_vcom()

    def _backend(self, name: str) -> dict[str, Any] | None:
        return next((item for item in self._vcom_raw or [] if item.get("name") == name), None)

    def _note_text(self, note: Any) -> str:
        text = str(note)
        if text in _BACKEND_NOTES:
            return self._t(_BACKEND_NOTES[text])
        match = re.fullmatch(r"Configured (COM\d+) collides with a physical port; choose another name\.", text)
        if match:
            return self._t("配置的 {com} 与物理端口冲突，请换一个名称。", com=match.group(1))
        return _short(text, 300)

    def _render_vcom(self) -> None:
        tr = self._t
        if self._vcom_raw is None:
            text = tr("点击“刷新检测”读取 HW VSP3 Single 与 com0com 的安装和配置状态（只读）。")
        else:
            lines = []
            for item in self._vcom_raw:
                name = _short(item.get("name", ""), 40)
                installed = tr("已安装") if item.get("installed") else tr("未安装")
                if item.get("name") == "com0com":
                    running = tr("有端口对") if item.get("running") else tr("无端口对")
                else:
                    running = tr("服务运行中") if item.get("running") else tr("服务未运行")
                lines.append(f"{name}：{installed} · {running}" if tr.language != "en" else f"{name}: {installed} · {running}")
                channel = item.get("channel") or {}
                if item.get("name") == "HW VSP3 Single" and item.get("installed"):
                    lines.append("  " + tr("配置的 COM：{com} · 目标：{target} · RFC2217：{nvt}",
                                           com=_short(channel.get("COM") or "—", 20), target=_short(channel.get("IP") or "—", 60),
                                           nvt=tr("开") if channel.get("NvtPortSetup") == "1" else tr("关")))
                elif item.get("name") == "com0com" and channel:
                    lines.append("  " + tr("端口对：{pairs}", pairs=", ".join(f"{_short(com or '—', 12)} ↔ {_short(end, 12)}" for end, com in channel.items())))
                if item.get("ports"):
                    lines.append("  " + tr("系统中的虚拟 COM：{ports}", ports=", ".join(_short(p, 12) for p in item["ports"])))
                lines.extend("  " + tr("提醒：{note}", note=self._note_text(note)) for note in item.get("notes", []))
            if self._vcom_suggest:
                lines.append(tr("建议使用的空闲 COM：{com}", com=self._vcom_suggest))
            text = "\n".join(lines)
        self._set_text(self._drivers_text, text)
        self._matrix_tree.delete(*self._matrix_tree.get_children())
        from .vcom import capability_matrix
        for row in self._vcom_matrix or capability_matrix():
            self._matrix_tree.insert("", "end", values=tuple(tr(_MATRIX_ZH.get(row.get(key, ""), row.get(key, ""))) for key in ("feature", "hwvsp", "com0com")))
        selected = self._selected_pair()
        self._pairs_tree.delete(*self._pairs_tree.get_children())
        for pair in self._pairs:
            iid = str(pair.get("pair"))
            self._pairs_tree.insert("", "end", iid=iid, values=(pair.get("pair"), pair.get("com") or "—", pair.get("endpoint")))
            if selected and selected.get("pair") == pair.get("pair"):
                self._pairs_tree.selection_set(iid)
        self._set_text(self._preview_box, self._preview_text or tr("点击“预览配置”查看将写入 HW VSP 的 CHANNEL0 片段（不含厂商 GENERAL 段）。"))
        self._vcom_result.refresh()
        try:  # during a language rebuild the bridge tab is built after this one
            self._bridge_pair_combo.configure(values=tuple(pair_label(p) for p in self._pairs))
        except (AttributeError, tk.TclError):
            pass

    def _selected_pair(self) -> dict[str, Any] | None:
        tree = getattr(self, "_pairs_tree", None)
        try:
            selection = tree.selection() if tree is not None else ()
        except tk.TclError:
            return None
        return next((p for p in self._pairs if str(p.get("pair")) in selection), None)

    def _refresh_vcom(self) -> None:
        def work() -> dict[str, Any]:
            from . import vcom
            backends = [backend.to_dict() for backend in vcom.detect_backends()]
            try:
                suggest = vcom.suggest_free_com()
            except RuntimeError:
                suggest = ""
            return {"backends": backends, "suggest": suggest, "matrix": vcom.capability_matrix()}
        if self._start_ext("vcom_detect", work):
            self._vcom_result.set("正在检测虚拟 COM 驱动…")

    def _preview_vcom(self) -> None:
        try:
            plan = build_vcom_plan(self.ext["vcom_com"].get(), self.ext["vcom_host"].get(), self.ext["vcom_port"].get(), self._vcom_rfc.get())
            from .vcom import HwVspBackend
            self._preview_text = HwVspBackend().preview(plan)
        except ValueError as exc:
            self._show_error("配置无法预览", exc)
            return
        self.ext["vcom_com"].set(plan.com)
        self._set_text(self._preview_box, self._preview_text)

    def _apply_hwvsp(self) -> None:
        try:
            plan = build_vcom_plan(self.ext["vcom_com"].get(), self.ext["vcom_host"].get(), self.ext["vcom_port"].get(), self._vcom_rfc.get())
        except ValueError as exc:
            self._show_error("配置无法应用", exc)
            return
        message = self._t(
            "将把 HW VSP 通道设为 {com} → {target}（RFC2217：{nvt}）。\n\n确定后 Windows 会弹出一次管理员确认，原配置先备份；HW VSP 服务会重启，最长约 1–2 分钟。",
            com=plan.com, target=_address(plan.host, plan.port), nvt=self._t("开") if plan.rfc2217 else self._t("关"))
        if not self._messagebox.askokcancel(self._t("应用到 HW VSP"), message, parent=self.root):
            return
        from .vcom import HwVspBackend
        self._preview_text = HwVspBackend().preview(plan)
        self._set_text(self._preview_box, self._preview_text)
        if self._start_ext("vcom_apply", lambda: {"com": plan.com, **HwVspBackend().apply(plan, timeout_s=120)}):
            self._vcom_result.set("正在等待管理员确认并应用 HW VSP 配置…")

    def _create_pair(self) -> None:
        name = self._simpledialog.askstring(self._t("新建 com0com 端口对"), self._t("应用程序使用的 COM 名称（另一端是桥接用的隐藏 CNCBn）"),
                                            initialvalue=self._vcom_suggest or "COM20", parent=self.root)
        if name is None:
            return
        try:
            com = normalize_com(name)
        except ValueError as exc:
            self._show_error("无法新建端口对", exc)
            return
        if not self._messagebox.askokcancel(self._t("新建 com0com 端口对"), self._t("新建 {com} ↔ CNCBn 端口对。\n\n确定后 Windows 会弹出一次管理员确认，最长约 2 分钟。", com=com), parent=self.root):
            return
        from .vcom import Com0comBackend
        if self._start_ext("pair_create", lambda: {"com": com, **Com0comBackend().create_pair(com)}):
            self._vcom_result.set("正在等待管理员确认并新建端口对…")

    def _wire_pair(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            self._show_error("无法启用关闭检测", "请先在列表中选择一个端口对。")
            return
        if pair.get("close_detect"):
            self._vcom_result.set("该端口对已启用关闭检测。")
            return
        number = int(pair["pair"])
        message = self._t("让端口对 #{pair} 在应用关闭 COM 时通知桥接结束会话？\n\n确定后 Windows 会弹出一次管理员确认。", pair=number)
        if not self._messagebox.askyesno(self._t("启用关闭检测"), message, parent=self.root):
            return
        from .vcom import Com0comBackend
        if self._start_ext("pair_wire", lambda: {"pair": number, **Com0comBackend().wire_open_signal(number)}):
            self._vcom_result.set("正在等待管理员确认并配置端口对…")

    def _remove_pair(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            self._show_error("无法删除端口对", "请先在列表中选择一个端口对。")
            return
        snapshot = self._bridge.snapshot() if self._bridge else {}
        if snapshot.get("endpoint") == pair.get("endpoint") and snapshot.get("state") not in (None, "stopped"):
            self._show_error("无法删除端口对", "该端口对正在被桥接使用，请先停止桥接。")
            return
        number = int(pair["pair"])
        message = self._t("删除端口对 #{pair}（{com} ↔ {endpoint}）？\n\n正在使用该 COM 的程序会失去端口。确定后 Windows 会弹出一次管理员确认。",
                          pair=number, com=_short(pair.get("com") or "—", 12), endpoint=_short(pair.get("endpoint"), 12))
        if not self._messagebox.askyesno(self._t("删除端口对"), message, parent=self.root):
            return
        from .vcom import Com0comBackend
        if self._start_ext("pair_remove", lambda: {"pair": number, **Com0comBackend().remove_pair(number)}):
            self._vcom_result.set("正在等待管理员确认并删除端口对…")

    # ------------------------------------------------------------ tab: com0com 桥接
    def _build_bridge(self) -> None:
        tab = self._bridge_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(6, weight=1)
        ttk.Label(tab, text=self._t("com0com 桥接到 RFC2217"), style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        self._wrap(ttk.Label(tab, text=self._t("应用打开端口对的可见 COM；桥接打开隐藏的 CNCBn 端，把数据、波特率等设置和 DTR/RTS 转发到远端。可与网关同时运行，例如桥接到 127.0.0.1 的网关入口并让网关使用本机模拟。"), style="CardMuted.TLabel")).grid(row=1, column=0, sticky="ew", pady=(2, 8))
        form = ttk.Frame(tab, style="Card.TFrame")
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure((0, 1, 2), weight=1, uniform="bridge")
        holder = ttk.Frame(form, style="Card.TFrame")
        holder.grid(row=0, column=0, sticky="ew", padx=(0, 12), pady=(0, 8))
        holder.columnconfigure(0, weight=1)
        ttk.Label(holder, text=self._t("端口对 / 端点"), style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        self._bridge_pair_combo = ttk.Combobox(holder, textvariable=self.ext["bridge_pair"], values=tuple(pair_label(p) for p in self._pairs), width=22)
        self._bridge_pair_combo.grid(row=1, column=0, sticky="ew")
        self._bridge_host_entry = self._ext_entry(form, 0, 1, "远端主机 / IP", self.ext["bridge_host"])
        self._bridge_port_entry = self._ext_entry(form, 0, 2, "远端端口", self.ext["bridge_port"])
        tools = ttk.Frame(tab, style="Card.TFrame")
        tools.grid(row=3, column=0, sticky="w", pady=(0, 8))
        self._bridge_start_button = ttk.Button(tools, text=self._t("启动桥接"), style="Primary.TButton", command=self._start_bridge)
        self._bridge_start_button.grid(row=0, column=0)
        self._bridge_stop_button = ttk.Button(tools, text=self._t("停止桥接"), command=self._stop_bridge)
        self._bridge_stop_button.grid(row=0, column=1, padx=(8, 0))
        self._bridge_restart_button = ttk.Button(tools, text=self._t("重新开始"), command=self._restart_bridge)
        self._bridge_restart_button.grid(row=0, column=2, padx=(8, 0))
        self._bridge_pairs_button = ttk.Button(tools, text=self._t("刷新端口对"), command=self._refresh_vcom)
        self._bridge_pairs_button.grid(row=0, column=3, padx=(8, 0))
        self._bridge_fill_button = ttk.Button(tools, text=self._t("按当前配置填写"), command=self._fill_bridge_from_profile)
        self._bridge_fill_button.grid(row=0, column=4, padx=(8, 0))
        status = ttk.Frame(tab, style="Card.TFrame")
        status.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        status.columnconfigure((1, 3), weight=1)
        self._bridge_state_label = ttk.Label(status, textvariable=self._bridge_vars["state"], style="State.TLabel")
        self._bridge_state_label.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(status, textvariable=self._bridge_vars["session"], style="CardMuted.TLabel").grid(row=0, column=2, columnspan=2, sticky="e")
        for row, column, title, key in ((1, 0, "应用 → 设备", "app_to_device"), (1, 2, "设备 → 应用", "device_to_app")):
            ttk.Label(status, text=self._t(title), style="CardMuted.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 8), pady=(6, 0))
            ttk.Label(status, textvariable=self._bridge_vars[key], style="Value.TLabel").grid(row=row, column=column + 1, sticky="w", pady=(6, 0))
        for row, title, key in ((2, "当前设置", "settings"), (3, "控制线", "lines"), (4, "状态说明", "message")):
            ttk.Label(status, text=self._t(title), style="Card.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
            self._wrap(ttk.Label(status, textvariable=self._bridge_vars[key], style="Card.TLabel"), 220).grid(row=row, column=1, columnspan=3, sticky="w", pady=2)
        self._bridge_error_label = self._wrap(ttk.Label(status, textvariable=self._bridge_vars["error"], style="Error.TLabel"))
        self._bridge_error_label.grid(row=5, column=0, columnspan=4, sticky="w", pady=(2, 0))
        self._bridge_reset_label = self._wrap(ttk.Label(tab, text=self._t(_BRIDGE_RESET_HELP), style="Warn.TLabel"))
        self._bridge_reset_label.grid(row=5, column=0, sticky="ew", pady=(0, 6))
        self._bridge_reset_label.grid_remove()
        self._bridge_log = self._readonly_text(tab, row=6, height=5)
        self._bridge_event_key = None
        self._render_bridge()

    def _start_bridge(self) -> None:
        try:
            endpoint, host, port = bridge_args(self.ext["bridge_pair"].get(), self.ext["bridge_host"].get(), self.ext["bridge_port"].get())
            from .c0c_bridge import Com0comBridge
            bridge = Com0comBridge(endpoint, host, port)
        except ValueError as exc:
            self._show_error("无法启动桥接", exc)
            return
        old = self._bridge

        def work() -> dict[str, Any]:
            if old is not None:
                old.stop()
            bridge.start()
            return bridge.snapshot()
        self._bridge = bridge
        self._bridge_event_key = None
        if self._start_ext("bridge_start", work):
            self._add_event("INFO", f"启动 com0com 桥接：{endpoint} → {_address(host, port)}。")

    def _stop_bridge(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            self._start_ext("bridge_stop", lambda: (bridge.stop(), bridge.snapshot())[1])

    def _restart_bridge(self) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        if not self._messagebox.askokcancel(self._t("重新开始桥接"), self._t(_BRIDGE_RESET_HELP) + "\n\n" + self._t("新会话从零开始，旧数据不会续传。是否继续？"), parent=self.root):
            return

        def work() -> dict[str, Any]:
            bridge.stop()
            bridge.start()
            return bridge.snapshot()
        self._start_ext("bridge_restart", work)

    def _render_bridge(self) -> None:
        if not hasattr(self, "_bridge_log"):
            return
        snapshot = self._bridge.snapshot() if self._bridge is not None else None
        state = snapshot.get("state", "stopped") if snapshot else None
        tr = self._t
        if snapshot is None:
            label, color = "未启动", _MUTED
        else:
            label, color = _BRIDGE_STATES.get(state, ("状态未知", _MUTED))
        self._bridge_vars["state"].set(label)
        self._bridge_state_label.configure(foreground=color)
        snapshot = snapshot or {}
        self._bridge_vars["session"].set(tr("会话 {session} · 端点 {endpoint} → {remote} · 请求 {requests}",
                                            session=snapshot.get("session_id") or "—", endpoint=_short(snapshot.get("endpoint") or "—", 12),
                                            remote=_short(snapshot.get("remote") or "—", 60), requests=snapshot.get("requests_sent", 0)))
        self._bridge_vars["app_to_device"].set(_byte_count(snapshot.get("app_to_device", 0)))
        self._bridge_vars["device_to_app"].set(_byte_count(snapshot.get("device_to_app", 0)))
        self._bridge_vars["settings"].set(bridge_settings_text(snapshot.get("settings"), tr))
        lines = snapshot.get("lines") or {}
        self._bridge_vars["lines"].set(" · ".join(f"{name.upper()} {tr('开') if lines[name] else tr('关')}" for name in ("dtr", "rts", "break") if name in lines) or "—")
        self._bridge_vars["message"].set(_short(snapshot.get("message") or "—", 200))
        error = snapshot.get("last_error")
        self._bridge_vars["error"].set(tr("最近错误：{error}", error=_short(error, 160)) if error else "")
        if error:
            self._bridge_error_label.grid()
        else:
            self._bridge_error_label.grid_remove()
        if state == "needs_reset":
            self._bridge_reset_label.grid()
        else:
            self._bridge_reset_label.grid_remove()
        events = snapshot.get("events") or []
        key = (id(self._bridge), len(events), events[-1].get("time") if events else None)
        if key != self._bridge_event_key:
            self._bridge_event_key = key
            text = "\n".join(f"{_short(e.get('time', ''), 32)}  [{tr({'info': '信息', 'warning': '提醒', 'error': '错误'}.get(str(e.get('level')), str(e.get('level'))))}]  {_short(e.get('message', ''), 300)}"
                             for e in events if isinstance(e, dict))
            self._set_text(self._bridge_log, text or tr("尚无桥接事件。"))
            self._bridge_log.see("end")
        if state != self._bridge_state:
            previous, self._bridge_state = self._bridge_state, state
            if previous is not None and state is not None:
                self._add_event("ERROR" if state in ("needs_reset", "error") else "INFO", f"com0com 桥接状态：{label}。")
            self._sync_controls()

    # ------------------------------------------------------------ tab: ESP32 设备
    def _build_device(self) -> None:
        tab = self._device_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(6, weight=1)
        ttk.Label(tab, text=self._t("ESP32 网关设备检查"), style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        self._wrap(ttk.Label(tab, text=self._t("只读检查：TCP 连接、可选 RFC2217 波特率探测、HTTP 状态接口。令牌只保存在本窗口内存中，不写入配置、日志或诊断。"), style="CardMuted.TLabel")).grid(row=1, column=0, sticky="ew", pady=(2, 8))
        form = ttk.Frame(tab, style="Card.TFrame")
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure((0, 1, 2), weight=1, uniform="device")
        self._ext_entry(form, 0, 0, "设备主机 / IP", self.ext["dev_host"])
        self._ext_entry(form, 0, 1, "RFC2217 端口", self.ext["dev_port"])
        self._ext_entry(form, 0, 2, "HTTP 端口", self.ext["dev_http"])
        self._ext_entry(form, 1, 0, "状态路径", self.ext["dev_path"])
        self._ext_entry(form, 1, 1, "鉴权请求头", self.ext["dev_header"])
        self._ext_entry(form, 1, 2, "令牌（可选）", self._token_var, show="•")
        probe = ttk.Frame(tab, style="Card.TFrame")
        probe.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        probe.columnconfigure(2, weight=1)
        ttk.Checkbutton(probe, text=self._t("探测波特率"), variable=self._probe_var).grid(row=0, column=0, sticky="w")
        ttk.Entry(probe, textvariable=self.ext["dev_baud"], width=10).grid(row=0, column=1, sticky="w", padx=(6, 10))
        self._wrap(ttk.Label(probe, text=self._t("探测会发送一次设置波特率请求，远端 UART 速度会被改为该值，直到下一次会话重新设置。"), style="Warn.TLabel"), 330).grid(row=0, column=2, sticky="ew")
        tools = ttk.Frame(tab, style="Card.TFrame")
        tools.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        self._device_check_button = ttk.Button(tools, text=self._t("检测设备"), style="Primary.TButton", command=self._check_device)
        self._device_check_button.grid(row=0, column=0, sticky="w")
        self._device_scan_button = ttk.Button(tools, text=self._t("扫描局域网"), command=self._scan_devices)
        self._device_scan_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(tools, text=self._t("网段（留空自动）"), style="CardMuted.TLabel").grid(row=0, column=2, padx=(12, 4))
        ttk.Entry(tools, textvariable=self.ext["scan_net"], width=18).grid(row=0, column=3, sticky="w")
        ttk.Label(tools, textvariable=self._device_status, style="CardMuted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        hosts = ttk.Frame(tab, style="Card.TFrame")
        hosts.grid(row=5, column=0, sticky="ew", pady=(0, 6))
        hosts.columnconfigure(0, weight=1)
        self._scan_list = tk.Listbox(hosts, height=3, activestyle="none", background="#F6F8FB", foreground=_NAVY,
                                     relief="flat", highlightthickness=1, highlightbackground="#E1E8EF", font=(self._font_family, 9))
        self._scan_list.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(hosts, command=self._scan_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self._scan_list.configure(yscrollcommand=scroll.set)
        self._scan_list.bind("<<ListboxSelect>>", self._pick_scanned_host, add="+")
        self._scan_list.bind("<Double-Button-1>", lambda event: (self._pick_scanned_host(event), self._check_device()), add="+")
        self._device_text = self._readonly_text(tab, row=6, height=7)
        self._render_device()

    def _pick_scanned_host(self, _event: tk.Event | None = None) -> None:
        selection = self._scan_list.curselection()
        if selection and selection[0] < len(self._scan_hosts):
            self.ext["dev_host"].set(self._scan_hosts[selection[0]])

    def _render_device(self) -> None:
        if not hasattr(self, "_device_text"):
            return
        if self._device_error:
            text = self._t("检测失败：{error}", error=self._t.event(self._device_error))
        else:
            text = format_device_report(self._device_raw, self._t)
        self._set_text(self._device_text, text)
        self._scan_list.delete(0, "end")
        for host in self._scan_hosts:
            self._scan_list.insert("end", host)
        self._device_status.refresh()

    def _check_device(self) -> None:
        try:
            kwargs = device_check_args(self.ext["dev_host"].get(), self.ext["dev_port"].get(), self.ext["dev_http"].get(),
                                       self.ext["dev_path"].get(), self.ext["dev_header"].get(), self._probe_var.get(), self.ext["dev_baud"].get())
        except ValueError as exc:
            self._show_error("无法检测设备", exc)
            return
        if kwargs["probe_baud"] and not self._messagebox.askokcancel(
                self._t("探测波特率"), self._t("探测会把远端 UART 波特率设为 {baud}。若设备正在被其他程序使用，请取消。是否继续？", baud=kwargs["probe_baud"]), parent=self.root):
            return
        token = self._token_var.get()

        def work() -> dict[str, Any]:
            from .device_probe import check_device
            return check_device(token=token, **kwargs)
        if self._start_ext("device_check", work):
            self._device_status.set("正在检测设备…")

    def _scan_devices(self) -> None:
        import ipaddress
        network = self.ext["scan_net"].get().strip()
        try:
            port = int(self.ext["dev_port"].get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self._show_error("无法扫描", "RFC2217 端口应在 1–65535 之间。")
            return
        if network:
            try:
                net = ipaddress.ip_network(network, strict=False)
                if net.version != 4 or not net.is_private or net.num_addresses > 256:
                    raise ValueError
            except ValueError:
                self._show_error("无法扫描", "只能扫描 /24 或更小的私有 IPv4 网段，例如 192.168.50.0/24。")
                return

        def work() -> dict[str, Any]:
            from .device_probe import local_networks, scan_subnet
            networks = [network] if network else local_networks()[:4]
            if not networks:
                raise ValueError("未发现本机私有 IPv4 网段，请手动填写网段。")
            hosts: list[str] = []
            for item in networks:
                hosts.extend(scan_subnet(item, port))
            return {"networks": networks, "hosts": hosts}
        if self._start_ext("device_scan", work):
            self._device_status.set("正在扫描局域网…")

    # ------------------------------------------------------------ shared
    def _sync_extension_controls(self) -> None:
        if not hasattr(self, "_device_scan_button"):
            return
        busy, closing = self._ext_pending, self._closing

        def state(enabled: bool) -> str:
            return "normal" if enabled and not closing else "disabled"
        vcom_busy = bool(busy & {"vcom_detect", "vcom_apply", "pair_create", "pair_remove", "pair_wire"})
        hw = self._backend("HW VSP3 Single") or {}
        c0c = self._backend("com0com") or {}
        self._vcom_refresh_button.configure(state=state(not vcom_busy))
        self._bridge_pairs_button.configure(state=state(not vcom_busy))
        self._vcom_preview_button.configure(state=state(True))
        self._vcom_apply_button.configure(state=state(not vcom_busy and bool(hw.get("installed"))))
        self._pair_create_button.configure(state=state(not vcom_busy and bool(c0c.get("installed"))))
        self._pair_remove_button.configure(state=state(not vcom_busy and bool(c0c.get("installed")) and self._selected_pair() is not None))
        selected = self._selected_pair()
        self._pair_wire_button.configure(state=state(not vcom_busy and selected is not None and not selected.get("close_detect")))
        bridge_busy = bool(busy & {"bridge_start", "bridge_stop", "bridge_restart", "bridge_shutdown"})
        current = self._bridge.snapshot().get("state") if self._bridge is not None else None
        live = current in ("starting", "connecting", "active")
        self._bridge_start_button.configure(state=state(not bridge_busy and not live))
        self._bridge_stop_button.configure(state=state(not bridge_busy and current not in (None, "stopped")))
        self._bridge_restart_button.configure(state=state(not bridge_busy and current == "needs_reset"))
        editable = not bridge_busy and not live and not closing
        self._bridge_pair_combo.configure(state="normal" if editable else "disabled")
        for widget in (self._bridge_host_entry, self._bridge_port_entry, self._bridge_fill_button):
            widget.configure(state="normal" if editable else "disabled")
        self._device_check_button.configure(state=state("device_check" not in busy))
        self._device_scan_button.configure(state=state("device_scan" not in busy))

    def _handle_extension_reply(self, reply: Reply) -> None:
        task = reply.task
        self._ext_pending.discard(task)
        error = reply.error
        result = reply.result if isinstance(reply.result, dict) else {}
        if task == "vcom_detect":
            if error:
                self._vcom_result.set("驱动检测失败：" + error)
            else:
                self._vcom_raw = result.get("backends") or []
                self._vcom_matrix = result.get("matrix") or []
                self._vcom_suggest = str(result.get("suggest") or "")
                c0c = self._backend("com0com") or {}
                self._pairs = [{"pair": int(end[4:]), "com": com, "endpoint": end}
                               for end, com in sorted((c0c.get("channel") or {}).items()) if str(end).startswith("CNCB") and str(end)[4:].isdigit()]
                configured = str(((self._backend("HW VSP3 Single") or {}).get("channel") or {}).get("COM") or "")
                if not self.ext["vcom_com"].get():
                    self.ext["vcom_com"].set(configured or self._vcom_suggest)
                if not self.ext["bridge_pair"].get() and self._pairs:
                    self.ext["bridge_pair"].set(pair_label(self._pairs[0]))
                self._vcom_result.set("驱动检测完成（只读）。")
            self._render_vcom()
        elif task in ("vcom_apply", "pair_create", "pair_remove", "pair_wire"):
            names = {"vcom_apply": "应用 HW VSP 配置", "pair_create": "新建端口对", "pair_remove": "删除端口对",
                     "pair_wire": "启用关闭检测"}
            if error:
                outcome = f"{names[task]}失败：{error}"
            elif not result.get("ok"):
                outcome = f"{names[task]}未成功：{clean_text(result.get('error') or '—', 80)}"
            elif task in ("vcom_apply", "pair_create") and not result.get("port_present"):
                outcome = f"{names[task]}已执行，但 20 秒内未看到虚拟端口出现，请刷新检测。"
            else:
                outcome = f"{names[task]}已完成。"
            self._vcom_result.set(outcome)
            self._add_event("INFO" if result.get("ok") and not error else "ERROR", outcome)
            self._sync_controls()
            self._refresh_vcom()
            return
        elif task in ("bridge_start", "bridge_stop", "bridge_restart"):
            if error:
                self._add_event("ERROR", "com0com 桥接操作失败：" + error)
                if not self._closing:
                    self._show_error("桥接操作未完成", error)
            self._render_bridge()
        elif task == "bridge_shutdown":
            pass
        elif task == "device_check":
            self._device_error = error or ""
            self._device_raw = None if error else result
            self._device_status.set("设备检测失败。" if error else "设备检测完成。")
            self._render_device()
        elif task == "device_scan":
            if error:
                self._device_status.set("扫描失败：" + error)
            else:
                self._scan_hosts = [str(h) for h in result.get("hosts", [])][:256]
                self._device_status.set(f"扫描完成：{len(self._scan_hosts)} 台主机开放端口（{', '.join(result.get('networks', []))}）。")
                if len(self._scan_hosts) == 1:
                    self.ext["dev_host"].set(self._scan_hosts[0])
            self._render_device()
        self._sync_controls()

    def _readonly_text(self, parent: ttk.Frame, row: int, height: int) -> tk.Text:
        holder = ttk.Frame(parent, style="Card.TFrame")
        holder.grid(row=row, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        text = tk.Text(holder, height=height, width=1, wrap="word", background="#F6F8FB", foreground=_NAVY,
                       relief="flat", borderwidth=0, padx=10, pady=7, font=(self._font_family, 9),
                       state="disabled", highlightthickness=1, highlightbackground="#E1E8EF", cursor="arrow")
        text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, command=text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        return text

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root or event.width == self._last_width:
            return
        self._last_width = event.width
        for label, inset in self._wrap_labels:
            label.configure(wraplength=max(180, min(event.width - inset, label.master.winfo_width() - 24)))

    def _show_initial_error(self) -> None:
        if not self._closing:
            self._messagebox.showerror("配置文件未载入", f"{self._load_error}\n\n原文件保持不变。当前使用临时默认模拟配置；保存时必须选择其他文件。", parent=self.root)

    def _form_values(self) -> dict[str, Any]:
        return {key: value.get() for key, value in self.vars.items()}

    def _is_dirty(self) -> bool:
        return self._form_values() != self._form_baseline

    def _apply_profile(self, profile: GatewayProfile) -> None:
        self._loading_form = True
        try:
            self._base_profile = profile
            self._selected_name = profile.name
            for key, variable in self.vars.items():
                value = getattr(profile, key)
                if key == "mode":
                    value = next(label for label, code in self._modes.items() if code == value)
                elif key == "protocol":
                    value = next(label for label, code in self._protocols.items() if code == value)
                variable.set(value)
            self._profile_combo.configure(values=tuple(p.name for p in self.profiles))
            self._selected_var.set(profile.name)
            self._form_baseline = self._form_values()
        finally:
            self._loading_form = False
        self._form_changed()

    def _form_changed(self, *_: Any) -> None:
        if self._loading_form or not self._ui_ready:
            return
        self._update_config_status()
        self._refresh_connection_text()
        self._sync_controls()

    def _update_config_status(self) -> None:
        if self._load_error:
            status = "原配置文件不可读 · 原文件受保护，保存时需另选文件"
        elif self._disk_revision is None:
            status = "默认配置尚未写入文件"
        else:
            status = "配置已载入"
        if self._is_dirty():
            status = "有未保存的修改 · " + status
        self._config_status.set(status)

    def _read_profile(self, name_override: str | None = None) -> GatewayProfile:
        values = self._form_values()

        def number(key: str, title: str, integer: bool = True) -> int | float:
            try:
                return int(str(values[key]).strip()) if integer else float(str(values[key]).strip())
            except (ValueError, TypeError):
                raise ValueError(f"{title}应为{'整数' if integer else '数字'}。") from None

        try:
            mode = self._modes[str(values["mode"])]
            protocol = self._protocols[str(values["protocol"])]
        except KeyError:
            raise ValueError("请选择有效的模式和协议。") from None
        # replace 保留未展示的 notes，以及未来模型新增的所有字段。
        profile = replace(
            self._base_profile,
            name=(name_override if name_override is not None else str(values["name"])).strip(),
            mode=mode, protocol=protocol, listen_host=str(values["listen_host"]),
            listen_port=number("listen_port", "本机监听端口"),
            remote_host=str(values["remote_host"]).strip(), remote_port=number("remote_port", "远端端口"),
            nodelay=bool(values["nodelay"]), buffer_bytes=number("buffer_bytes", "缓冲区大小"),
            chunk_bytes=number("chunk_bytes", "单次读块"),
            connect_timeout_s=number("connect_timeout_s", "连接超时", False),
            stall_timeout_s=number("stall_timeout_s", "发送停滞超时", False),
            keepalive_s=number("keepalive_s", "保活间隔"),
        )
        profile.validate()
        return profile

    def _candidate_profiles(self, profile: GatewayProfile) -> list[GatewayProfile]:
        if any(item.name == profile.name and item.name != self._selected_name for item in self.profiles):
            raise ValueError("配置名称已存在，请使用其他名称。")
        return [profile if item.name == self._selected_name else item for item in self.profiles]

    def _resolve_unsaved(self, action: str) -> bool:
        if not self._is_dirty():
            return True
        answer = self._messagebox.askyesnocancel(
            "有未保存的修改", f"{action}前，是否保存当前配置的修改？\n\n是：保存后继续\n否：放弃本次修改后继续\n取消：留在当前配置", parent=self.root,
        )
        if answer is None:
            return False
        return self._save_current() if answer else True

    def _switch_profile(self, _event: tk.Event | None = None) -> None:
        wanted = self._selected_var.get()
        self._selected_var.set(self._selected_name)
        if self._running or self._gateway_busy or self._closing or wanted == self._selected_name:
            return
        if not self._resolve_unsaved("切换配置"):
            return
        profile = next((item for item in self.profiles if item.name == wanted), None)
        if profile is not None:
            self._apply_profile(profile)

    def _save_current(self) -> bool:
        try:
            profile = self._read_profile()
            candidates = self._candidate_profiles(profile)
        except ValueError as exc:
            self._show_error("配置无法保存", exc)
            return False
        return self._persist(candidates, profile.name)

    def _target_store(self) -> ProfileStore | None:
        if not self._load_error:
            return self.store
        path = self._filedialog.asksaveasfilename(
            parent=self.root, title="选择新的配置文件（原错误文件保持不变）", defaultextension=".json",
            initialfile="profiles-recovered.json", filetypes=[("JSON 配置", "*.json")],
        )
        if not path:
            return None
        store = ProfileStore(path)
        if store.path.resolve() in self._protected_paths:
            self._show_error("不能覆盖原文件", "原配置文件无法通过校验，请选择不同文件名保存。")
            return None
        return store

    def _save_store(self, store: ProfileStore, profiles: list[GatewayProfile], selected: str) -> bool:
        try:
            resolved = store.path.resolve()
            if resolved in self._protected_paths:
                raise ValueError("目标是受保护的错误配置文件，请另选文件。")
            if store.path.exists():
                # 保存按钮也不覆盖无效 JSON；导出到已有文件同样验证。
                try:
                    store.load()
                except Exception as exc:
                    self._protected_paths.add(resolved)
                    if resolved == self.store.path.resolve():
                        self._load_error = clean_text(exc)
                        self._update_config_status()
                    raise ValueError("目标文件不能通过配置校验，已拒绝覆盖；请另选文件。") from exc
            if resolved == self.store.path.resolve() and self._revision(store.path) != self._disk_revision:
                if not self._messagebox.askokcancel("配置文件已在外部变化", "磁盘上的配置文件自载入后已变化。是否用当前界面内容保存到该文件？", parent=self.root):
                    return False
            store.save(profiles, selected)
            return True
        except Exception as exc:
            self._show_error("保存失败", exc)
            return False

    def _persist(self, profiles: list[GatewayProfile], selected: str) -> bool:
        store = self._target_store()
        if store is None or not self._save_store(store, profiles, selected):
            return False
        self.store = store
        self.profiles = profiles
        self._disk_revision = self._revision(store.path)
        self._load_error = None
        self._apply_profile(next(item for item in profiles if item.name == selected))
        self._config_status.set("已保存 · " + _short(store.path.name, 60))
        return True

    def _save_as_profile(self) -> None:
        if len(self.profiles) >= 100:
            self._show_error("无法另存", "最多保留 100 个配置档，请先删除不再使用的配置。")
            return
        stem = str(self.vars["name"].get()).strip() or self._t("新配置")
        stem = stem[:65]
        names = {item.name for item in self.profiles}
        copy_label = self._t("副本")
        suggestion = stem + " " + copy_label
        suffix = 2
        while suggestion in names:
            suggestion = f"{stem} {copy_label} {suffix}"
            suffix += 1
        name = self._simpledialog.askstring("另存配置", "新配置名称（当前参数将复制到新配置）", initialvalue=suggestion, parent=self.root)
        if name is None:
            return
        try:
            profile = self._read_profile(name)
            if profile.name in names:
                raise ValueError("配置名称已存在，请使用其他名称。")
        except ValueError as exc:
            self._show_error("无法另存", exc)
            return
        self._persist([*self.profiles, profile], profile.name)

    def _delete_profile(self) -> None:
        if len(self.profiles) <= 1:
            self._show_error("无法删除", "至少需要保留一个配置档。")
            return
        warning = "\n当前配置未保存的修改也会被丢弃。" if self._is_dirty() else ""
        if not self._messagebox.askyesno("删除配置", f"删除“{_short(self._selected_name, 80)}”？{warning}\n其他配置会保留，并保存到配置文件。", parent=self.root):
            return
        index = next(i for i, item in enumerate(self.profiles) if item.name == self._selected_name)
        remaining = [item for item in self.profiles if item.name != self._selected_name]
        self._persist(remaining, remaining[min(index, len(remaining) - 1)].name)

    def _import_profiles(self) -> None:
        path = self._filedialog.askopenfilename(parent=self.root, title="导入 JSON 配置", filetypes=[("JSON 配置", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            source = Path(path)
            if not source.is_file():
                raise ValueError("所选文件不存在。")
            imported, selected = ProfileStore(source).load()
        except Exception as exc:
            self._show_error("导入失败", exc)
            return
        if not self._resolve_unsaved("导入配置"):
            return
        names = "\n".join(f"  · {_short(item.name, 80)}" for item in imported[:8])
        if len(imported) > 8:
            names += f"\n  … 共 {len(imported)} 个配置"
        if not self._messagebox.askokcancel(
            "确认导入并保存", f"配置校验通过。\n{names}\n\n选中配置：{_short(selected, 80)}\n\n确认后将保存这些配置，并替换当前配置列表。不会自动启动连接。", parent=self.root,
        ):
            return
        self._persist(imported, selected)

    def _export_profiles(self) -> None:
        try:
            profile = self._read_profile()
            profiles = self._candidate_profiles(profile)
        except ValueError as exc:
            self._show_error("配置无法导出", exc)
            return
        path = self._filedialog.asksaveasfilename(parent=self.root, title="导出全部配置（包含当前表单修改）", defaultextension=".json", initialfile="gateway-profiles.json", filetypes=[("JSON 配置", "*.json")])
        if not path:
            return
        target = ProfileStore(path)
        if target.path.resolve() == self.store.path.resolve():
            self._persist(profiles, profile.name)
        elif self._save_store(target, profiles, profile.name):
            self._add_event("INFO", "配置已导出；当前配置文件的未保存状态保持不变。")
            self._messagebox.showinfo("导出完成", "已导出全部配置，包含当前表单参数。", parent=self.root)

    def _sync_controls(self) -> None:
        if not self._ui_ready:
            return
        locked = self._running or bool(self._gateway_busy) or self._closing
        for widget, normal in self._editable:
            widget.configure(state="disabled" if locked else normal)
        remote_enabled = not locked and self._modes.get(self.vars["mode"].get()) == "device"
        for widget in (self._remote_host_entry, self._remote_port_entry):
            widget.configure(state="normal" if remote_enabled else "disabled")
        self._profile_combo.configure(state="disabled" if locked else "readonly")
        for widget in self._profile_controls:
            widget.configure(state="disabled" if locked else "normal")
        self._profile_controls[2].configure(state="normal" if not locked and len(self.profiles) > 1 else "disabled")
        self._start_button.configure(state="disabled" if locked else "normal")
        self._stop_button.configure(state="normal" if self._running and not self._gateway_busy and not self._closing else "disabled")
        can_rearm = self._snapshot.get("state") == "needs_reset" and not self._gateway_busy and not self._closing
        self._rearm_button.configure(state="normal" if can_rearm else "disabled")
        self._diagnostic_button.configure(state="disabled" if self._closing else "normal")
        self._environment_button.configure(state="disabled" if self._closing or "environment" in self._aux_pending else "normal")
        self._selftest_button.configure(state="disabled" if self._closing or "selftest" in self._aux_pending else "normal")
        self._copy_endpoint_button.configure(state="normal" if not self._closing and self._endpoint()[2] else "disabled")
        self._sync_extension_controls()
        if self._closing:
            task = "正在停止并等待后台任务收尾…"
        elif self._gateway_busy:
            task = {"start": "正在启动…", "stop": "正在停止…", "rearm": "正在重新就绪…"}.get(self._gateway_busy, "正在处理…")
        elif self._aux_pending or self._ext_pending:
            task = " / ".join([self._t("环境检查中" if key == "environment" else "自检中") for key in sorted(self._aux_pending)]
                              + [self._t(_EXT_TASKS[key]) for key in sorted(self._ext_pending) if key in _EXT_TASKS])
        else:
            task = ""
        self._task_text.set(task)

    def _endpoint(self) -> tuple[str, Any, bool]:
        if self._running:
            host = str(self._snapshot.get("listen_host") or (self._run_profile.listen_host if self._run_profile else "127.0.0.1"))
            port = self._snapshot.get("listen_port")
            usable = self._snapshot.get("state") in ("ready", "connecting", "active", "needs_reset")
        else:
            host = str(self.vars["listen_host"].get())
            port = self.vars["listen_port"].get()
            usable = True
        try:
            port = int(port)
            usable = usable and 1 <= port <= 65535
        except (ValueError, TypeError, OverflowError):
            usable = False
        return host, port, usable

    def _refresh_connection_text(self) -> None:
        host, port, usable = self._endpoint()
        if usable:
            endpoint = _address(host, port)
            heading = "本机入口" if self._running else "配置入口（启动后生效）"
            self._vcom_endpoint.set(f"{heading}：{endpoint}")
            target = endpoint
        else:
            target = "启动后显示的本机地址与端口"
            self._vcom_endpoint.set("入口端口尚未就绪；启动成功后显示实际端口。")
        if not self._running:
            label = _address(host, port) if usable else "端口待分配 / 待填写"
            self._endpoint_text.set(f"尚未启动 · {_short(label, 52)}")
        protocol = self._snapshot.get("protocol") if self._running else self._protocols.get(str(self.vars["protocol"].get()))
        if protocol is None and self._run_profile:
            protocol = self._run_profile.protocol
        protocol_help = "选择 RFC2217 / Telnet 模式，启用对应 NVT 与串口控制协商。" if protocol == "rfc2217" else "选择原始 TCP 模式，关闭 Telnet / RFC2217 / NVT 处理。"
        self._vcom_steps.set(
            "1. 在成熟的 Windows VCOM 软件中，手动创建一个空闲 COM 端口。\n"
            f"2. 启动本网关，将 VCOM 设为 TCP 客户端，连接 {target}。\n"
            f"3. {protocol_help}\n"
            "4. 在官方软件中选择该驱动创建的 COM；本界面不会创建 COM。"
        )
        simulated = self._modes.get(self.vars["mode"].get()) == "simulator"
        self._mode_hint.configure(text=self._t(
            "本机模拟无需真实串口设备，适合先检查软件链路。网关不会自动启动；启动后等待客户端接入。"
            if simulated else "远端设备模式会连接指定网络地址。停止网关后可切换或编辑配置；网络断线后需要手动重新就绪。"
        ))

    def _copy_endpoint(self) -> None:
        host, port, usable = self._endpoint()
        if usable:
            self.root.clipboard_clear()
            self.root.clipboard_append(_address(host, port))
            self._task_text.set("已复制本机入口")

    def _start(self) -> None:
        if self._running or self._gateway_busy or self._closing:
            return
        try:
            profile = self._read_profile()
        except ValueError as exc:
            self._show_error("配置无法启动", exc)
            return
        self._run_profile = profile
        self._running = True
        self._gateway_busy = "start"
        self._ui_error = ""
        self._snapshot = {"state": "starting", "mode": profile.mode, "protocol": profile.protocol, "events": []}
        self._last_engine_events = []
        self._add_event("INFO", f"启动配置“{_short(profile.name, 80)}”。")
        self._render_snapshot()
        self._sync_controls()
        self._worker.submit("start", profile)

    def _stop(self) -> None:
        if not self._running or self._gateway_busy or self._closing:
            return
        self._gateway_busy = "stop"
        self._state_text.set("正在停止")
        self._sync_controls()
        self._worker.submit("stop")

    def _rearm(self) -> None:
        if self._snapshot.get("state") != "needs_reset" or self._gateway_busy or self._closing:
            return
        if not self._messagebox.askokcancel("重新就绪", _RESET_HELP + "\n\n确认已关闭官方软件的 COM 后继续。", parent=self.root):
            return
        self._gateway_busy = "rearm"
        self._ui_error = ""
        self._sync_controls()
        self._worker.submit("rearm")

    def _poll(self) -> None:
        for _ in range(60):
            try:
                reply = self._replies.get_nowait()
            except queue.Empty:
                break
            self._handle_reply(reply)
        self._render_events()
        if time.monotonic() >= self._next_bridge_render:
            self._next_bridge_render = time.monotonic() + 0.25
            self._render_bridge()
        if self._closing:
            if self._shutdown_complete and not self._aux_pending and "bridge_shutdown" not in self._ext_pending:
                self.root.destroy()
                if self._on_closed is not None:
                    self._on_closed()
                return
            if time.monotonic() >= self._close_deadline and not self._close_warning_shown:
                self._close_warning_shown = True
                self._messagebox.showwarning(
                    "正在等待后台收尾",
                    "后台停止或检查任务尚未完成，窗口暂时保留。\n\n确认网关停止、正在运行的自检完成后才会关闭，避免遗留监听。请稍候。",
                    parent=self.root,
                )
        elif not self._gateway_busy and not self._snapshot_pending and time.monotonic() >= self._next_snapshot:
            self._snapshot_pending = True
            self._next_snapshot = time.monotonic() + 0.25
            self._worker.submit("snapshot")
        self.root.after(50, self._poll)

    def _handle_reply(self, reply: Reply) -> None:
        task = reply.task
        if task in _EXT_TASKS:
            self._handle_extension_reply(reply)
            return
        if task in ("environment", "selftest"):
            self._aux_pending.discard(task)
            if reply.error:
                self._check_results[task] = "执行失败：" + reply.error
                self._add_event("ERROR", ("环境检查" if task == "environment" else "自检") + "失败：" + reply.error)
            elif task == "environment":
                self._check_raw[task] = reply.result
                self._check_results[task] = self._environment_report(reply.result)
                self._add_event("INFO", "只读环境检查已完成；未打开任何 COM。")
            else:
                self._check_raw[task] = reply.result
                self._check_results[task] = self._selftest_report(reply.result)
                passed = isinstance(reply.result, dict) and reply.result.get("ok") is True
                self._add_event("INFO" if passed else "ERROR", "本机模拟自检通过。" if passed else "本机模拟自检未通过，请查看检查结果。")
            self._render_checks(task)
            self._sync_controls()
            return
        if task == "snapshot":
            self._snapshot_pending = False
        elif task in ("start", "stop", "rearm") and not self._closing:
            self._gateway_busy = None
        if task == "shutdown":
            if reply.error:
                self._closing = False
                self._gateway_busy = None
                self._worker.cancel_shutdown()
                self._ui_error = reply.error
                self._show_error("暂时无法关闭", reply.error + "\n\n窗口已保留，请重试停止或关闭。")
            else:
                self._shutdown_complete = True
                self._running = False
                self._snapshot = reply.result
            self._render_snapshot()
            self._sync_controls()
            return
        if reply.error:
            previous = self._ui_error
            self._ui_error = reply.error
            if reply.error != previous:
                self._add_event("ERROR", reply.error)
            if task != "snapshot" and not self._closing:
                self._show_error("网关操作未完成", reply.error)
            self._render_snapshot()
            self._sync_controls()
            return
        result = reply.result
        if task == "rearm":
            if not result.get("rearmed"):
                self._ui_error = "尚不能重新就绪。请先关闭官方软件的 COM，并确认客户端连接已断开。"
                if not self._closing:
                    self._show_error("尚未重新就绪", self._ui_error)
            else:
                self._ui_error = ""
            result = result.get("snapshot")
        if isinstance(result, dict):
            self._snapshot = result
            state = result.get("state", "stopped")
            self._running = state != "stopped"
            if task in ("start", "stop"):
                self._ui_error = ""
            self._merge_engine_events(result.get("events", []))
            self._render_snapshot()
        self._sync_controls()

    def _render_snapshot(self) -> None:
        state = str(self._snapshot.get("state", "stopped"))
        label, color = _STATES.get(state, ("状态未知", _MUTED))
        if self._gateway_busy == "stop":
            label = "正在停止"
        elif self._closing:
            label = "正在关闭" if not self._shutdown_complete else "网关已停止"
        self._state_text.set(label)
        self._state_label.configure(foreground=color)
        message = None if self._t.language == "en" else self._snapshot.get("message")
        if state == "needs_reset":
            message = _RESET_HELP
        elif not message:
            message = {
                "stopped": "选择配置后点击“启动网关”。",
                "starting": "正在准备本机 TCP 入口…",
                "ready": "入口已就绪，等待外部虚拟 COM 或 TCP 客户端连接。",
                "connecting": "正在连接远端设备，请稍候。",
                "active": "客户端已连接；可在“连接与统计”中查看传输情况。",
                "error": "请查看错误信息，停止网关后检查配置。",
            }.get(state, "等待服务状态。")
        self._message_text.set(_short(message, 210))
        error = self._ui_error or self._snapshot.get("last_error")
        if error:
            self._error_text.set("最近错误：" + _short(error, 180))
            self._error_label.grid()
        else:
            self._error_label.grid_remove()
        if state == "needs_reset":
            self._reset_label.grid()
        else:
            self._reset_label.grid_remove()
        for key, variable in self._metric_vars.items():
            variable.set(_byte_count(self._snapshot.get(key, 0)))
        capacity = self._run_profile.buffer_bytes if self._run_profile else self._base_profile.buffer_bytes
        for key, variable in self._buffer_vars.items():
            current = self._snapshot.get("queue_to_" + key, 0)
            peak = self._snapshot.get("peak_to_" + key, 0)
            variable.set(f"当前 {_byte_count(current)}  /  峰值 {_byte_count(peak)}")
            try:
                current_value = max(0, int(current))
            except (ValueError, TypeError, OverflowError):
                current_value = 0
            self._buffer_bars[key].configure(maximum=max(1, capacity), value=min(capacity, current_value))
        self._serial_requested.set(_serial_text(self._snapshot.get("serial_requested"), self._t.language))
        self._serial_confirmed.set(_serial_text(self._snapshot.get("serial_confirmed"), self._t.language))
        self._session_text.set(
            f"会话 {_short(self._snapshot.get('session_id') or '—', 18)}  ·  "
            f"接入 {self._snapshot.get('connections', 0)}  ·  断开 {self._snapshot.get('disconnects', 0)}  ·  "
            f"拒绝 {self._snapshot.get('rejected_connections', 0)}"
        )
        if self._running:
            host, port, usable = self._endpoint()
            address = _address(host, port) if usable else "入口准备中"
            mode = self._snapshot.get("mode") or (self._run_profile.mode if self._run_profile else "simulator")
            self._endpoint_text.set(("本机模拟" if mode == "simulator" else "远端设备") + " · " + _short(address, 52))
        if self._run_profile:
            prefix = "运行配置" if self._running else "上次运行"
            self._runtime_text.set(f"{prefix}：{_short(self._run_profile.name, 28)}")
        else:
            self._runtime_text.set("尚未启动")
        self._refresh_connection_text()

    def _add_event(self, level: str, message: str) -> None:
        self._events.append((datetime.now().strftime("%H:%M:%S"), level, _short(message, 1200)))
        self._events_dirty = True

    def _merge_engine_events(self, events: Any) -> None:
        if not isinstance(events, list):
            return
        current = []
        for event in events[-100:]:
            if not isinstance(event, dict):
                continue
            stamp = event.get("time", "")
            if isinstance(stamp, (int, float)):
                try:
                    stamp = datetime.fromtimestamp(stamp).strftime("%H:%M:%S")
                except (ValueError, OverflowError, OSError):
                    stamp = str(stamp)
            level = str(event.get("level", "info")).upper()
            level = "WARNING" if level == "WARN" else level
            current.append((_short(stamp, 32), level, _short(event.get("message", ""), 1200)))
        overlap = 0
        for length in range(min(len(self._last_engine_events), len(current)), 0, -1):
            if self._last_engine_events[-length:] == current[:length]:
                overlap = length
                break
        if current[overlap:]:
            self._events.extend(current[overlap:])
            self._events_dirty = True
        self._last_engine_events = current

    def _render_events(self) -> None:
        if not self._events_dirty:
            return
        position = self._log_text.yview()
        follow = not position or position[1] >= 0.98
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        for stamp, level, text in self._events:
            display_level = self._t({"INFO": "信息", "WARNING": "提醒", "ERROR": "错误", "DEBUG": "调试"}.get(level, level))
            text = self._t.event(text)
            self._log_text.insert("end", f"{stamp}  [{display_level}]  {text}\n", level)
        self._log_text.configure(state="disabled")
        if follow:
            self._log_text.see("end")
        elif position:
            self._log_text.yview_moveto(position[0])
        self._events_dirty = False

    def _run_check(self, task: str) -> None:
        if self._closing or task in self._aux_pending:
            return
        self._aux_pending.add(task)
        self._check_raw.pop(task, None)
        self._check_results[task] = "正在后台执行，请稍候…"
        self._render_checks(task)
        self._sync_controls()
        start_auxiliary(task, self._replies)

    def _environment_report(self, result: Any) -> str:
        if not isinstance(result, dict):
            return self._t("环境检查未返回有效结果。")
        lines = [f"系统：{_short(result.get('os', '未知'))}", f"Python：{_short(result.get('python', '未知'))}"]
        ports = result.get("ports", [])
        lines.append("已枚举串口（仅列出，未打开）：")
        if isinstance(ports, list) and ports:
            for item in ports:
                if isinstance(item, dict):
                    lines.append(f"  {_short(item.get('device', '未知端口'))}  {_short(item.get('description', ''))}")
                    if item.get("hwid"):
                        lines.append("    标识：" + _short(item["hwid"], 240))
        else:
            lines.append("  未发现串口，仍可使用本机模拟。")
        drivers = result.get("drivers", [])
        lines.append("检测到的驱动线索（不代表已创建 COM）：")
        if isinstance(drivers, list) and drivers:
            for item in drivers:
                if isinstance(item, dict):
                    lines.append(f"  {_short(item.get('name', '未知'))}  {_short(item.get('path', ''), 260)}")
        else:
            lines.append("  未发现；可手动检查外部 VCOM 软件。")
        messages = result.get("messages", [])
        if isinstance(messages, list):
            lines.extend("提示：" + clean_text(item, 500) for item in messages)
        return "\n".join(self._t.event(line.strip()) if line.strip() else "" for line in lines)

    def _selftest_report(self, result: Any) -> str:
        if not isinstance(result, dict):
            return self._t("自检未返回有效结果。")
        lines = ["结果：" + ("通过" if result.get("ok") is True else "未通过"), "范围：" + clean_text(result.get("scope", "独立临时本机模拟"), 500)]
        checks = result.get("checks", [])
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                verdict = "通过" if check.get("ok") is True else "失败"
                lines.append(f"[{verdict}] {_short(check.get('name', '检查项'))}")
                detail = check.get("detail")
                if detail:
                    lines.append("  " + clean_text(detail, 1600))
        if result.get("error"):
            lines.append("错误：" + clean_text(result["error"], 1200))
        elapsed = result.get("elapsed_s")
        if isinstance(elapsed, (int, float)):
            lines.append(f"用时：{elapsed:.2f} 秒")
        lines.append("仅验证本机模拟链路，真实硬件未验证。")
        return "\n".join(self._t.event(line.strip()) if line.strip() else "" for line in lines)

    def _render_checks(self, focus: str | None = None) -> None:
        self._checks_text.configure(state="normal")
        self._checks_text.delete("1.0", "end")
        self._checks_text.insert("end", self._t("环境检查") + "\n" + self._translated_check("environment") + "\n\n")
        self._checks_text.mark_set("selftest_start", "end-1c")
        self._checks_text.mark_gravity("selftest_start", "left")
        self._checks_text.insert("end", self._t("运行自检") + "\n" + self._translated_check("selftest"))
        self._checks_text.configure(state="disabled")
        if focus == "selftest":
            self._checks_text.yview("selftest_start")
        elif focus == "environment":
            self._checks_text.yview_moveto(0)

    def _export_diagnostics(self) -> None:
        try:
            profile = self._run_profile or self._read_profile()
        except ValueError as exc:
            self._show_error("无法导出诊断", exc)
            return
        document = diagnostic_document(self._snapshot, profile, self._t.language)
        document["profile_source"] = "last_run" if self._run_profile else "current_form"
        extend_diagnostics(document, self._vcom_raw, self._bridge.snapshot() if self._bridge else None, self._device_raw)
        path = self._filedialog.asksaveasfilename(parent=self.root, title="导出网关诊断（不含载荷和配置备注）", defaultextension=".json", initialfile=f"gateway-diagnostic-{datetime.now():%Y%m%d-%H%M%S}.json", filetypes=[("JSON 诊断", "*.json")])
        if not path:
            return
        destination = Path(path)
        if destination.resolve() == self.store.path.resolve() or destination.resolve() in self._protected_paths:
            self._show_error("不能覆盖配置", "请为诊断文件使用不同的文件名。")
            return
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=".gateway-diagnostic-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            self._add_event("INFO", "诊断已导出；包含服务快照和对应配置，不含传输载荷或配置备注。")
            self._messagebox.showinfo("诊断已导出", "已导出服务快照与对应配置，以及虚拟 COM 检测、桥接状态和最近一次设备检测摘要。传输载荷、令牌、配置备注及未列入诊断白名单的字段不会导出。", parent=self.root)
        except Exception as exc:
            self._show_error("诊断导出失败", exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _open_help(self) -> None:
        window = tk.Toplevel(self.root)
        window.title(self._t("使用说明 · " + _TITLE))
        window.geometry("720x550")
        window.minsize(560, 420)
        window.transient(self.root)
        window.configure(background=_BG)
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)
        frame = ttk.Frame(window, style="Card.TFrame", padding=18)
        frame.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text = self._readonly_text(frame, 0, 20)
        contents = self._t(HELP_ZH, path=self.store.path)
        text.configure(state="normal")
        text.insert("1.0", contents)
        text.configure(state="disabled")
        ttk.Button(window, text=self._t("关闭说明"), command=window.destroy).grid(row=1, column=0, pady=12)
        window.bind("<Escape>", lambda _event: window.destroy())

    def _request_close(self) -> None:
        if self._closing:
            return
        if not self._resolve_unsaved("关闭窗口"):
            return
        self._closing = True
        self._shutdown_complete = False
        self._close_deadline = time.monotonic() + 5.0
        self._close_warning_shown = False
        self._gateway_busy = "shutdown"
        self._worker.submit("shutdown")
        bridge = self._bridge
        if bridge is not None and bridge.snapshot().get("state") != "stopped" and "bridge_shutdown" not in self._ext_pending:
            self._ext_pending.add("bridge_shutdown")
            run_background("bridge_shutdown", bridge.stop, self._replies)
        self._render_snapshot()
        self._sync_controls()

    def _show_error(self, title: str, error: Any) -> None:
        messagebox.showerror(self._t(title), self._t.error(error), parent=self.root)


def main(config_path: str | None = None, language: str | None = None) -> None:
    """打开可切换中英文的桌面界面；不自动连接或枚举硬件。"""
    root = tk.Tk()
    _GatewayApp(root, config_path, language)
    root.mainloop()


if __name__ == "__main__":
    import sys
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--language")
    selected_language = preliminary.parse_known_args()[0].language
    tr = Translator(selected_language)
    parser = argparse.ArgumentParser(description=tr("串口网络网关桌面界面"))
    parser.add_argument("--config", metavar=tr("文件"), help=tr("使用指定 JSON 配置文件"))
    parser.add_argument("--language", choices=tuple(LANGUAGES), help=tr("界面语言（默认读取已保存偏好）"))
    options = parser.parse_args()
    main(options.config, options.language)
