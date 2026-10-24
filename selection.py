"""剪贴板 + 「复制选中文字」相关操作（ctypes 直接调用 Win32，无第三方依赖）。

核心思路：记住剪贴板原内容 -> 模拟按下 Ctrl+C -> 等剪贴板变化 -> 读出文本 -> 还原剪贴板。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL


def _open_clipboard(retries: int = 12, delay: float = 0.01) -> bool:
    """剪贴板可能被别的程序短暂占用，失败时重试几次。"""
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def get_clipboard_text() -> str:
    """读取剪贴板中的纯文本，没有则返回空字符串。"""
    if not _open_clipboard():
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """把文本写入剪贴板，成功返回 True。"""
    data = (text or "").encode("utf-16-le") + b"\x00\x00"
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        # 所有权已交给系统，不能自己 GlobalFree
        return True
    finally:
        user32.CloseClipboard()


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber())


def _key_down(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)


def _key_up(vk: int) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def release_modifiers(vks: tuple[int, ...] = (VK_CONTROL, VK_MENU, VK_SHIFT)) -> None:
    """热键触发时修饰键通常还按着，先抬起它们，避免 Ctrl+C 变成 Ctrl+Alt+C。"""
    for vk in vks:
        _key_up(vk)


def send_ctrl_c() -> None:
    _key_down(VK_CONTROL)
    _key_down(VK_C)
    time.sleep(0.01)
    _key_up(VK_C)
    _key_up(VK_CONTROL)


def copy_selection(
    timeout: float = 0.9,
    poll: float = 0.02,
    restore_clipboard: bool = True,
    modifiers: tuple[int, ...] = (VK_CONTROL, VK_MENU, VK_SHIFT),
) -> tuple[str, str]:
    """模拟 Ctrl+C 抓取当前选中文字。

    返回 (选中的文本, 剪贴板原文本)。没抓到选中内容时文本为空字符串。
    默认会把剪贴板恢复成原来的内容，避免打扰用户。
    """
    previous = get_clipboard_text()
    sequence = clipboard_sequence()

    release_modifiers(modifiers)
    time.sleep(0.03)
    send_ctrl_c()

    captured = ""
    deadline = time.monotonic() + max(0.2, timeout)
    while time.monotonic() < deadline:
        time.sleep(poll)
        if clipboard_sequence() != sequence:
            captured = get_clipboard_text()
            if captured:
                break
    # 再给某些慢程序一点时间
    if not captured:
        time.sleep(0.12)
        if clipboard_sequence() != sequence:
            captured = get_clipboard_text()

    if restore_clipboard and previous:
        set_clipboard_text(previous)
    return captured, previous
