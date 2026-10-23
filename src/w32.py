# -*- coding: utf-8 -*-
"""Win32 薄封装（纯 ctypes，无第三方依赖）。

只暴露本工具用到的部分：托盘图标、弹出菜单、消息循环、窗口枚举、
进程路径查询、Job 对象（用于兜底回收子进程）。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---------------------------------------------------------------- 常量
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1

NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETFOCUS, NIM_SETVERSION = 0, 1, 2, 3, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_STATE, NIF_INFO, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x8, 0x10, 0x80

MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_DISABLED = 0x0002
MF_SEPARATOR = 0x0800

TPM_LEFTALIGN = 0x0000
TPM_CENTERALIGN = 0x0004
TPM_RIGHTALIGN = 0x0008
TPM_TOPALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

SM_CXSMICON = 49
SM_CYSMICON = 50

ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

LR_DEFAULTCOLOR = 0x0000
ICON_RES_VERSION = 0x00030000

SW_RESTORE = 9
SW_SHOW = 5

CREATE_NO_WINDOW = 0x08000000

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


# ---------------------------------------------------------------- 结构体
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
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# ---------------------------------------------------------------- 函数签名
user32.CreateIconFromResourceEx.restype = wintypes.HICON
user32.CreateIconFromResourceEx.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL

shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p,
]
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.EnumWindows.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.GetLastError.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

shell32.ShellExecuteW.restype = wintypes.HINSTANCE
shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, ctypes.c_int,
]


# ---------------------------------------------------------------- 封装
def set_dpi_aware() -> None:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def small_icon_size() -> int:
    n = int(user32.GetSystemMetrics(SM_CXSMICON) or 16)
    return max(16, min(n, 64))


def icon_from_ico_bytes(data: bytes, size: int) -> int:
    """从 .ico 字节里取出指定尺寸的图标资源，转成 HICON。"""
    import struct

    if len(data) < 6:
        raise ValueError("ico too short")
    count = struct.unpack_from("<H", data, 4)[0]
    best = None
    for i in range(count):
        w, h, _c, _r, _p, _b, sz, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        edge = w or 256
        if best is None or abs(edge - size) < abs(best[0] - size):
            best = (edge, sz, off)
    if best is None:
        raise ValueError("ico has no entries")
    _edge, sz, off = best
    payload = data[off:off + sz]
    hicon = user32.CreateIconFromResourceEx(
        payload, len(payload), True, ICON_RES_VERSION, 0, 0, LR_DEFAULTCOLOR
    )
    if not hicon:
        raise ctypes.WinError(ctypes.get_last_error())
    return hicon


def notify_add(hwnd: int, uid: int, hicon: int, tip: str, callback_msg: int) -> None:
    nid = _make_nid(hwnd, uid, hicon, tip, callback_msg, NIF_MESSAGE | NIF_ICON | NIF_TIP)
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        raise OSError("Shell_NotifyIcon NIM_ADD 失败")


def notify_modify(hwnd: int, uid: int, hicon: int | None, tip: str | None,
                  callback_msg: int) -> None:
    flags = NIF_MESSAGE
    if hicon is not None:
        flags |= NIF_ICON
    if tip is not None:
        flags |= NIF_TIP
    nid = _make_nid(hwnd, uid, hicon or 0, tip or "", callback_msg, flags)
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))


def notify_delete(hwnd: int, uid: int) -> None:
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = uid
    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))


def _make_nid(hwnd, uid, hicon, tip, callback_msg, flags) -> NOTIFYICONDATAW:
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = uid
    nid.uFlags = flags
    nid.uCallbackMessage = callback_msg
    nid.hIcon = hicon
    nid.szTip = (tip or "")[:120]
    return nid


def show_menu(hwnd: int, items: list[tuple[int, str]]) -> int:
    """弹出右键菜单，返回被点中的命令 id（0 表示取消）。"""
    menu = user32.CreatePopupMenu()
    if not menu:
        return 0
    try:
        for cmd, text in items:
            if text == "-":
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                user32.AppendMenuW(menu, MF_STRING, cmd, text)
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # 经典 workaround：先把窗口设前台，否则点菜单外部时菜单不会消失
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_LEFTALIGN | TPM_TOPALIGN,
            pt.x, pt.y, 0, hwnd, None,
        )
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        return int(cmd or 0)
    finally:
        user32.DestroyMenu(menu)


def get_window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_window_rect(hwnd: int) -> tuple[int, int]:
    rc = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return (0, 0)
    return (rc.right - rc.left, rc.bottom - rc.top)


def enum_top_windows() -> list[int]:
    out: list[int] = []

    def _cb(hwnd, _lparam):
        out.append(int(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def process_image_path(pid: int) -> str | None:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(h)


def create_mutex(name: str):
    """返回 (handle, already_running)。handle 需在进程生命周期内保持引用。"""
    h = kernel32.CreateMutexW(None, False, name)
    err = kernel32.GetLastError()
    return h, err == ERROR_ALREADY_EXISTS


class KillOnCloseJob:
    """把子进程放进 Job 对象，本进程异常退出时系统会自动回收整棵子进程树。"""

    def __init__(self) -> None:
        self._job = kernel32.CreateJobObjectW(None, None)
        if self._job:
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(
                self._job, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info),
            )

    @property
    def valid(self) -> bool:
        return bool(self._job)

    def assign(self, pid: int) -> bool:
        if not self._job:
            return False
        h = kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
        if not h:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(self._job, h))
        finally:
            kernel32.CloseHandle(h)

    def close(self) -> None:
        if self._job:
            kernel32.CloseHandle(self._job)
            self._job = 0


def shell_open(target: str) -> bool:
    """ShellExecute 打开目标（支持 .lnk / shell: 路径）。"""
    rc = shell32.ShellExecuteW(None, None, target, None, None, SW_SHOW)
    return int(rc) > 32


def launch_aumid(aumid: str) -> bool:
    """用 explorer 启动打包应用（AUMID）。"""
    import subprocess

    try:
        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{aumid}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def message_loop(wndproc_holder: dict) -> None:
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def register_window_class(name: str, wndproc) -> bool:
    """wndproc 必须是 WNDPROC 实例（调用方负责保持引用，否则会被 GC）。"""
    wc = WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p).value
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = name
    atom = user32.RegisterClassW(ctypes.byref(wc))
    return bool(atom)


def create_message_window(name: str) -> int:
    hwnd = user32.CreateWindowExW(
        0, name, name, 0, 0, 0, 0, 0, None, None, kernel32.GetModuleHandleW(None), None
    )
    return int(hwnd or 0)


# ---- 供上层以 w32.xxx 形式直接调用的常用函数别名 ----
PostMessageW = user32.PostMessageW
DestroyWindow = user32.DestroyWindow
ShowWindow = user32.ShowWindow
SetForegroundWindow = user32.SetForegroundWindow
IsWindowVisible = user32.IsWindowVisible
IsIconic = user32.IsIconic
GetCursorPos = user32.GetCursorPos
EnumWindows = user32.EnumWindows
GetSystemMetrics = user32.GetSystemMetrics
RegisterWindowMessageW = user32.RegisterWindowMessageW
TranslateMessage = user32.TranslateMessage
DispatchMessageW = user32.DispatchMessageW
GetMessageW = user32.GetMessageW
RegisterClassW = user32.RegisterClassW
CreateWindowExW = user32.CreateWindowExW
DefWindowProcW = user32.DefWindowProcW
PostQuitMessage = user32.PostQuitMessage


def exit_code_from_winerror() -> str:
    return f"WinError {ctypes.get_last_error()}"


__all__ = [n for n in dir() if not n.startswith("_")]
