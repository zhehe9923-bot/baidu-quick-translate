"""图形界面：主窗口、翻译结果弹窗、设置对话框。"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import messagebox, ttk

import baidu_api
import config as config_mod
import hotkey as hotkey_mod
import selection
import tray as tray_mod
import updater
import usage
import winutil
from version import APP_VERSION

FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimHei")
SETUP_URL = "https://api.fanyi.baidu.com/"
DOC_URL = "https://api.fanyi.baidu.com/doc/21"

C = {
    "bg": "#eef1f8",
    "card": "#ffffff",
    "border": "#d8deea",
    "text": "#1f2430",
    "muted": "#6b7280",
    "accent": "#2932e1",
    "accent_hover": "#1f28c8",
    "accent_press": "#1820a8",
    "ok": "#0f9d58",
    "warn": "#d97706",
    "err": "#dc2626",
    "soft": "#f5f7fd",
}


# --------------------------------------------------------------------------- 小工具
def pick_font(root: tk.Misc) -> str:
    try:
        families = set(tkfont.families(root))
    except tk.TclError:
        return "Microsoft YaHei UI"
    for name in FONT_CANDIDATES:
        if name in families:
            return name
    return "TkDefaultFont"


def set_window_icon(window: tk.Tk) -> tk.PhotoImage | None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    if not os.path.isfile(path):
        return None
    try:
        image = tk.PhotoImage(file=path)
        window.iconphoto(True, image)
        return image
    except tk.TclError:
        return None


def make_button(parent, text, command, kind="secondary", font=None, padx=16, pady=8):
    """统一风格的扁平按钮。"""
    if kind == "primary":
        bg, fg, hover, press = C["accent"], "#ffffff", C["accent_hover"], C["accent_press"]
    elif kind == "ghost":
        bg, fg, hover, press = C["card"], C["muted"], C["soft"], C["border"]
    elif kind == "danger":
        bg, fg, hover, press = C["card"], C["err"], "#fef2f2", "#fee2e2"
    else:
        bg, fg, hover, press = C["soft"], C["text"], "#e8edf9", C["border"]
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=press,
        activeforeground=fg,
        relief="flat",
        bd=0,
        highlightthickness=0,
        padx=padx,
        pady=pady,
        cursor="hand2",
        font=font,
    )
    button.bind("<Enter>", lambda _e: button.config(bg=hover))
    button.bind("<Leave>", lambda _e: button.config(bg=bg))
    return button


def place_near_cursor(window: tk.Toplevel, cursor: tuple[int, int] | None = None,
                      offset: tuple[int, int] = (18, 22)) -> None:
    """把弹窗放在鼠标附近，并保证完整显示在当前屏幕内。"""
    window.update_idletasks()
    x, y = cursor if cursor else winutil.get_cursor_pos()
    width = max(window.winfo_reqwidth(), window.winfo_width())
    height = max(window.winfo_reqheight(), window.winfo_height())
    left, top, right, bottom = winutil.get_work_area(x, y)
    pos_x = x + offset[0]
    pos_y = y + offset[1]
    if pos_x + width > right - 8:
        pos_x = max(left + 8, x - width - offset[0])
    if pos_y + height > bottom - 8:
        pos_y = max(top + 8, y - height - offset[1])
    window.geometry(f"+{int(pos_x)}+{int(pos_y)}")


def estimate_lines(text: str, width_chars: int = 42) -> int:
    lines = 0
    for part in (text or "").split("\n"):
        if not part:
            lines += 1
        else:
            lines += max(1, math.ceil(len(part) / width_chars))
    return max(1, min(12, lines))


def shorten(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------- 提示气泡
class Toast(tk.Toplevel):
    """屏幕角落的小提示，自动消失。"""

    def __init__(self, master, text, kind="info", ms=2400, anchor=None):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.96)
        bg = {"info": "#1f2430", "err": "#b91c1c", "ok": "#065f46"}.get(kind, "#1f2430")
        frame = tk.Frame(self, bg=bg, padx=16, pady=10)
        frame.pack()
        tk.Label(
            frame,
            text=text,
            bg=bg,
            fg="#ffffff",
            justify="left",
            wraplength=420,
            font=(pick_font(self), 10),
        ).pack()
        self.update_idletasks()
        left, top, right, bottom = winutil.get_work_area()
        if anchor is None:
            anchor = winutil.get_cursor_pos()
        x, y = anchor
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        pos_x = min(max(left + 8, x + 12), right - width - 12)
        pos_y = min(max(top + 8, y + 18), bottom - height - 12)
        self.geometry(f"+{int(pos_x)}+{int(pos_y)}")
        self.deiconify()
        self.after(max(800, ms), self._close)

    def _close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------- 等待提示
class BusyPopup(tk.Toplevel):
    """翻译进行中的小提示。"""

    def __init__(self, master, cursor: tuple[int, int], font: str):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.96)
        frame = tk.Frame(self, bg=C["accent"], padx=16, pady=9)
        frame.pack()
        self.label = tk.Label(frame, text="百度翻译中", bg=C["accent"], fg="#ffffff", font=(font, 10))
        self.label.pack()
        self._dots = 0
        self._alive = True
        self.update_idletasks()
        self.geometry(f"+{cursor[0] + 18}+{cursor[1] + 22}")
        self.deiconify()
        self._tick()

    def _tick(self):
        if not self._alive:
            return
        try:
            if not self.winfo_exists():
                return
            self._dots = (self._dots + 1) % 4
            self.label.config(text="百度翻译中" + "." * self._dots)
            self.after(260, self._tick)
        except tk.TclError:
            self._alive = False

    def close(self):
        self._alive = False
        try:
            self.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------- 结果弹窗
class ResultPopup(tk.Toplevel):
    """显示翻译结果的浮窗。"""

    def __init__(self, master, translation: baidu_api.Translation, cfg: dict, font: str):
        super().__init__(master)
        self.translation = translation
        self.cfg = cfg
        self.font = font
        self._drag_offset = (0, 0)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", float(cfg.get("opacity", 0.97)))
        self.configure(bg=C["border"])

        card = tk.Frame(self, bg=C["card"], padx=16, pady=12)
        card.pack(fill="both", expand=True, padx=1, pady=1)

        # ---- 标题行（可拖动）
        header = tk.Frame(card, bg=C["card"])
        header.pack(fill="x")
        title = tk.Label(header, text="百度翻译", bg=C["card"], fg=C["accent"],
                         font=(font, 10, "bold"))
        title.pack(side="left")
        tk.Label(header, text="·", bg=C["card"], fg=C["border"], font=(font, 10)).pack(side="left", padx=4)
        tk.Label(header, text=translation.lang_label, bg=C["card"], fg=C["muted"],
                 font=(font, 9)).pack(side="left")
        if translation.service != "general":
            extra = translation.service_short
            if translation.domain:
                extra += f"·{translation.domain_name}"
            tk.Label(header, text=extra, bg=C["card"], fg=C["accent"],
                     font=(font, 8)).pack(side="left", padx=(6, 0))
        close = tk.Label(header, text="✕", bg=C["card"], fg=C["muted"], font=(font, 11),
                         cursor="hand2", padx=4)
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())
        close.bind("<Enter>", lambda _e: close.config(fg=C["err"]))
        close.bind("<Leave>", lambda _e: close.config(fg=C["muted"]))
        for widget in (header, title):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)

        # ---- 原文
        self.source_label = None
        if cfg.get("show_source", True):
            self.source_label = tk.Label(
                card,
                text=shorten(translation.source),
                bg=C["card"],
                fg=C["muted"],
                font=(font, 9),
                justify="left",
                anchor="w",
                wraplength=440,
            )
            self.source_label.pack(fill="x", pady=(8, 0))
            tk.Frame(card, bg=C["border"], height=1).pack(fill="x", pady=(9, 8))

        # ---- 译文
        height = estimate_lines(translation.target)
        self.text = tk.Text(
            card,
            height=height,
            width=44,
            wrap="word",
            bg=C["card"],
            fg=C["text"],
            font=(font, 11),
            relief="flat",
            bd=0,
            highlightthickness=0,
            spacing1=2,
            spacing3=2,
            cursor="arrow",
            insertwidth=0,
        )
        self.text.insert("1.0", translation.target)
        self.text.configure(state="disabled")
        self.text.pack(fill="both", expand=True)
        self.text.bind("<Control-c>", lambda _e: self._copy_selected())

        # ---- 底部按钮
        footer = tk.Frame(card, bg=C["card"])
        footer.pack(fill="x", pady=(10, 0))
        self.copy_btn = make_button(
            footer, "复制译文", self._copy_translation, "primary", font=(font, 9), padx=12, pady=5
        )
        self.copy_btn.pack(side="left")
        copy_src = make_button(
            footer, "复制原文", self._copy_source, "secondary", font=(font, 9), padx=12, pady=5
        )
        copy_src.pack(side="left", padx=(8, 0))
        close_btn = make_button(
            footer, "关闭", self.close, "ghost", font=(font, 9), padx=12, pady=5
        )
        close_btn.pack(side="left", padx=(8, 0))
        tk.Label(footer, text="Esc 关闭", bg=C["card"], fg=C["muted"],
                 font=(font, 8)).pack(side="right")

        self.bind("<Escape>", lambda _e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

        if cfg.get("auto_copy", True):
            selection.set_clipboard_text(translation.target)

        self.deiconify()
        # 需要真正 update 一次，Text 控件完成布局后 displaylines 才能算准
        self.update()
        self._fit_text_height()
        place_near_cursor(self)
        try:
            self.focus_force()
            self.text.focus_set()
        except tk.TclError:
            pass

        seconds = int(cfg.get("popup_seconds", 0) or 0)
        if seconds > 0:
            self.after(seconds * 1000, self.close)

    # ------------------------------------------------------------------ 行为
    def _fit_text_height(self) -> None:
        """按实际排版行数设置 Text 高度，避免译文被截断。"""
        lines = estimate_lines(self.translation.target)
        try:
            # count(...,"displaylines") 返回的是「行间隔数」，所以行数要 +1
            result = self.text.count("1.0", "end-1c", "displaylines")
            if isinstance(result, tuple) and result:
                lines = int(result[0]) + 1
            elif isinstance(result, int):
                lines = result + 1
            else:
                lines = 1
        except (tk.TclError, TypeError, IndexError):
            pass
        try:
            self.text.configure(height=max(1, min(12, lines)))
        except tk.TclError:
            pass
    def _start_drag(self, event):
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag(self, event):
        self.geometry(f"+{event.x_root - self._drag_offset[0]}+{event.y_root - self._drag_offset[1]}")

    def _flash_copied(self, button, text="已复制 ✓"):
        original = button.cget("text")
        button.config(text=text)
        self.after(1200, lambda: button.config(text=original) if button.winfo_exists() else None)

    def _copy_translation(self):
        if selection.set_clipboard_text(self.translation.target):
            self._flash_copied(self.copy_btn)

    def _copy_source(self):
        if selection.set_clipboard_text(self.translation.source):
            Toast(self.master, "已复制原文", kind="ok", ms=1200)

    def _copy_selected(self):
        try:
            text = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            text = self.translation.target
        if text:
            selection.set_clipboard_text(text)
        return "break"

    def close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------- 设置对话框
class SettingsDialog(tk.Toplevel):
    """APPID / 密钥 / 快捷键 / 弹窗等设置。"""

    def __init__(self, master, cfg: dict, font: str, on_saved, first_run: bool = False):
        super().__init__(master)
        self.cfg = dict(cfg)
        self.font = font
        self.on_saved = on_saved
        self.first_run = first_run
        self._recording = False
        self._test_thread: threading.Thread | None = None

        self.title("设置" if not first_run else "首次使用 · 填写百度翻译密钥")
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.appid_var = tk.StringVar(value=self.cfg.get("appid", ""))
        self.secret_var = tk.StringVar(value=self.cfg.get("secret", ""))
        self.hotkey_var = tk.StringVar(value=self.cfg.get("hotkey", "ctrl+alt+t"))
        self.timeout_var = tk.StringVar(value=str(self.cfg.get("request_timeout", 10)))
        self.popup_seconds_var = tk.StringVar(value=str(self.cfg.get("popup_seconds", 0)))
        self.opacity_var = tk.StringVar(value=f"{float(self.cfg.get('opacity', 0.97)):.2f}")
        self.fallback_var = tk.BooleanVar(value=bool(self.cfg.get("fallback_clipboard", True)))
        self.auto_copy_var = tk.BooleanVar(value=bool(self.cfg.get("auto_copy", True)))
        self.show_source_var = tk.BooleanVar(value=bool(self.cfg.get("show_source", True)))
        self.tray_var = tk.BooleanVar(value=bool(self.cfg.get("minimize_to_tray", True)))
        self.start_min_var = tk.BooleanVar(value=bool(self.cfg.get("start_minimized", False)))
        self.show_secret_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self.target_display, self.display_to_code = self._language_choices()
        self.service_display, self.service_to_code = self._service_choices()
        self.domain_display, self.domain_to_code = self._domain_choices()
        self.quota_var = tk.StringVar(value="")

        self._build()
        self._center(820, 0)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._save())
        self.after(120, self._focus_first)

    # ------------------------------------------------------------------ 语言列表
    def _language_choices(self):
        labels: list[str] = []
        mapping: dict[str, str] = {}
        for code, name in baidu_api.LANGUAGES:
            display = "自动（中 ↔ 英）" if code == "auto" else name
            labels.append(display)
            mapping[display] = code
        current = self.cfg.get("target_mode", "auto")
        current_label = next((k for k, v in mapping.items() if v == current), "自动（中 ↔ 英）")
        self.target_var = tk.StringVar(value=current_label)
        return labels, mapping

    def _service_choices(self):
        labels = [name for _code, name in baidu_api.SERVICES]
        mapping = {name: code for code, name in baidu_api.SERVICES}
        current = baidu_api.normalize_service(self.cfg.get("service", baidu_api.DEFAULT_SERVICE))
        self.service_var = tk.StringVar(value=baidu_api.SERVICE_NAME[current])
        return labels, mapping

    def _domain_choices(self):
        labels = [name for _code, name in baidu_api.DOMAINS]
        mapping = {name: code for code, name in baidu_api.DOMAINS}
        current = baidu_api.normalize_domain(self.cfg.get("domain", baidu_api.DEFAULT_DOMAIN))
        self.domain_var = tk.StringVar(value=baidu_api.DOMAIN_NAME[current])
        return labels, mapping

    # ------------------------------------------------------------------ 布局
    def _build(self):
        outer = tk.Frame(self, bg=C["bg"], padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        title = tk.Label(
            outer,
            text="百度翻译 · 划词翻译助手",
            bg=C["bg"],
            fg=C["text"],
            font=(self.font, 15, "bold"),
            anchor="w",
        )
        title.pack(fill="x")
        subtitle = (
            "需要百度翻译开放平台的 APPID 和密钥：登录后进入「管理控制台 → 我的服务」开通需要的翻译服务"
            "（通用文本翻译 / 大模型文本翻译 / 领域文本翻译），再在下方选择对应服务。"
            if self.first_run
            else "修改后点击「保存」生效。"
        )
        tk.Label(
            outer,
            text=subtitle,
            bg=C["bg"],
            fg=C["muted"],
            font=(self.font, 9),
            justify="left",
            anchor="w",
            wraplength=710,
        ).pack(fill="x", pady=(4, 10))

        columns = tk.Frame(outer, bg=C["bg"])
        columns.pack(fill="both", expand=True)
        left = self._card(columns)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = self._card(columns)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self._build_api_card(left)
        self._build_usage_card(right)

        # ---- 底部按钮
        footer = tk.Frame(outer, bg=C["bg"])
        footer.pack(fill="x", pady=(14, 0))
        save = make_button(footer, "保存", self._save, "primary", font=(self.font, 10), padx=26, pady=8)
        save.pack(side="right")
        cancel = make_button(footer, "取消", self._cancel, "secondary", font=(self.font, 10), padx=20, pady=8)
        cancel.pack(side="right", padx=(0, 10))
        if self.first_run:
            tk.Button(
                footer,
                text="没有账号？先去申请（免费）",
                command=lambda: webbrowser.open(SETUP_URL),
                bg=C["bg"],
                fg=C["accent"],
                activebackground=C["bg"],
                activeforeground=C["accent_press"],
                relief="flat",
                bd=0,
                highlightthickness=0,
                cursor="hand2",
                font=(self.font, 9, "underline"),
            ).pack(side="left")

    def _card(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=C["card"], padx=16, pady=14, highlightthickness=1,
                         highlightbackground=C["border"])
        return frame

    def _section_title(self, parent, text):
        tk.Label(parent, text=text, bg=C["card"], fg=C["text"],
                 font=(self.font, 10, "bold"), anchor="w").pack(fill="x", pady=(0, 8))

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=C["card"], fg=C["muted"], font=(self.font, 9), anchor="w")

    def _entry(self, parent, textvariable, show=None):
        entry = tk.Entry(
            parent,
            textvariable=textvariable,
            show=show or "",
            bg=C["soft"],
            fg=C["text"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["accent"],
            insertbackground=C["text"],
            font=(self.font, 10),
        )
        return entry

    def _build_api_card(self, parent):
        self._section_title(parent, "① 翻译服务与接口密钥")

        self._label(parent, "翻译服务（需在控制台开通对应服务）").pack(fill="x")
        self.service_combo = ttk.Combobox(
            parent,
            textvariable=self.service_var,
            values=self.service_display,
            state="readonly",
            font=(self.font, 9),
        )
        self.service_combo.pack(fill="x", ipady=3, pady=(3, 6))
        self.service_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_service_fields())

        self._label(parent, "领域（仅「领域文本翻译」使用）").pack(fill="x")
        self.domain_combo = ttk.Combobox(
            parent,
            textvariable=self.domain_var,
            values=self.domain_display,
            state="readonly",
            font=(self.font, 9),
        )
        self.domain_combo.pack(fill="x", ipady=3, pady=(3, 4))
        tk.Label(
            parent,
            text="领域翻译需开通，且仅支持中英互译。",
            bg=C["card"],
            fg=C["muted"],
            font=(self.font, 8),
            justify="left",
            wraplength=340,
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        quota_line = tk.Frame(parent, bg=C["card"])
        quota_line.pack(fill="x", pady=(0, 2))
        self._label(quota_line, "每月免费额度（字符，0=不估算）").pack(side="left")
        self.quota_entry = self._entry(quota_line, self.quota_var)
        self.quota_entry.configure(width=12)
        self.quota_entry.pack(side="right", ipady=4)
        tk.Label(
            parent,
            text="仅本机估算，实际额度以控制台为准。",
            bg=C["card"],
            fg=C["muted"],
            font=(self.font, 8),
            justify="left",
            wraplength=340,
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(0, 8))

        self._label(parent, "APPID").pack(fill="x")
        self.appid_entry = self._entry(parent, self.appid_var)
        self.appid_entry.pack(fill="x", ipady=6, pady=(3, 10))

        secret_head = tk.Frame(parent, bg=C["card"])
        secret_head.pack(fill="x")
        self._label(secret_head, "密钥").pack(side="left")
        tk.Checkbutton(
            secret_head,
            text="显示密钥",
            variable=self.show_secret_var,
            command=self._toggle_secret,
            bg=C["card"],
            fg=C["muted"],
            activebackground=C["card"],
            activeforeground=C["text"],
            selectcolor=C["card"],
            font=(self.font, 8),
            bd=0,
            highlightthickness=0,
        ).pack(side="right")
        self.secret_entry = self._entry(parent, self.secret_var, show="*")
        self.secret_entry.pack(fill="x", ipady=6, pady=(3, 0))

        actions = tk.Frame(parent, bg=C["card"])
        actions.pack(fill="x", pady=(10, 0))
        self.test_btn = make_button(
            actions, "测试连接", self._test_connection, "primary", font=(self.font, 9), padx=14, pady=6
        )
        self.test_btn.pack(side="left")
        tk.Button(
            actions,
            text="申请 / 管理密钥 ↗",
            command=lambda: webbrowser.open(SETUP_URL),
            bg=C["card"],
            fg=C["accent"],
            activebackground=C["card"],
            activeforeground=C["accent_press"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=(self.font, 9, "underline"),
        ).pack(side="left", padx=(12, 0))
        timeout_box = tk.Frame(actions, bg=C["card"])
        timeout_box.pack(side="right")
        tk.Label(
            timeout_box, text="超时(秒)", bg=C["card"], fg=C["muted"], font=(self.font, 8)
        ).pack(side="left", padx=(0, 4))
        tk.Spinbox(
            timeout_box,
            from_=3,
            to=60,
            textvariable=self.timeout_var,
            width=4,
            bg=C["soft"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["border"],
            font=(self.font, 9),
        ).pack(side="left", ipady=2)

        self.status_label = tk.Label(
            parent,
            textvariable=self.status_var,
            bg=C["card"],
            fg=C["muted"],
            font=(self.font, 9),
            justify="left",
            anchor="w",
            wraplength=340,
        )
        self.status_label.pack(fill="x", pady=(8, 0))

        tk.Label(
            parent,
            text="密钥只存本机；额度用完需到控制台购买。",
            bg=C["card"],
            fg=C["muted"],
            font=(self.font, 8),
            justify="left",
            anchor="w",
            wraplength=340,
        ).pack(fill="x", pady=(12, 0))

        self._update_service_fields()

    def _build_usage_card(self, parent):
        self._section_title(parent, "② 使用与弹窗")

        self._label(parent, "全局快捷键（选中文字后按它翻译）").pack(fill="x")
        row = tk.Frame(parent, bg=C["card"])
        row.pack(fill="x", pady=(3, 6))
        self.hotkey_entry = self._entry(row, self.hotkey_var)
        self.hotkey_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.record_btn = make_button(
            row, "录制", self._toggle_record, "secondary", font=(self.font, 9), padx=12, pady=5
        )
        self.record_btn.pack(side="left", padx=(8, 0))
        tk.Label(
            parent,
            text="点「录制」后直接按下组合键；也可手动输入，例如 ctrl+alt+t",
            bg=C["card"],
            fg=C["muted"],
            font=(self.font, 8),
            justify="left",
            wraplength=340,
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        self._label(parent, "目标语言").pack(fill="x")
        combo = ttk.Combobox(
            parent,
            textvariable=self.target_var,
            values=self.target_display,
            state="readonly",
            font=(self.font, 9),
        )
        combo.pack(fill="x", ipady=3, pady=(3, 12))

        self._check(parent, "没抓到选中文字时翻译剪贴板内容", self.fallback_var).pack(fill="x", pady=2)
        self._check(parent, "译文出现后自动复制到剪贴板", self.auto_copy_var).pack(fill="x", pady=2)
        self._check(parent, "弹窗里显示原文", self.show_source_var).pack(fill="x", pady=2)

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(10, 10))

        self._label(parent, "托盘常驻").pack(fill="x")
        self._check(parent, "关闭窗口时收进托盘（不退出程序）", self.tray_var).pack(fill="x", pady=2)
        self._check(parent, "启动时只显示托盘图标", self.start_min_var).pack(fill="x", pady=2)
        tk.Label(
            parent,
            text="托盘图标：左键单击唤回窗口，右键弹出菜单（含「退出」）。",
            bg=C["card"],
            fg=C["muted"],
            font=(self.font, 8),
            justify="left",
            wraplength=340,
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(10, 12))

        line = tk.Frame(parent, bg=C["card"])
        line.pack(fill="x")
        self._label(line, "弹窗自动关闭（秒，0 = 不关）").pack(side="left")
        tk.Spinbox(
            line,
            from_=0,
            to=600,
            textvariable=self.popup_seconds_var,
            width=6,
            bg=C["soft"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["border"],
            font=(self.font, 10),
        ).pack(side="right", ipady=3)

        line2 = tk.Frame(parent, bg=C["card"])
        line2.pack(fill="x", pady=(10, 0))
        self._label(line2, "弹窗不透明度").pack(side="left")
        tk.Spinbox(
            line2,
            from_=0.4,
            to=1.0,
            increment=0.05,
            textvariable=self.opacity_var,
            width=6,
            bg=C["soft"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=C["border"],
            font=(self.font, 10),
        ).pack(side="right", ipady=3)

    def _check(self, parent, text, variable):
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            bg=C["card"],
            fg=C["text"],
            activebackground=C["card"],
            activeforeground=C["text"],
            selectcolor=C["card"],
            font=(self.font, 9),
            anchor="w",
            padx=0,
            bd=0,
            highlightthickness=0,
        )

    def _center(self, width: int, height: int = 0):
        """居中放置；height<=0 时按内容自适应高度，避免内容被裁掉。"""
        self.update_idletasks()
        if height <= 0:
            height = max(520, self.winfo_reqheight())
        width = max(width, self.winfo_reqwidth())
        left, top, right, bottom = winutil.get_work_area()
        height = min(height, max(480, bottom - top - 40))
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _focus_first(self):
        try:
            self.appid_entry.focus_set()
        except (AttributeError, tk.TclError):
            pass

    # ------------------------------------------------------------------ 交互
    def _selected_service(self) -> str:
        return self.service_to_code.get(self.service_var.get(), baidu_api.DEFAULT_SERVICE)

    def _refresh_quota_var(self):
        """显示当前服务的每月免费额度。"""
        service = self._selected_service()
        default = config_mod.DEFAULT_QUOTAS.get(service, 0)
        try:
            value = int(self.cfg.get(f"quota_{service}", default))
        except (TypeError, ValueError):
            value = default
        try:
            self.quota_var.set(str(value))
        except tk.TclError:
            pass

    def _update_service_fields(self):
        """切换服务时：领域下拉框可用性 + 额度输入框同步。"""
        code = self._selected_service()
        try:
            self.domain_combo.configure(state="readonly" if code == "domain" else "disabled")
        except tk.TclError:
            pass
        self._refresh_quota_var()

    def _toggle_secret(self):
        self.secret_entry.config(show="" if self.show_secret_var.get() else "*")

    def _toggle_record(self):
        if self._recording:
            self._stop_record()
            return
        self._recording = True
        self.record_btn.config(text="请按键…", bg=C["warn"], fg="#ffffff")
        self.bind("<KeyPress>", self._on_key_press)
        self.focus_force()
        self.hotkey_entry.focus_set()

    def _stop_record(self):
        self._recording = False
        self.record_btn.config(text="录制", bg=C["soft"], fg=C["text"])
        try:
            self.unbind("<KeyPress>")
        except tk.TclError:
            pass

    def _on_key_press(self, event):
        if not self._recording:
            return "break"
        keysym = (event.keysym or "").lower()
        if keysym in ("control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r",
                      "win_l", "win_r", "meta_l", "meta_r", "super_l", "super_r"):
            return "break"
        if keysym in ("escape", "esc"):
            self._stop_record()
            return "break"

        parts: list[str] = []
        state = int(event.state or 0)
        if state & 0x0004:
            parts.append("ctrl")
        if state & (0x0008 | 0x20000):
            parts.append("alt")
        if state & 0x0001:
            parts.append("shift")

        key_map = {
            "space": "space",
            "tab": "tab",
            "return": "enter",
            "backspace": "backspace",
            "delete": "delete",
            "insert": "insert",
            "home": "home",
            "end": "end",
            "prior": "pageup",
            "next": "pagedown",
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "minus": "minus",
            "equal": "equal",
            "comma": "comma",
            "period": "period",
            "slash": "slash",
            "backslash": "backslash",
            "semicolon": "semicolon",
            "apostrophe": "quote",
            "quoteleft": "backquote",
            "bracketleft": "bracketleft",
            "bracketright": "bracketright",
        }
        if keysym in key_map:
            key = key_map[keysym]
        elif keysym.startswith("f") and keysym[1:].isdigit():
            key = keysym
        elif len(keysym) == 1 and keysym.isalnum():
            key = keysym
        else:
            return "break"

        combo = "+".join(parts + [key])
        try:
            hotkey_mod.parse_hotkey(combo)
        except ValueError:
            self.status_var.set("快捷键需要带 Ctrl / Alt / Shift，例如 ctrl+alt+t")
            return "break"
        self.hotkey_var.set(combo)
        self._stop_record()
        return "break"

    def _test_connection(self):
        appid = self.appid_var.get().strip()
        secret = self.secret_var.get().strip()
        if not appid or not secret:
            self._set_status("请先填写 APPID 和密钥", C["err"])
            return
        service = self.service_to_code.get(self.service_var.get(), baidu_api.DEFAULT_SERVICE)
        domain = self.domain_to_code.get(self.domain_var.get(), baidu_api.DEFAULT_DOMAIN)
        if service == "domain":
            # 领域翻译只支持中英互译
            mode = self.display_to_code.get(self.target_var.get(), "auto")
            if mode not in ("auto", "zh", "en"):
                self._set_status("领域文本翻译只支持中英互译，请先把目标语言改成「自动」或中文/英语", C["err"])
                return
        if self._test_thread and self._test_thread.is_alive():
            return
        self.test_btn.config(state="disabled", text="测试中…")
        self._set_status(f"正在测试「{baidu_api.SERVICE_NAME.get(service)}」…", C["muted"])

        def worker():
            try:
                translator = baidu_api.BaiduTranslator(
                    appid, secret, float(self.timeout_var.get() or 10),
                    service=service, domain=domain,
                )
                result = translator.test()
                message = f"连接成功（{result.service_name}）：hello → {result.target}"
                self.after(0, lambda: self._set_status(message, C["ok"]))
            except baidu_api.BaiduTranslateError as exc:
                self.after(0, lambda e=exc: self._set_status(str(e), C["err"]))
            except Exception as exc:  # 兜底，避免线程静默失败
                self.after(0, lambda e=exc: self._set_status(f"测试失败：{e}", C["err"]))
            finally:
                self.after(0, lambda: self.test_btn.config(state="normal", text="测试连接"))

        self._test_thread = threading.Thread(target=worker, daemon=True)
        self._test_thread.start()

    def _set_status(self, text: str, color: str):
        self.status_var.set(text)
        try:
            self.status_label.config(fg=color)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 保存
    def _collect(self) -> dict | None:
        appid = self.appid_var.get().strip()
        secret = self.secret_var.get().strip()
        if bool(appid) != bool(secret):
            self._set_status("APPID 和密钥需要同时填写", C["err"])
            return None
        combo = self.hotkey_var.get().strip().lower()
        try:
            hotkey_mod.parse_hotkey(combo)
        except ValueError as exc:
            self._set_status(f"快捷键无效：{exc}", C["err"])
            return None
        try:
            timeout = int(float(self.timeout_var.get()))
        except ValueError:
            timeout = 10
        try:
            seconds = int(float(self.popup_seconds_var.get()))
        except ValueError:
            seconds = 0
        try:
            opacity = float(self.opacity_var.get())
        except ValueError:
            opacity = 0.97
        try:
            quota = int(float(self.quota_var.get().replace(",", "").strip() or "0"))
        except ValueError:
            quota = 0
        quota = max(0, min(1_000_000_000, quota))
        service = self.service_to_code.get(self.service_var.get(), baidu_api.DEFAULT_SERVICE)
        quotas = {
            key: int(self.cfg.get(f"quota_{key}", config_mod.DEFAULT_QUOTAS.get(key, 0)))
            for key in config_mod.DEFAULT_QUOTAS
        }
        quotas[service] = quota

        cfg = dict(self.cfg)
        cfg.update(
            {
                "appid": appid,
                "secret": secret,
                "hotkey": combo,
                "target_mode": self.display_to_code.get(self.target_var.get(), "auto"),
                "service": service,
                "domain": self.domain_to_code.get(self.domain_var.get(), baidu_api.DEFAULT_DOMAIN),
                "request_timeout": max(3, min(60, timeout)),
                "popup_seconds": max(0, min(600, seconds)),
                "opacity": max(0.4, min(1.0, opacity)),
                "fallback_clipboard": bool(self.fallback_var.get()),
                "auto_copy": bool(self.auto_copy_var.get()),
                "show_source": bool(self.show_source_var.get()),
                "minimize_to_tray": bool(self.tray_var.get()),
                "start_minimized": bool(self.start_min_var.get()),
                "first_run_done": True,
            }
        )
        for key, value in quotas.items():
            cfg[f"quota_{key}"] = value
        return cfg

    def _save(self):
        cfg = self._collect()
        if cfg is None:
            return
        self._stop_record()
        self.grab_release()
        self.destroy()
        self.on_saved(cfg)

    def _cancel(self):
        self._stop_record()
        self.grab_release()
        self.destroy()


UPDATE_PLAN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "UPDATE_PLAN.md")


class UpdateDialog(tk.Toplevel):
    """检查 / 下载 / 安装 GitHub Releases 上的更新。"""

    def __init__(self, master, app, font: str):
        super().__init__(master)
        self.app = app
        self.font = font
        self.info: updater.UpdateInfo | None = None
        self._busy = False

        self.title("检查更新")
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.url_var = tk.StringVar(value=str(app.cfg.get("update_url", "")))
        self.auto_var = tk.BooleanVar(value=bool(app.cfg.get("update_auto_check", True)))
        self.status_var = tk.StringVar(value="")

        outer = tk.Frame(self, bg=C["bg"], padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="检查更新", bg=C["bg"], fg=C["text"],
                 font=(font, 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(outer, text=f"当前版本：v{APP_VERSION}", bg=C["bg"], fg=C["muted"],
                 font=(font, 9), anchor="w").pack(fill="x", pady=(2, 10))

        card = tk.Frame(outer, bg=C["card"], padx=14, pady=12,
                        highlightthickness=1, highlightbackground=C["border"])
        card.pack(fill="both", expand=True)

        tk.Label(card, text="更新源（GitHub 上 version.json 的地址）", bg=C["card"],
                 fg=C["muted"], font=(font, 9), anchor="w").pack(fill="x")
        entry = self._entry(card, self.url_var)
        entry.pack(fill="x", ipady=5, pady=(3, 8))

        row = tk.Frame(card, bg=C["card"])
        row.pack(fill="x")
        self.check_btn = make_button(row, "检查更新", self._do_check, "primary",
                                     font=(font, 9), padx=14, pady=6)
        self.check_btn.pack(side="left")
        tk.Checkbutton(
            row, text="启动时自动检查（每天最多一次）", variable=self.auto_var,
            bg=C["card"], fg=C["muted"], activebackground=C["card"],
            activeforeground=C["text"], selectcolor=C["card"],
            font=(font, 8), bd=0, highlightthickness=0,
        ).pack(side="left", padx=(12, 0))

        self.progress = ttk.Progressbar(card, mode="determinate", length=430)
        self.progress.pack(fill="x", pady=(10, 0))

        self.status_label = tk.Label(
            card, textvariable=self.status_var, bg=C["card"], fg=C["muted"],
            font=(font, 9), justify="left", anchor="w", wraplength=520,
        )
        self.status_label.pack(fill="x", pady=(4, 8))

        tk.Label(card, text="更新内容", bg=C["card"], fg=C["muted"],
                 font=(font, 9), anchor="w").pack(fill="x")
        self.notes = tk.Text(
            card, height=7, width=64, bg=C["soft"], fg=C["text"], relief="flat",
            bd=0, highlightthickness=1, highlightbackground=C["border"],
            font=(font, 9), wrap="word", padx=8, pady=6, cursor="arrow",
        )
        self.notes.pack(fill="both", expand=True, pady=(3, 0))
        self.notes.insert("1.0", "点「检查更新」后，这里会显示新版本的更新说明。")
        self.notes.configure(state="disabled")

        footer = tk.Frame(outer, bg=C["bg"])
        footer.pack(fill="x", pady=(12, 0))
        self.install_btn = make_button(footer, "下载并安装", self._do_install, "primary",
                                       font=(font, 9), padx=16, pady=7)
        self.install_btn.pack(side="left")
        self.install_btn.config(state="disabled")
        tk.Button(
            footer, text="更新方案说明", command=self._open_plan,
            bg=C["bg"], fg=C["accent"], activebackground=C["bg"],
            activeforeground=C["accent_press"], relief="flat", bd=0,
            highlightthickness=0, cursor="hand2", font=(font, 9, "underline"),
        ).pack(side="left", padx=(12, 0))
        make_button(footer, "关闭", self._on_close, "secondary",
                    font=(font, 9), padx=16, pady=7).pack(side="right")

        self._center()
        self.bind("<Escape>", lambda _e: self._on_close())
        self.after(120, self.check_btn.focus_set)

    # ------------------------------------------------------------------ 布局
    def _entry(self, parent, textvariable):
        return tk.Entry(
            parent, textvariable=textvariable, bg=C["soft"], fg=C["text"],
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=C["border"], highlightcolor=C["accent"],
            insertbackground=C["text"], font=(self.font, 9),
        )

    def _center(self):
        self.update_idletasks()
        width = max(620, self.winfo_reqwidth())
        height = max(520, self.winfo_reqheight())
        left, top, right, bottom = winutil.get_work_area()
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------------ 状态
    def _set_status(self, text: str, color: str = C["muted"]):
        self.status_var.set(text)
        try:
            self.status_label.config(fg=color)
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        try:
            self.check_btn.config(state=state)
            if self.info is None:
                self.install_btn.config(state="disabled")
            else:
                self.install_btn.config(state=state)
        except tk.TclError:
            pass

    def _set_notes(self, text: str):
        try:
            self.notes.configure(state="normal")
            self.notes.delete("1.0", "end")
            self.notes.insert("1.0", text or "（没有提供更新说明）")
            self.notes.configure(state="disabled")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 交互
    def _save_source(self):
        url = self.url_var.get().strip()
        auto = bool(self.auto_var.get())
        self.app.cfg["update_url"] = url
        self.app.cfg["update_auto_check"] = auto
        try:
            config_mod.save(self.app.cfg)
        except OSError:
            pass

    def _do_check(self):
        if self._busy:
            return
        self._save_source()
        url = self.url_var.get().strip()
        self.info = None
        self.install_btn.config(state="disabled")
        self._set_busy(True)
        self._set_status("正在检查更新…")
        self.progress["value"] = 0

        def worker():
            try:
                info = updater.check_for_update(APP_VERSION, url)
            except updater.UpdateError as exc:
                self.after(0, lambda e=exc: self._check_failed(str(e)))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: self._check_failed(f"检查更新失败：{e}"))
            else:
                self.after(0, lambda i=info: self._check_done(i))

        threading.Thread(target=worker, daemon=True).start()

    def _check_failed(self, message: str):
        self._set_busy(False)
        self._set_status(message, C["err"])

    def _check_done(self, info):
        self._set_busy(False)
        if info is None:
            self._set_notes(f"当前已是最新版本 v{APP_VERSION}。")
            self._set_status(f"已是最新版本（v{APP_VERSION}）", C["ok"])
            return
        self.info = info
        self._set_notes(info.notes or "（发布者没有填写更新说明）")
        self._set_status(f"发现新版本 v{info.version}，可以下载安装", C["accent"])
        try:
            self.install_btn.config(state="normal")
        except tk.TclError:
            pass

    def _do_install(self):
        if self._busy or self.info is None:
            return
        if not updater.is_frozen():
            messagebox.showinfo(
                "源码运行模式",
                "源码运行模式不能自动替换程序。\n请用 git pull 拉取新代码，"
                "或重新下载带 exe 的发布包。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "下载更新",
            f"将下载 v{self.info.version} 并校验，下载完成后需要重启程序。\n\n现在开始吗？",
            parent=self,
        ):
            return
        self._save_source()
        self._set_busy(True)
        self._set_status("准备下载…")
        info = self.info

        def progress(done, total, elapsed):
            def ui():
                if total > 0:
                    percent = min(100, int(done * 100 / total))
                    self.progress["value"] = percent
                    self._set_status(
                        f"正在下载… {percent}%  "
                        f"({done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB)"
                    )
                else:
                    self._set_status(f"正在下载… {done / 1024 / 1024:.1f} MB")

            self.after(0, ui)

        def worker():
            try:
                dest_root = updater.updates_root() / info.version
                zip_path = updater.download_update(info, dest_root, progress=progress)
                extracted = updater.extract_update(zip_path, dest_root / "stage")
                payload = updater.find_payload_dir(extracted, updater.exe_name_of())
                script = updater.write_update_script(
                    payload, updater.app_dir_of(), updater.exe_name_of(),
                    os.getpid(), dest_root,
                )
            except updater.UpdateError as exc:
                self.after(0, lambda e=exc: self._install_failed(str(e)))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: self._install_failed(f"更新失败：{e}"))
            else:
                self.after(0, lambda s=script: self._install_ready(s))

        threading.Thread(target=worker, daemon=True).start()

    def _install_failed(self, message: str):
        self._set_busy(False)
        self.progress["value"] = 0
        self._set_status(message, C["err"])
        messagebox.showerror("更新失败", message, parent=self)

    def _install_ready(self, script):
        self._set_busy(False)
        self.progress["value"] = 100
        self._set_status("下载完成并通过 SHA-256 校验，可以重启安装", C["ok"])
        if messagebox.askyesno(
            "更新已就绪",
            "新版本已下载并校验通过。\n\n现在关闭程序、替换文件并重新启动吗？",
            parent=self,
        ):
            try:
                updater.launch_update_script(script, os.getpid())
            except updater.UpdateError as exc:
                messagebox.showerror("无法启动更新", str(exc), parent=self)
                return
            self.app.quit()
        else:
            self._set_status("已下载，可稍后重新点「下载并安装」", C["muted"])

    def _open_plan(self):
        if os.path.isfile(UPDATE_PLAN_FILE):
            try:
                os.startfile(UPDATE_PLAN_FILE)  # noqa: S606 - 打开本地说明文件
                return
            except OSError:
                pass
        messagebox.showinfo(
            "更新方案说明",
            "更新方案见程序目录下的 docs/UPDATE_PLAN.md：\n"
            "在 GitHub 建仓 → 打 tag 触发 Actions 发布 → "
            "把 version.json 地址填到这里即可。",
            parent=self,
        )

    def _on_close(self):
        self._save_source()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# --------------------------------------------------------------------------- 主程序
class App:
    """主窗口 + 全局快捷键 + 翻译流程 + 系统托盘常驻。"""

    def __init__(self):
        self.cfg = config_mod.load()
        self.ui_queue: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.font = pick_font(self.root)
        self.icon = set_window_icon(self.root)
        self.root.title(config_mod.APP_TITLE)
        self.root.configure(bg=C["bg"])
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self._style_ttk()
        self._center(440, 474)

        self._hotkey: hotkey_mod.GlobalHotkey | None = None
        self._busy = False
        self._popup: ResultPopup | None = None
        self._busy_popup: BusyPopup | None = None
        self._settings_open = False
        self._last_cursor = (0, 0)

        # 托盘
        self._tray: tray_mod.TrayIcon | None = None
        self._tray_ok = False
        self._tray_hint_shown = False
        self._tray_menu: tk.Menu | None = None

        # 自动更新
        self._update_dialog: UpdateDialog | None = None
        self._pending_update: updater.UpdateInfo | None = None
        self._update_check_running = False

        self._build_ui()
        self._build_tray_menu()
        self._refresh_status()
        self._start_hotkey()
        self._init_tray()
        self._refresh_status()
        self._poll_queue()

        if updater.is_frozen():
            try:
                updater.cleanup_leftovers(updater.app_dir_of())
            except Exception:
                pass
        self.root.after(4000, self._auto_check_update)

        if self.cfg.get("start_minimized") and self._tray_ok:
            self.root.withdraw()
        if not self._has_credentials():
            self.root.after(350, lambda: self._open_settings(first_run=True))

    # ------------------------------------------------------------------ 界面
    def _style_ttk(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=C["soft"],
            background=C["soft"],
            foreground=C["text"],
            bordercolor=C["border"],
            lightcolor=C["border"],
            darkcolor=C["border"],
            arrowcolor=C["muted"],
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", C["soft"])],
            foreground=[("readonly", C["text"])],
        )
        self.root.option_add("*TCombobox*Listbox.font", (self.font, 9))

    def _center(self, width: int, height: int):
        self.root.update_idletasks()
        left, top, right, bottom = winutil.get_work_area()
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=C["bg"], padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        # ---- 标题
        head = tk.Frame(outer, bg=C["bg"])
        head.pack(fill="x")
        badge = tk.Label(
            head,
            text="译",
            bg=C["accent"],
            fg="#ffffff",
            font=(self.font, 13, "bold"),
            width=2,
            pady=1,
        )
        badge.pack(side="left")
        titles = tk.Frame(head, bg=C["bg"])
        titles.pack(side="left", padx=(10, 0), fill="x", expand=True)
        tk.Label(titles, text="划词翻译", bg=C["bg"], fg=C["text"],
                 font=(self.font, 15, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            titles,
            text="选中任意文字，按快捷键立即用百度翻译",
            bg=C["bg"],
            fg=C["muted"],
            font=(self.font, 9),
            anchor="w",
        ).pack(fill="x")

        # ---- 状态卡片
        card = tk.Frame(
            outer,
            bg=C["card"],
            padx=14,
            pady=12,
            highlightthickness=1,
            highlightbackground=C["border"],
        )
        card.pack(fill="x", pady=(14, 12))
        self.api_dot = tk.Label(card, text="●", bg=C["card"], fg=C["warn"], font=(self.font, 9))
        self.api_dot.grid(row=0, column=0, sticky="w")
        self.api_label = tk.Label(card, text="", bg=C["card"], fg=C["text"],
                                  font=(self.font, 10), anchor="w")
        self.api_label.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.hotkey_label = tk.Label(card, text="", bg=C["card"], fg=C["muted"],
                                     font=(self.font, 9), anchor="w")
        self.hotkey_label.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(5, 0))
        self.target_label = tk.Label(card, text="", bg=C["card"], fg=C["muted"],
                                     font=(self.font, 9), anchor="w")
        self.target_label.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=(2, 0))
        self.tray_label = tk.Label(card, text="", bg=C["card"], fg=C["muted"],
                                   font=(self.font, 9), anchor="w")
        self.tray_label.grid(row=3, column=1, sticky="w", padx=(6, 0), pady=(2, 0))
        self.service_label = tk.Label(card, text="", bg=C["card"], fg=C["muted"],
                                      font=(self.font, 9), anchor="w")
        self.service_label.grid(row=4, column=1, sticky="w", padx=(6, 0), pady=(2, 0))
        self.usage_label = tk.Label(card, text="", bg=C["card"], fg=C["muted"],
                                    font=(self.font, 9), anchor="w")
        self.usage_label.grid(row=5, column=1, sticky="w", padx=(6, 0), pady=(2, 0))
        card.columnconfigure(1, weight=1)

        self.hint_label = tk.Label(
            outer,
            text="",
            bg=C["bg"],
            fg=C["muted"],
            font=(self.font, 8),
            justify="left",
            anchor="w",
            wraplength=400,
        )
        self.hint_label.pack(fill="x", pady=(0, 10))

        # ---- 操作按钮
        row1 = tk.Frame(outer, bg=C["bg"])
        row1.pack(fill="x")
        primary = make_button(
            row1, "翻译选中文字", self._translate_selection_clicked, "primary",
            font=(self.font, 10, "bold"), padx=10, pady=9,
        )
        primary.pack(side="left", fill="x", expand=True)
        clipboard_btn = make_button(
            row1, "翻译剪贴板", self._translate_clipboard, "secondary",
            font=(self.font, 10), padx=10, pady=9,
        )
        clipboard_btn.pack(side="left", padx=(10, 0))

        row2 = tk.Frame(outer, bg=C["bg"])
        row2.pack(fill="x", pady=(10, 0))
        settings_btn = make_button(
            row2, "设置", self._open_settings, "secondary", font=(self.font, 9), padx=16, pady=6
        )
        settings_btn.pack(side="left")
        about_btn = make_button(
            row2, "使用说明", self._show_help, "ghost", font=(self.font, 9), padx=14, pady=6
        )
        about_btn.pack(side="left", padx=(8, 0))
        self.update_btn = make_button(
            row2, "检查更新", self._open_update_dialog, "ghost", font=(self.font, 9), padx=10, pady=6
        )
        self.update_btn.pack(side="left", padx=(8, 0))
        quit_btn = make_button(
            row2, "退出", self.quit, "ghost", font=(self.font, 9), padx=16, pady=6
        )
        quit_btn.pack(side="right")

    def _refresh_status(self):
        if self._has_credentials():
            self.api_dot.config(fg=C["ok"])
            self.api_label.config(text="百度翻译 · 已配置", fg=C["text"])
            if self._tray_available():
                self.hint_label.config(text="提示：关窗不退出，程序常驻托盘；快捷键随时可用。")
            else:
                self.hint_label.config(text="提示：译文会自动复制到剪贴板，可直接粘贴。")
        else:
            self.api_dot.config(fg=C["warn"])
            self.api_label.config(text="未配置 APPID / 密钥", fg=C["warn"])
            self.hint_label.config(
                text="第一步：点击「设置」填写百度翻译开放平台的 APPID 和密钥（免费申请），填好后即可使用。"
            )
        combo = self.cfg.get("hotkey", "ctrl+alt+t")
        try:
            _mods, _vk, display = hotkey_mod.parse_hotkey(combo)
            self.hotkey_label.config(text=f"快捷键：{display}")
            self.target_label.config(
                text=f"目标语言：{self._target_display()}"
            )
        except ValueError as exc:
            self.hotkey_label.config(text=f"快捷键无效：{exc}")
        if self._tray_available():
            self.tray_label.config(text="托盘常驻：运行中（关闭窗口不退出）", fg=C["ok"])
        elif self._tray is not None:
            self.tray_label.config(
                text="托盘不可用（自动重试中，关窗=最小化）", fg=C["warn"]
            )
        else:
            self.tray_label.config(text="托盘常驻：未启用", fg=C["muted"])
        self.root.title(config_mod.APP_TITLE)
        service = baidu_api.normalize_service(self.cfg.get("service", baidu_api.DEFAULT_SERVICE))
        service_text = f"翻译服务：{baidu_api.SERVICE_NAME.get(service, service)}"
        if service == "domain":
            domain = baidu_api.normalize_domain(self.cfg.get("domain", baidu_api.DEFAULT_DOMAIN))
            service_text += f" · {baidu_api.DOMAIN_NAME.get(domain, domain)}"
        self.service_label.config(text=service_text, fg=C["muted"])
        self._refresh_usage_label(service)

    def _refresh_usage_label(self, service: str):
        """本月本机用量 + 按配置额度估算的剩余字符。"""
        used, requests_count = usage.month_usage(service)
        quota = int(self.cfg.get(f"quota_{service}", config_mod.DEFAULT_QUOTAS.get(service, 0)))
        if quota > 0:
            remaining = max(0, quota - used)
            text = (
                f"本月已用：{usage.format_number(used)} 字符"
                f" · 约剩 {usage.format_number(remaining)}"
            )
            color = C["warn"] if remaining <= quota * 0.1 else C["muted"]
        else:
            text = f"本月已用：{usage.format_number(used)} 字符（{requests_count} 次请求）"
            color = C["muted"]
        self.usage_label.config(text=text, fg=color)

    def _target_display(self) -> str:
        mode = self.cfg.get("target_mode", "auto")
        if mode == "auto":
            return "自动（中 ↔ 英）"
        return baidu_api.LANG_NAME.get(mode, mode)

    # ------------------------------------------------------------------ 快捷键
    def _start_hotkey(self):
        self._stop_hotkey()
        combo = self.cfg.get("hotkey") or "ctrl+alt+t"
        self._hotkey = hotkey_mod.GlobalHotkey(
            combo,
            lambda: self.ui_queue.put(self._begin_selection_translate),
            on_error=lambda exc: self.ui_queue.put(lambda e=exc: self._on_hotkey_error(e)),
        )
        self._hotkey.start()

    def _stop_hotkey(self):
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None

    def _on_hotkey_error(self, exc: Exception):
        Toast(self.root, f"快捷键用不了：{exc}", kind="err", ms=5000)
        self.hotkey_label.config(text=f"快捷键未生效（{self.cfg.get('hotkey')}），请到设置里换一个")

    # ------------------------------------------------------------------ 托盘
    def _init_tray(self):
        """创建常驻托盘图标；失败时程序仍可正常使用（只是没有托盘）。"""
        icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico"
        )
        self._tray = tray_mod.TrayIcon(
            tooltip=self._tray_tooltip(),
            icon_path=icon_path,
            on_left_click=lambda: self.ui_queue.put(self.show_main_window),
            on_right_click=lambda pos: self.ui_queue.put(
                lambda p=pos: self._popup_tray_menu(p)
            ),
            on_error=lambda exc: self.ui_queue.put(lambda e=exc: self._on_tray_error(e)),
            on_ready=lambda: self.ui_queue.put(self._on_tray_ready),
        )
        self._tray_ok = bool(self._tray.start(wait=2.0))

    def _tray_tooltip(self) -> str:
        try:
            _mods, _vk, display = hotkey_mod.parse_hotkey(self.cfg.get("hotkey", "ctrl+alt+t"))
        except ValueError:
            display = self.cfg.get("hotkey", "ctrl+alt+t")
        return f"划词翻译 · 百度翻译\n快捷键：{display}"

    def _tray_available(self) -> bool:
        return bool(self._tray is not None and self._tray.available)

    def _on_tray_error(self, exc: Exception):
        self._tray_ok = False
        self._refresh_status()
        Toast(
            self.root,
            "托盘图标暂时创建不了（系统托盘不可用）。\n"
            "程序会在后台自动重试；期间关闭窗口会最小化到任务栏，快捷键照常可用。\n"
            "如果是用受限沙箱打开的程序，请到「文件资源管理器」里双击 exe 运行。",
            kind="err", ms=7000,
        )

    def _on_tray_ready(self):
        """托盘图标（重新）添加成功。"""
        was_ok = self._tray_ok
        self._tray_ok = True
        self._refresh_status()
        if not was_ok:
            Toast(self.root, "托盘图标已恢复：关窗口会收进托盘", kind="ok", ms=2500)

    def _build_tray_menu(self):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="显示主窗口", command=self.show_main_window)
        menu.add_separator()
        menu.add_command(label="翻译选中文字", command=self._begin_selection_translate)
        menu.add_command(label="翻译剪贴板", command=self._translate_clipboard)
        menu.add_separator()
        menu.add_command(label="设置…", command=self._open_settings)
        menu.add_command(label="使用说明", command=self._show_help)
        menu.add_separator()
        menu.add_command(label="退出", command=self.quit)
        self._tray_menu = menu

    def _popup_tray_menu(self, position: tuple[int, int]):
        if self._tray_menu is None:
            return
        try:
            self._tray_menu.tk_popup(int(position[0]), int(position[1]))
        except tk.TclError:
            pass
        finally:
            try:
                self._tray_menu.grab_release()
            except tk.TclError:
                pass

    def show_main_window(self):
        """从托盘唤回主窗口。"""
        try:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.after(180, lambda: self._drop_topmost())
            self.root.focus_force()
        except tk.TclError:
            pass

    def _drop_topmost(self):
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def hide_to_tray(self) -> bool:
        """把窗口收进托盘；托盘不可用时返回 False。"""
        if not self._tray_available():
            return False
        try:
            self.root.withdraw()
        except tk.TclError:
            return False
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            try:
                _mods, _vk, display = hotkey_mod.parse_hotkey(self.cfg.get("hotkey", "ctrl+alt+t"))
            except ValueError:
                display = self.cfg.get("hotkey", "ctrl+alt+t")
            if self._tray is not None:
                self._tray.notify(
                    "划词翻译",
                    f"程序已常驻托盘，按 {display} 仍可随时划词翻译；右键托盘图标可打开菜单。",
                )
        return True

    def _on_close_window(self):
        """点窗口右上角关闭：优先收进托盘；托盘不可用时最小化到任务栏。"""
        if self.cfg.get("minimize_to_tray", True):
            if self.hide_to_tray():
                return
            try:
                self.root.iconify()      # 没有托盘也保持常驻（在任务栏里）
                return
            except tk.TclError:
                pass
        self.quit()

    # ------------------------------------------------------------------ 翻译流程
    def _has_credentials(self) -> bool:
        return bool(self.cfg.get("appid") and self.cfg.get("secret"))

    def _ensure_credentials(self) -> bool:
        if self._has_credentials():
            return True
        Toast(self.root, "还没有配置 APPID / 密钥，先填一下才能调用百度翻译", kind="err", ms=3200)
        self._open_settings(first_run=not self.cfg.get("first_run_done", False))
        return False

    def _make_translator(self) -> baidu_api.BaiduTranslator:
        return baidu_api.BaiduTranslator(
            self.cfg.get("appid", ""), self.cfg.get("secret", ""),
            self.cfg.get("request_timeout", 10),
            service=self.cfg.get("service", baidu_api.DEFAULT_SERVICE),
            domain=self.cfg.get("domain", baidu_api.DEFAULT_DOMAIN),
        )

    def _translate_selection_clicked(self):
        self._begin_selection_translate()

    def _begin_selection_translate(self):
        if not self._ensure_credentials():
            return
        if self._busy:
            return
        self._close_popup()
        self._busy = True
        self._last_cursor = winutil.get_cursor_pos()
        threading.Thread(target=self._worker_selection, daemon=True).start()

    def _worker_selection(self):
        try:
            text, _previous = selection.copy_selection()
            source_note = "选中文字"
            if not (text or "").strip() and self.cfg.get("fallback_clipboard", True):
                text = selection.get_clipboard_text()
                source_note = "剪贴板"
            text = (text or "").strip()
            if not text:
                self.ui_queue.put(
                    lambda: self._finish_error("没有拿到要翻译的文字。请先用鼠标选中一段文字再按快捷键。")
                )
                return
            self.ui_queue.put(self._show_busy)
            translation = self._translate(text)
            self.ui_queue.put(lambda t=translation: self._show_result(t))
        except baidu_api.BaiduTranslateError as exc:
            self.ui_queue.put(lambda e=exc: self._finish_error(str(e)))
        except Exception as exc:  # 兜底
            self.ui_queue.put(lambda e=exc: self._finish_error(f"翻译失败：{e}"))
        finally:
            self._busy = False

    def _translate_clipboard(self):
        if not self._ensure_credentials():
            return
        text = selection.get_clipboard_text().strip()
        if not text:
            Toast(self.root, "剪贴板里没有文字", kind="err", ms=2200)
            return
        if self._busy:
            return
        self._close_popup()
        self._busy = True
        self._last_cursor = winutil.get_cursor_pos()
        threading.Thread(target=self._worker_clipboard, args=(text,), daemon=True).start()

    def _worker_clipboard(self, text: str):
        try:
            self.ui_queue.put(self._show_busy)
            translation = self._translate(text)
            self.ui_queue.put(lambda t=translation: self._show_result(t))
        except baidu_api.BaiduTranslateError as exc:
            self.ui_queue.put(lambda e=exc: self._finish_error(str(e)))
        except Exception as exc:
            self.ui_queue.put(lambda e=exc: self._finish_error(f"翻译失败：{e}"))
        finally:
            self._busy = False

    def _translate(self, text: str) -> baidu_api.Translation:
        from_lang, to_lang = baidu_api.detect_direction(text, self.cfg.get("target_mode", "auto"))
        translator = self._make_translator()
        return translator.translate(text, from_lang, to_lang)

    def _show_busy(self):
        self._close_busy()
        try:
            self._busy_popup = BusyPopup(self.root, self._last_cursor or winutil.get_cursor_pos(), self.font)
        except tk.TclError:
            self._busy_popup = None

    def _close_busy(self):
        if self._busy_popup is not None:
            self._busy_popup.close()
            self._busy_popup = None

    def _show_result(self, translation: baidu_api.Translation):
        self._close_busy()
        self._close_popup()
        # 记录本机用量（成功才计数），并刷新主窗口的额度显示
        try:
            usage.record(translation.service, len(translation.source))
        except Exception:
            pass
        try:
            self._refresh_status()
        except tk.TclError:
            pass
        try:
            self._popup = ResultPopup(self.root, translation, self.cfg, self.font)
        except tk.TclError:
            self._popup = None

    def _finish_error(self, message: str):
        self._close_busy()
        Toast(self.root, message, kind="err", ms=4200)

    def _close_popup(self):
        if self._popup is not None:
            try:
                self._popup.close()
            except tk.TclError:
                pass
            self._popup = None

    # ------------------------------------------------------------------ 设置 / 帮助
    def _open_settings(self, first_run: bool = False):
        if self._settings_open:
            return
        # 设置窗口是主窗口的 transient，主窗口收进托盘时要先唤回来，否则弹不出来
        if self._is_hidden():
            self.show_main_window()
        self._settings_open = True
        try:
            dialog = SettingsDialog(
                self.root,
                self.cfg,
                self.font,
                on_saved=self._on_settings_saved,
                first_run=first_run,
            )
        except Exception:
            self._settings_open = False
            raise

        def _on_destroy(_event):
            self._settings_open = False

        dialog.bind("<Destroy>", _on_destroy)
        self._settings_dialog = dialog

    def _is_hidden(self) -> bool:
        try:
            return self.root.state() == "withdrawn"
        except tk.TclError:
            return False

    def _on_settings_saved(self, new_cfg: dict):
        old_hotkey = self.cfg.get("hotkey")
        self.cfg = config_mod.normalize(new_cfg)
        try:
            config_mod.save(self.cfg)
        except OSError as exc:
            Toast(self.root, f"配置保存失败：{exc}", kind="err", ms=4000)
        self._refresh_status()
        if self.cfg.get("hotkey") != old_hotkey:
            self._start_hotkey()
        if self._tray is not None:
            self._tray.set_tooltip(self._tray_tooltip())
        if self._has_credentials():
            Toast(self.root, "设置已保存", kind="ok", ms=1800)

    def _show_help(self):
        if self._is_hidden():
            self.show_main_window()
        lines = [
            "划词翻译 · 百度翻译",
            "",
            "1. 在设置里填写百度翻译开放平台的 APPID 与密钥；",
            f"2. 在任意程序里用鼠标选中文字，按 {self.cfg.get('hotkey', 'ctrl+alt+t')}；",
            "3. 弹窗显示译文，并自动复制到剪贴板；",
            "4. 也可以点「翻译剪贴板」，直接翻译剪贴板里的内容。",
            "",
            f"申请密钥：{SETUP_URL}",
            f"接口文档：{DOC_URL}",
            "",
            "配置文件位置：",
            str(config_mod.config_path()),
        ]
        popup = tk.Toplevel(self.root)
        popup.title("使用说明")
        popup.configure(bg=C["bg"])
        popup.resizable(False, False)
        popup.transient(self.root)
        text = tk.Text(popup, width=56, height=15, bg=C["card"], fg=C["text"],
                       relief="flat", bd=0, highlightthickness=1,
                       highlightbackground=C["border"], font=(self.font, 9),
                       wrap="word", padx=14, pady=12, cursor="arrow")
        text.pack(padx=14, pady=14)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        link = tk.Button(popup, text="打开申请页面", command=lambda: webbrowser.open(SETUP_URL),
                         bg=C["accent"], fg="#ffffff", activebackground=C["accent_press"],
                         activeforeground="#ffffff", relief="flat", bd=0, highlightthickness=0,
                         cursor="hand2", font=(self.font, 9), padx=16, pady=6)
        link.pack(pady=(0, 14))
        popup.update_idletasks()
        popup.geometry(f"+{self.root.winfo_x() + 40}+{self.root.winfo_y() + 60}")
        popup.focus_force()

    # ------------------------------------------------------------------ 自动更新
    def _open_update_dialog(self):
        if self._update_dialog is not None:
            try:
                if self._update_dialog.winfo_exists():
                    self._update_dialog.lift()
                    self._update_dialog.focus_force()
                    return
            except tk.TclError:
                pass
            self._update_dialog = None
        if self._is_hidden():
            self.show_main_window()
        try:
            dialog = UpdateDialog(self.root, self, self.font)
        except Exception as exc:  # noqa: BLE001
            Toast(self.root, f"打开更新窗口失败：{exc}", kind="err", ms=4000)
            return
        self._update_dialog = dialog

        def _on_destroy(_event):
            self._update_dialog = None

        dialog.bind("<Destroy>", _on_destroy)

    def _auto_check_update(self):
        """启动后静默检查一次（每天最多一次，失败不打扰用户）。"""
        if not self.cfg.get("update_auto_check", True):
            return
        if not updater.is_frozen():
            return  # 源码模式不自动检查
        if self._update_check_running:
            return
        url = str(self.cfg.get("update_url", "")).strip()
        if not url or "YOUR_GITHUB" in url:
            return
        now = time.time()
        try:
            last = float(self.cfg.get("update_last_check", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if now - last < 24 * 3600:
            return
        self._update_check_running = True

        def worker():
            try:
                info = updater.check_for_update(APP_VERSION, url)
            except Exception:
                info = None
            finally:
                self._update_check_running = False
            self.cfg["update_last_check"] = time.time()
            try:
                config_mod.save(self.cfg)
            except OSError:
                pass
            if info is not None:
                self.ui_queue.put(lambda i=info: self._on_update_available(i))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_available(self, info: updater.UpdateInfo):
        self._pending_update = info
        try:
            self.update_btn.config(text=f"发现新版本 v{info.version}", fg=C["accent"])
        except tk.TclError:
            pass
        Toast(self.root, f"发现新版本 v{info.version}，点「发现新版本」查看", kind="ok", ms=5000)

    # ------------------------------------------------------------------ 事件循环
    def _poll_queue(self):
        try:
            while True:
                callback = self.ui_queue.get_nowait()
                try:
                    callback()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(30, self._poll_queue)

    def quit(self):
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
            self._tray = None
        self._stop_hotkey()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()
