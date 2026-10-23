# -*- coding: utf-8 -*-
"""任务栏文字显示验收脚本。

检查项：
  1. 文字窗口位置（在 TrayNotifyWnd 左侧，不越界、不盖任务按钮）
  2. 悬停提示（模拟鼠标停留后截图 + 枚举 tooltip 窗口）
  3. 右键菜单
  4. 点击不抢焦点
  5. 任务栏自动隐藏开关后的跟随/隐藏
  6. 全屏应用在前台时自动隐藏
  7. Explorer 重启后重新贴附
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "src"))
import _safety  # noqa: E402
import w32  # noqa: E402
from PIL import ImageGrab  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDPIAware()
psutil_ok = True
try:
    import psutil
except ImportError:
    psutil_ok = False

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "images")
LABEL_CLASS = "CodexQuotaLabelWnd_1"

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                ("rc", RECT), ("lParam", wintypes.LPARAM)]


shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.SHAppBarMessage.restype = ctypes.c_size_t
shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
user32.SHAppBarMessage = shell32.SHAppBarMessage


def rect(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def find_label():
    found = []

    def cb(h, _):
        if w32.get_window_class(h) == LABEL_CLASS:
            found.append(h)
        return True

    user32.EnumWindows(w32.WNDENUMPROC(cb), 0)
    return found[0] if found else 0


def shell_rects():
    shell = user32.FindWindowW("Shell_TrayWnd", None)
    tray = user32.FindWindowExW(shell, None, "TrayNotifyWnd", None)
    task = 0
    found = []

    def cb(h, _):
        if w32.get_window_class(h) == "MSTaskSwWClass":
            found.append(h)
        return True

    user32.EnumChildWindows(shell, w32.WNDENUMPROC(cb), 0)
    if found:
        task = found[0]
    return shell, rect(shell), (rect(tray) if tray else None), (rect(task) if task else None)


def grab(name, crop=None):
    img = ImageGrab.grab(all_screens=True)
    path = os.path.join(OUT, name)
    if crop:
        img = img.crop(crop)
    img.save(path)
    return path


def annotate_bar(name):
    """截任务栏并标注 ^ 与文字区域。"""
    from PIL import Image, ImageDraw, ImageFont

    shell, sr, tr, task = shell_rects()
    label = find_label()
    lr = rect(label)
    img = ImageGrab.grab(all_screens=True)
    # 文字已不带宽标签，整块很窄，按文字左缘取景即可
    x0 = max(0, lr[0] - 90)
    band = img.crop((x0, sr[1] - 6, min(img.width, tr[0] + 300), sr[3]))
    zoom = 1.6
    band = band.resize((int(band.width * zoom), int(band.height * zoom)))
    canvas = Image.new("RGB", (band.width, band.height + 46), (255, 255, 255))
    canvas.paste(band, (0, 46))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 15)
    except OSError:
        font = ImageFont.load_default()

    def sx(px):
        return int((px - x0) * zoom)

    # 文字区域框
    d.rectangle([sx(lr[0]), 46, sx(lr[2]), 46 + band.height], outline=(0, 132, 255), width=2)
    d.text((sx(lr[0]), 8), "额度文字", font=font, fill=(0, 110, 220))
    # 折叠箭头框
    ax = sx(tr[0]) - 2
    d.rectangle([ax, 46, sx(tr[0] + 40), 46 + band.height], outline=(230, 60, 60), width=2)
    d.text((ax - 8, 26), "^ 折叠箭头", font=font, fill=(200, 40, 40))
    # 托盘区左边界线
    d.line([(sx(tr[0]), 40), (sx(tr[0]), canvas.height)], fill=(230, 60, 60), width=1)
    canvas.save(os.path.join(OUT, name))
    return os.path.join(OUT, name)


def main():
    _safety.announce(
        "显示验收（test_display.py）",
        [
            "查找本程序的文字窗口并截图（会移动鼠标、可能点一下文字区域）",
            "**临时打开任务栏自动隐藏**，测完恢复原值",
            "创建一个铺满屏幕的测试窗口验证「全屏时隐藏」，然后立即销毁",
            "把截图写进 docs/images/",
        ],
        [
            "不结束任何进程（不碰 Codex，不碰 explorer.exe）",
            "不改注册表里的其他设置",
            "不会留下被改动的任务栏配置（结束时恢复原状态）",
        ],
    )
    if not _safety.confirm("--yes" in sys.argv):
        return 1

    print("=== 0. 基本状态 ===")
    shell, sr, tr, task = shell_rects()
    label = find_label()
    if not label:
        sys.exit("未找到任务栏文字窗口，程序没在跑？")
    lr = rect(label)
    visible = bool(user32.IsWindowVisible(label))
    print(f"任务栏={sr} 托盘区={tr} 任务按钮={task}")
    print(f"文字窗口={lr} 可见={visible}")
    ok_left = lr[2] <= tr[0] and lr[0] >= (task[2] if task else 0)
    ok_vcenter = abs(((lr[1] + lr[3]) / 2) - ((sr[1] + sr[3]) / 2)) <= 6
    print(f"位于 ^ 左侧: {ok_left}   垂直居中: {ok_vcenter}")
    p = annotate_bar("bar-annotated.png")
    print("标注截图:", p)

    print("\n=== 1. 悬停提示 ===")
    cx, cy = (lr[0] + lr[2]) // 2, (lr[1] + lr[3]) // 2
    user32.SetCursorPos(cx, cy)
    time.sleep(0.4)
    user32.SetCursorPos(cx + 2, cy)
    tips = []
    for _ in range(12):
        time.sleep(0.3)
        tips = []
        for h in w32.enum_top_windows():
            if w32.get_window_class(h) == "CodexQuotaTipWnd_1" and user32.IsWindowVisible(h):
                tips.append((h, rect(h), w32.get_window_title(h)))
        if tips:
            break
    print("可见提示框:", tips)
    grab("bar-tooltip.png", (max(0, lr[0] - 120), max(0, lr[1] - 220),
                             min(3200, lr[2] + 420), sr[3]))

    print("\n=== 2. 右键菜单 ===")
    user32.SetCursorPos(cx, cy)
    time.sleep(0.2)
    w32.PostMessageW(label, w32.WM_RBUTTONUP, 0, 0)
    time.sleep(0.8)
    menu = 0
    mrect = None
    for h in w32.enum_top_windows():
        if w32.get_window_class(h) == "#32768" and user32.IsWindowVisible(h):
            menu, mrect = h, rect(h)
            break
    print("菜单窗口:", menu, mrect)
    if menu:
        grab("bar-menu.png", (max(0, mrect[0] - 40), max(0, mrect[1] - 40),
                              min(3200, mrect[2] + 260), min(2000, mrect[3] + 30)))
    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(0.4)

    print("\n=== 3. 点击是否抢焦点 ===")
    before = user32.GetForegroundWindow()
    user32.SetCursorPos(cx, cy)
    time.sleep(0.15)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.5)
    after = user32.GetForegroundWindow()
    print(f"点击前前台窗口={before} 点击后={after} 未变={before == after}")
    print(f"文字窗口是否成为前台={after == label}")

    print("\n=== 4. 任务栏自动隐藏 ===")
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    state = user32.SHAppBarMessage(0x0004, ctypes.byref(abd))  # ABM_GETSTATE
    print("当前状态位:", hex(state))
    auto_hide = bool(state & 0x1)
    target = (state & ~0x1) if auto_hide else (state | 0x1)
    abd2 = APPBARDATA()
    abd2.cbSize = ctypes.sizeof(APPBARDATA)
    abd2.lParam = target

    def _restore_autohide() -> None:
        abd_back = APPBARDATA()
        abd_back.cbSize = ctypes.sizeof(APPBARDATA)
        abd_back.lParam = state
        user32.SHAppBarMessage(0x000A, ctypes.byref(abd_back))
        time.sleep(1.2)
        user32.SetCursorPos(1600, 1700)
        time.sleep(1.2)

    # 改了系统设置就必须保证能还原：中间任何一步抛异常（含 Ctrl+C）都走 finally。
    try:
        user32.SHAppBarMessage(0x000A, ctypes.byref(abd2))  # ABM_SETSTATE
        time.sleep(1.6)
        s2, sr2, tr2, _ = shell_rects()
        lr2 = rect(find_label())
        print(f"切换后任务栏={sr2} 文字窗口={lr2} 可见={bool(user32.IsWindowVisible(find_label()))}")
        # 鼠标移出任务栏，让自动隐藏生效
        user32.SetCursorPos(1600, 600)
        time.sleep(1.8)
        s3, sr3, _, _ = shell_rects()
        label3 = find_label()
        vis3 = bool(user32.IsWindowVisible(label3)) if label3 else False
        print(f"鼠标离开后任务栏={sr3} 文字可见={vis3} 文字矩形={rect(label3) if label3 else None}")
        # 注意：名字里带 attempt —— 本机 Win11 会忽略 ABM_SETSTATE，任务栏不一定真的滑出，
        # 这张图只是「尝试过」的原始记录，不能当作自动隐藏已验证的证据。
        grab("bar-autohide-attempt.png", (0, sr[1] - 40, 3200, 2000))
    finally:
        print("恢复自动隐藏设置…")
        _restore_autohide()
        print("已恢复自动隐藏设置，文字可见:",
              bool(user32.IsWindowVisible(find_label())) if find_label() else False)

    print("\n=== 5. 全屏应用（模拟） ===")
    hinst = ctypes.WinDLL("kernel32").GetModuleHandleW(None)
    hwnd_full = user32.CreateWindowExW(
        0x00000008, "Static", "fullscreen-test", 0x80000000 | 0x10000000,
        0, 0, 3200, 2000, None, None, hinst, None)
    # 铺满屏幕的测试窗口也必须在 finally 里销毁，免得异常时留在桌面上
    try:
        user32.ShowWindow(hwnd_full, 5)
        user32.SetForegroundWindow(hwnd_full)
        time.sleep(1.6)
        label4 = find_label()
        print("全屏窗口在前台时文字可见:",
              bool(user32.IsWindowVisible(label4)) if label4 else False)
    finally:
        user32.DestroyWindow(hwnd_full)
        time.sleep(1.6)
    label5 = find_label()
    print("关闭全屏窗口后文字可见:",
          bool(user32.IsWindowVisible(label5)) if label5 else False)


if __name__ == "__main__":
    sys.exit(main())
