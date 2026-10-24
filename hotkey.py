"""Windows 全局快捷键（RegisterHotKey），只用标准库 ctypes。

用法：
    manager = GlobalHotkey("ctrl+alt+t", on_press, on_error=on_error)
    manager.start()      # 后台线程注册并监听
    manager.stop()       # 退出
"""

from __future__ import annotations

import ctypes
import re
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_HOTKEY_ID = 0xB001

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}

_MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
    "super": MOD_WIN,
}

_NAMED_VK = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "minus": 0xBD,
    "-": 0xBD,
    "equal": 0xBB,
    "=": 0xBB,
    "comma": 0xBC,
    ",": 0xBC,
    "period": 0xBE,
    ".": 0xBE,
    "slash": 0xBF,
    "/": 0xBF,
    "backslash": 0xDC,
    "\\": 0xDC,
    "semicolon": 0xBA,
    ";": 0xBA,
    "quote": 0xDE,
    "'": 0xDE,
    "backquote": 0xC0,
    "`": 0xC0,
    "bracketleft": 0xDB,
    "[": 0xDB,
    "bracketright": 0xDD,
    "]": 0xDD,
}

_DISPLAY_KEY = {
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "esc": "Esc",
    "escape": "Esc",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "ins": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "minus": "-",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "semicolon": ";",
    "quote": "'",
    "backquote": "`",
    "bracketleft": "[",
    "bracketright": "]",
}

_DISPLAY_MOD = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}


def _key_vk(key: str) -> int | None:
    """把按键名转换成虚拟键码，失败返回 None。"""
    key = key.strip().lower()
    if key in _NAMED_VK:
        return _NAMED_VK[key]
    match = re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key)
    if match:
        return 0x70 + int(match.group(1)) - 1
    match = re.fullmatch(r"num([0-9])", key)
    if match:
        return 0x60 + int(match.group(1))
    if len(key) == 1 and (key.isascii() and (key.isalnum() or key in "`-=[]\\;',./")):
        return ord(key.upper())
    return None


def parse_hotkey(combo: str) -> tuple[int, int, str]:
    """解析快捷键字符串，返回 (修饰键掩码, 虚拟键码, 展示文本)。"""
    if not combo or not combo.strip():
        raise ValueError("快捷键不能为空")
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("快捷键格式不正确，例如：ctrl+alt+t")
    mods = 0
    keys: list[str] = []
    for part in parts:
        if part in _MODIFIER_NAMES:
            mods |= _MODIFIER_NAMES[part]
        else:
            keys.append(part)
    if len(keys) != 1:
        raise ValueError("快捷键需要且只能有一个主按键，例如：ctrl+alt+t")
    if mods == 0:
        raise ValueError("快捷键至少要包含一个 Ctrl / Alt / Shift / Win 修饰键")
    vk = _key_vk(keys[0])
    if vk is None:
        raise ValueError(f"不认识的按键：{keys[0]}")

    display_parts: list[str] = []
    for name in ("ctrl", "alt", "shift", "win"):
        if mods & _MODIFIER_NAMES[name]:
            display_parts.append(_DISPLAY_MOD[name])
    key = keys[0]
    display_parts.append(_DISPLAY_KEY.get(key) or (key.upper() if len(key) <= 2 else key.title()))
    return mods, vk, " + ".join(display_parts)


class GlobalHotkey:
    """在后台线程注册并监听全局快捷键。"""

    def __init__(self, combo: str, callback, on_error=None):
        self.combo = combo or ""
        self.callback = callback
        self.on_error = on_error
        self.thread_id: int | None = None
        self.ready = threading.Event()
        self.error: Exception | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> "GlobalHotkey":
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self
            self.error = None
            self.ready.clear()
            self._thread = threading.Thread(target=self._run, name="global-hotkey", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        with self._lock:
            thread_id = self.thread_id
            self.thread_id = None
        if thread_id:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self.error is None)

    # ---------------------------------------------------------------- 内部实现
    def _fail(self, exc: Exception) -> None:
        self.error = exc
        self.ready.set()
        if self.on_error:
            try:
                self.on_error(exc)
            except Exception:  # 回调里的异常不能让监听线程崩掉
                pass

    def _run(self) -> None:
        try:
            mods, vk, _display = parse_hotkey(self.combo)
        except ValueError as exc:
            self._fail(exc)
            return

        self.thread_id = int(kernel32.GetCurrentThreadId())
        if not user32.RegisterHotKey(None, _HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            code = ctypes.get_last_error()
            self.thread_id = None
            self._fail(RuntimeError(
                f"快捷键 {self.combo} 注册失败（系统错误码 {code}），可能已被其它程序占用，"
                "请换一个组合键"
            ))
            return

        self.ready.set()
        message = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):      # 0 = WM_QUIT，-1 = 出错
                    break
                if message.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)
            self.thread_id = None
