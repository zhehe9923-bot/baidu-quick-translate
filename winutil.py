"""一些 Win32 小工具：DPI、鼠标位置、屏幕工作区、单实例。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
except OSError:  # pragma: no cover
    shell32 = None

SPI_GETWORKAREA = 0x0030
MONITOR_DEFAULTTONEAREST = 0x00000002
ERROR_ALREADY_EXISTS = 183

_mutex_handle = None


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.SetProcessDPIAware.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE


def enable_dpi_awareness() -> None:
    """让窗口在高分屏上清晰（必须在创建 Tk 之前调用）。"""
    try:
        # Windows 10 1703+: Per-Monitor V2
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [wintypes.HANDLE]
        setter.restype = wintypes.BOOL
        if setter(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_int
        if shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except OSError:
        pass


def get_cursor_pos() -> tuple[int, int]:
    point = POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        return int(point.x), int(point.y)
    return 0, 0


def get_work_area(x: int | None = None, y: int | None = None) -> tuple[int, int, int, int]:
    """返回鼠标所在显示器的可用区域 (left, top, right, bottom)。"""
    if x is None or y is None:
        x, y = get_cursor_pos()
    monitor = user32.MonitorFromPoint(POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    if monitor:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    rect = RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    return 0, 0, 1920, 1080


def set_app_user_model_id(app_id: str) -> None:
    """让任务栏图标与窗口正确归组。"""
    if shell32 is None:
        return
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except (AttributeError, OSError):
        pass


def single_instance(name: str) -> bool:
    """命名互斥量实现单实例：已有实例在运行则返回 False。"""
    global _mutex_handle
    if _mutex_handle:
        return True
    handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    last_error = ctypes.get_last_error()
    if not handle:
        return True  # 创建失败就不拦着用户启动
    _mutex_handle = handle
    if last_error == ERROR_ALREADY_EXISTS:
        return False
    return True
