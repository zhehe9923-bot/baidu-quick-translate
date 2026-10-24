"""系统托盘图标（Windows Shell_NotifyIcon，纯标准库 ctypes 实现）。

在独立线程里创建一个隐藏窗口并跑消息循环，托盘鼠标事件、任务栏重建、
气泡通知都在这个线程处理；对 tkinter 的回调通过上层传入的 lambda 投递
到主线程队列，绝不在这个线程里碰 tk 对象。
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_SET_TOOLTIP = WM_APP + 2
WM_BALLOON = WM_APP + 3
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B

# 添加托盘图标失败时的重试策略：每 2 秒重试一次，最多 30 次（约 1 分钟）
RETRY_TIMER_ID = 0x0A17
RETRY_INTERVAL_MS = 2000
RETRY_MAX_ATTEMPTS = 30

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
NIIF_NOSOUND = 0x00000010

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
IDI_APPLICATION = 32512
ERROR_CLASS_ALREADY_EXISTS = 1410

LRESULT = ctypes.c_ssize_t

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.LPVOID]
user32.SetTimer.restype = wintypes.UINT
user32.KillTimer.argtypes = [wintypes.HWND, wintypes.UINT]
user32.KillTimer.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wintypes.HICON
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class TrayIcon:
    """托盘图标：start() 后常驻，stop() 移除。"""

    _instance_counter = 0

    def __init__(
        self,
        tooltip: str = "划词翻译",
        icon_path: str | None = None,
        on_left_click=None,
        on_right_click=None,
        on_error=None,
        on_ready=None,
    ):
        self.tooltip = (tooltip or "划词翻译")[:127]
        self.icon_path = icon_path or ""
        self.on_left_click = on_left_click
        self.on_right_click = on_right_click
        self.on_error = on_error
        self.on_ready = on_ready

        self.hwnd: int | None = None
        self.hicon: int | None = None
        self.error: Exception | None = None
        self.available = False

        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._wndproc = None
        self._class_name = ""
        self._taskbar_created = 0
        self._pending_tooltip: str | None = None
        self._pending_balloon: tuple[str, str] | None = None
        self._retry_count = 0
        self._error_reported = False

    # ---------------------------------------------------------------- 对外接口
    def start(self, wait: float = 3.0) -> bool:
        """启动托盘线程；阻塞最多 wait 秒以确认图标是否添加成功。"""
        if self._thread and self._thread.is_alive():
            return self.available
        self.error = None
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)
        self._thread.start()
        self._ready.wait(wait)
        return self.available

    def stop(self) -> None:
        hwnd = self.hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(2.0)
        self._thread = None
        self.hwnd = None
        self.available = False

    def set_tooltip(self, text: str) -> None:
        self.tooltip = (text or "划词翻译")[:127]
        self._pending_tooltip = self.tooltip
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_SET_TOOLTIP, 0, 0)

    def notify(self, title: str, text: str) -> None:
        """弹出气球/系统通知（任务栏图标还在时有效）。"""
        self._pending_balloon = ((title or "划词翻译")[:63], (text or "")[:255])
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_BALLOON, 0, 0)

    # ---------------------------------------------------------------- 内部实现
    def _fail(self, exc: Exception) -> None:
        self.error = exc
        self.available = False
        logger.warning("托盘图标创建失败: %s", exc)
        self._ready.set()
        if self.on_error:
            try:
                self.on_error(exc)
            except Exception:
                pass

    def _make_nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        return nid

    def _add_icon(self) -> bool:
        nid = self._make_nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip
        try:
            ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        except OSError as exc:  # pragma: no cover
            logger.warning("Shell_NotifyIcon 调用异常: %s", exc)
            return False
        if ok:
            logger.info("托盘图标已添加")
        else:
            logger.warning(
                "Shell_NotifyIcon(NIM_ADD) 失败，GetLastError=%s", ctypes.get_last_error()
            )
        return ok

    def _load_icon(self) -> int:
        if self.icon_path and os.path.isfile(self.icon_path):
            handle = user32.LoadImageW(
                None, self.icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE
            )
            if handle:
                return int(handle)
            logger.warning("加载图标文件失败: %s", self.icon_path)
        handle = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        return int(handle or 0)

    # ---------------------------------------------------------------- 添加与重试
    def _try_add(self) -> bool:
        """尝试添加托盘图标；失败则安排重试，成功则通知上层。"""
        if self._add_icon():
            self._retry_count = 0
            self._error_reported = False
            self.available = True
            self.error = None
            if self.hwnd:
                user32.KillTimer(self.hwnd, RETRY_TIMER_ID)
            self._call(self.on_ready)
            return True
        self._schedule_retry()
        return False

    def _schedule_retry(self) -> None:
        self._retry_count += 1
        if self._retry_count > RETRY_MAX_ATTEMPTS:
            self._report_failure()
            return
        if self._retry_count == 1:
            logger.warning(
                "托盘图标添加失败，将每 %s 秒重试（最多 %s 次）",
                RETRY_INTERVAL_MS // 1000, RETRY_MAX_ATTEMPTS,
            )
        if self.hwnd:
            user32.SetTimer(self.hwnd, RETRY_TIMER_ID, RETRY_INTERVAL_MS, None)

    def _report_failure(self) -> None:
        if self._error_reported:
            return
        self._error_reported = True
        self.available = False
        self.error = RuntimeError("系统托盘暂不可用（Shell_NotifyIcon 添加托盘图标失败）")
        logger.warning("托盘图标重试 %s 次仍失败", RETRY_MAX_ATTEMPTS)
        if self.on_error:
            try:
                self.on_error(self.error)
            except Exception:
                logger.exception("on_error 回调出错")

    def _call(self, callback, *args) -> None:
        if not callback:
            return
        try:
            callback(*args)
        except Exception:
            logger.exception("托盘回调出错")

    # ---------------------------------------------------------------- 线程主体
    def _run(self) -> None:
        try:
            hinst = kernel32.GetModuleHandleW(None)
            TrayIcon._instance_counter += 1
            self._class_name = f"BaiduQuickTranslateTray{os.getpid()}_{TrayIcon._instance_counter}"
            self._wndproc = WNDPROC(self._window_proc)

            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = self._wndproc
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = hinst
            wc.hIcon = 0
            wc.hCursor = 0
            wc.hbrBackground = 0
            wc.lpszMenuName = None
            wc.lpszClassName = self._class_name
            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom and ctypes.get_last_error() != ERROR_CLASS_ALREADY_EXISTS:
                self._fail(RuntimeError(f"RegisterClass 失败（{ctypes.get_last_error()}）"))
                return

            self.hwnd = user32.CreateWindowExW(
                0, self._class_name, self._class_name, 0,
                0, 0, 0, 0, None, None, hinst, None,
            )
            if not self.hwnd:
                self._fail(RuntimeError(f"创建托盘窗口失败（{ctypes.get_last_error()}）"))
                return

            self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            self.hicon = self._load_icon()
            # 第一次添加；失败不退出线程，改由 WM_TIMER 定时重试
            self._try_add()
            self._ready.set()

            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # 兜底，避免线程静默死掉
            logger.exception("托盘线程异常")
            self._fail(exc)
        finally:
            if self.hwnd:
                try:
                    nid = self._make_nid()
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                except OSError:
                    pass
            self.available = False
            self._ready.set()
            if self._class_name:
                try:
                    user32.UnregisterClassW(self._class_name, kernel32.GetModuleHandleW(None))
                except OSError:
                    pass

    # ---------------------------------------------------------------- 窗口过程
    def _window_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TIMER and int(wparam) == RETRY_TIMER_ID:
                self._try_add()
                return 0

            if msg == WM_TRAYICON:
                event = int(lparam) & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._call(self.on_left_click)
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    point = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    self._call(self.on_right_click, (int(point.x), int(point.y)))
                return 0

            if self._taskbar_created and msg == self._taskbar_created:
                # 资源管理器重启后需要重新添加图标，这里重置重试计数再试
                self._retry_count = 0
                self._error_reported = False
                self._try_add()
                return 0

            if msg == WM_SET_TOOLTIP:
                tooltip = self._pending_tooltip
                self._pending_tooltip = None
                if tooltip is not None and hwnd:
                    nid = self._make_nid()
                    nid.uFlags = NIF_TIP
                    nid.szTip = tooltip
                    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                return 0

            if msg == WM_BALLOON:
                balloon = self._pending_balloon
                self._pending_balloon = None
                if balloon and hwnd:
                    title, text = balloon
                    nid = self._make_nid()
                    nid.uFlags = NIF_INFO
                    nid.szInfoTitle = title
                    nid.szInfo = text
                    nid.dwInfoFlags = NIIF_INFO | NIIF_NOSOUND
                    nid.uVersion = 0
                    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                return 0

            if msg == WM_CLOSE:
                self.available = False
                if hwnd:
                    user32.KillTimer(hwnd, RETRY_TIMER_ID)
                    nid = self._make_nid()
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                    user32.DestroyWindow(hwnd)
                return 0

            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            logger.exception("托盘窗口过程异常")
        # LPARAM 有时是很大的无符号值，转成有符号再交给 DefWindowProc，避免溢出
        try:
            return int(
                user32.DefWindowProcW(
                    wintypes.HWND(hwnd),
                    wintypes.UINT(msg),
                    ctypes.c_size_t(wparam),
                    ctypes.c_ssize_t(lparam),
                )
            )
        except (ctypes.ArgumentError, OverflowError, TypeError):
            return 0
