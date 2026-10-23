# -*- coding: utf-8 -*-
"""任务栏自动隐藏场景专项测试：切换开关 → 观察任务栏与文字窗口，最后恢复。

注意：本脚本会真实改动「任务栏自动隐藏」这个系统设置（结束时恢复原值）。

要点：光标停在屏幕下边缘时任务栏不会收起，必须先 SetCursorPos 把光标移离任务栏
再观察，否则会误判成「ABM_SETSTATE 不生效」。实测数据见 docs\\测试报告.md 第五节。
"""
import ctypes
import os
import sys
import time
import winreg
from ctypes import wintypes

import _safety

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import w32  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                ("rc", RECT), ("lParam", wintypes.LPARAM)]


shell32.SHAppBarMessage.restype = ctypes.c_size_t
shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]

ABM_GETSTATE = 0x0004
ABM_SETSTATE = 0x000A


def rect(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def label_hwnd():
    found = []

    def cb(h, _):
        if w32.get_window_class(h) == "CodexQuotaLabelWnd_1":
            found.append(h)
        return True

    user32.EnumWindows(w32.WNDENUMPROC(cb), 0)
    return found[0] if found else 0


def reg_state():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects3")
        data, _ = winreg.QueryValueEx(key, "Settings")
        b = bytes(data)
        return f"autohide=0x{b[8]:02x}"
    except OSError as exc:
        return f"ERR {exc}"


def set_state(value):
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    abd.lParam = value
    shell32.SHAppBarMessage(ABM_SETSTATE, ctypes.byref(abd))


def snapshot(tag):
    shell = user32.FindWindowW("Shell_TrayWnd", None)
    h = label_hwnd()
    print(f"[{tag}] 任务栏={rect(shell)} 文字可见={bool(user32.IsWindowVisible(h)) if h else None} "
          f"文字矩形={rect(h) if h else None} 注册表={reg_state()}")


_abd0 = APPBARDATA()
_abd0.cbSize = ctypes.sizeof(APPBARDATA)
state = shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(_abd0))
print("ABM_GETSTATE =", state, " -> autohide =", bool(state & 0x1))
snapshot("初始")

_safety.announce(
    "任务栏自动隐藏专项（test_autohide.py）",
    [
        "**临时打开「任务栏自动隐藏」**（改动系统设置）",
        "把鼠标移到屏幕下边缘再移开，观察任务栏与文字窗口",
        "结束时恢复成原来的设置",
    ],
    [
        "不结束任何进程，不重启 explorer.exe",
        "不改动任务栏以外的系统设置",
    ],
)
if not _safety.confirm("--yes" in sys.argv):
    sys.exit(1)

target = (state | 0x1) & ~0x2  # 只开自动隐藏，不强制置顶
# 改了系统设置就必须保证能还原：中间任何一步抛异常（含 Ctrl+C）都走 finally。
try:
    print(f"设置 ABS_AUTOHIDE（lParam={target}）")
    set_state(target)
    time.sleep(2.0)
    snapshot("设置后")

    user32.SetCursorPos(1600, 700)   # 鼠标移离任务栏
    time.sleep(2.0)
    snapshot("鼠标离开 2s")

    user32.SetCursorPos(1600, 1995)  # 鼠标移到屏幕下边缘，唤起任务栏
    time.sleep(1.5)
    snapshot("鼠标贴边唤起")
finally:
    print(f"恢复原状态（lParam={state}）")
    set_state(state)
    time.sleep(1.5)
    user32.SetCursorPos(1600, 1700)
    time.sleep(1.5)
    snapshot("恢复后")
