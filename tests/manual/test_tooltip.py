# -*- coding: utf-8 -*-
"""悬停提示专项：把鼠标停在文字上，逐步观察 tooltip 窗口并截图。

只做两件事：移动鼠标、写一张截图（docs/images/bar-tooltip-live.png）。
不结束任何进程、不改系统设置。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import w32  # noqa: E402
from PIL import ImageGrab  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDPIAware()

MOUSEEVENTF_MOVE = 0x0001


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def rect(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def find_label():
    found = []

    def cb(h, _):
        if w32.get_window_class(h) == "CodexQuotaLabelWnd_1":
            found.append(h)
        return True

    user32.EnumWindows(w32.WNDENUMPROC(cb), 0)
    return found[0] if found else 0


def tooltips():
    out = []
    for h in w32.enum_top_windows():
        if w32.get_window_class(h) == "CodexQuotaTipWnd_1" and user32.IsWindowVisible(h):
            out.append((h, rect(h)))
    return out


hwnd = find_label()
r = rect(hwnd)
cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
print("文字窗口", hwnd, r, "鼠标目标", cx, cy)

# 先移开，再移入，模拟真实"进入"
user32.SetCursorPos(cx - 400, cy - 300)
time.sleep(0.4)
user32.SetCursorPos(cx, cy)
for i in range(10):
    time.sleep(0.5)
    tips = tooltips()
    print(f"{(i+1)*0.5:.1f}s tooltip={tips}")

shot = ImageGrab.grab(all_screens=True)
shot.crop((max(0, r[0] - 200), max(0, r[1] - 200), min(3200, r[2] + 500), 2000)).save(
    os.path.join(ROOT, "docs", "images", "bar-tooltip-live.png"))
print("截图已保存 docs/images/bar-tooltip-live.png")
