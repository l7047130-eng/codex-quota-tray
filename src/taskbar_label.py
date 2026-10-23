# -*- coding: utf-8 -*-
"""任务栏文字显示（两行布局）+ 悬停卡片。

位置：任务栏折叠箭头 `^` 左侧，右缘贴 TrayNotifyWnd 左边界。

布局（对齐 Windows 11 任务栏「时间/日期」的两行紧凑排版，两行左对齐、无前缀）：

    5h 97%
    周 91%

字号按任务栏厚度取（FONT_RATIO），行距按 PITCH_PER_FONT 取；
画完按墨迹紧裁，窗口矩形就等于文字范围，垂直居中、悬停区域都以此为准。

字体：拉丁字母与数字用 Segoe UI Variable Text（Win11 任务栏字体），
中文回落到 Microsoft YaHei UI；按字符分组绘制并按基线对齐，保证中英混排不歪。

Windows 11 任务栏由 XAML 合成层绘制，WS_CHILD 子窗口会被完全盖住
（实测洋红测试窗口 0 像素可见），因此这里是贴附任务栏的分层窗口。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

import w32

user32 = w32.user32
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# ---------------------------------------------------------------- 常量
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
MA_NOACTIVATE = 3
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

WM_APP = 0x8000
WM_REFRESH_VIEW = WM_APP + 0x21

TIMER_GEOM = 1
TIMER_THEME = 2
TIMER_TIP = 3

TME_LEAVE = 0x00000002

CMD_REFRESH = 1001
CMD_EXIT = 1002

GEOM_INTERVAL_MS = 500
THEME_INTERVAL_MS = 5000
TIP_DELAY_MS = 350
GAP_TO_TRAY = 10
GAP_TO_TASKLIST = 12

# 字号占任务栏厚度的比例。实测校准（175% 缩放，任务栏厚 84px）：
#   系统自带时间/日期：数字墨迹高 15px，两行基线相距 30px，整块墨迹 45px
#   本程序：字号 20px 时数字墨迹正好 15px —— 与系统时钟同号
# 所以 84 × 0.238 ≈ 20。换成别的任务栏厚度/缩放时按比例跟着变。
FONT_RATIO = 0.238

# 两行基线之间的距离 = 字号 × 该系数（20px 字号 → 26px 基线距）。
#
# 为什么不是时钟的 30px：时钟两行都是拉丁数字，字底正好落在基线上，
# 所以它的「基线距」就等于人眼看到的「两行底边差」。本程序第二行是汉字「周」，
# 汉字要占满字身框、字底比基线再低约 5px。若基线照抄 30px，屏幕上量出来
# 两行底边差是 34px、整块 49px，比时钟的 30/45 明显偏松。
# 取 26px 基线距后：截图实测两行底边差 30px、整块 45px —— 与时钟完全一致，
# 这才是用户实际看到的效果。
PITCH_PER_FONT = 1.3

# 悬停卡片的基础字号 = 任务栏文字字号 × 该系数（1.0 表示与任务栏文字同号）
CARD_FONT_RATIO = 1.0


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]


user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                 wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    wintypes.HDC, ctypes.POINTER(POINT), wintypes.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [POINT]
user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]

gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

# Win11 任务栏字体：Segoe UI Variable（数字/拉丁）+ 微软雅黑 UI（中文）
LATIN_FONTS = (
    r"C:\Windows\Fonts\SegUIVar.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
)
CJK_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)


def _first_font(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (0x2E80 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFFEF or 0x3000 <= code <= 0x303F)


def _runs(text: str) -> list[tuple[str, bool]]:
    """按中英文切成若干段，返回 (片段, 是否中文)。"""
    out: list[tuple[str, bool]] = []
    for ch in text:
        cjk = _is_cjk(ch)
        if out and out[-1][1] == cjk:
            out[-1] = (out[-1][0] + ch, cjk)
        else:
            out.append((ch, cjk))
    return out


class Fonts:
    """同一字号下的拉丁/中文双字体。"""

    def __init__(self, size: int) -> None:
        self.size = max(6, int(round(size)))
        self.latin = _first_font(LATIN_FONTS, self.size)
        self.cjk = _first_font(CJK_FONTS, self.size)

    def font_for(self, cjk: bool):
        return self.cjk if cjk else self.latin

    @property
    def ascent(self) -> int:
        return self.latin.getmetrics()[0]

    @property
    def line_height(self) -> int:
        ascent, descent = self.latin.getmetrics()
        return ascent + descent

    def width(self, text: str) -> float:
        total = 0.0
        for chunk, cjk in _runs(text):
            font = self.font_for(cjk)
            left, _t, right, _b = font.getbbox(chunk)
            total += right - left
        return total

    def draw(self, draw: ImageDraw.ImageDraw, x: float, baseline: float, text: str,
             fill) -> float:
        """按基线绘制，返回结束横坐标。"""
        cursor = float(x)
        for chunk, cjk in _runs(text):
            font = self.font_for(cjk)
            left, top, right, _bottom = font.getbbox(chunk)
            draw.text((cursor - left, baseline - font.getmetrics()[0]), chunk,
                      font=font, fill=fill)
            cursor += right - left
        return cursor


@dataclass(frozen=True)
class Palette:
    text: tuple
    card_bg: tuple
    card_border: tuple
    card_title: tuple
    card_label: tuple
    card_value: tuple
    card_secondary: tuple


# 任务栏文字统一纯黑（用户指定）；深色任务栏才转白。
PALETTE_LIGHT = Palette(
    text=BLACK,
    card_bg=(255, 255, 255, 252), card_border=(224, 224, 228, 255),
    card_title=(24, 24, 26, 255), card_label=(58, 58, 62, 255),
    card_value=(0, 0, 0, 255), card_secondary=(138, 138, 145, 255),
)
PALETTE_DARK = Palette(
    text=WHITE,
    card_bg=(44, 44, 47, 250), card_border=(72, 72, 78, 255),
    card_title=(242, 242, 246, 255), card_label=(216, 216, 222, 255),
    card_value=(255, 255, 255, 255), card_secondary=(154, 154, 162, 255),
)


@dataclass(frozen=True)
class QuotaView:
    """界面需要的全部信息。state: ok / loading / error"""

    state: str = "loading"
    five: Optional[int] = None      # 剩余百分比
    week: Optional[int] = None
    five_reset: str = ""            # 「重置 23:53 · 4 小时 59 分后」
    week_reset: str = ""
    message: str = ""               # 失败原因（简短）

    @property
    def loading(self) -> bool:
        return self.state == "loading"

    @property
    def failed(self) -> bool:
        return self.state == "error"


# ---------------------------------------------------------------- 任务栏两行文字
def render_text(view: QuotaView, palette: Palette, line_px: int) -> Image.Image:
    """只画两行，左对齐，没有「Codex：」前缀：

        5h 97%
        周 91%

    行距用 PITCH_PER_FONT 固定成「基线到基线」的距离，和系统时钟一致；
    画完按墨迹紧裁，所以窗口矩形就等于文字范围 —— 右缘贴托盘的距离、
    垂直居中、以及悬停区域都直接以这两行字为准。
    """
    fonts = Fonts(line_px)
    pitch = max(1, round(line_px * PITCH_PER_FONT))

    if view.loading:
        lines = ("5h …", "周 …")
    elif view.failed:
        lines = ("5h —", "周 —")
    else:
        lines = ("5h —" if view.five is None else f"5h {view.five}%",
                 "周 —" if view.week is None else f"周 {view.week}%")

    # 先在透明画布上按基线排版（左右各留 4px 防抗锯齿被裁）
    scratch_w = int(round(max(fonts.width(text) for text in lines))) + 8
    scratch_h = fonts.ascent + pitch * (len(lines) - 1) + fonts.line_height + 8
    scratch = Image.new("RGBA", (max(1, scratch_w), max(1, scratch_h)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    baseline = 4 + fonts.ascent
    for index, text in enumerate(lines):
        fonts.draw(draw, 4, baseline + pitch * index, text, palette.text)

    box = scratch.getbbox() or (0, 0, 1, 1)
    ink = scratch.crop(box)

    # 背景 alpha=1（肉眼不可见）：分层窗口按 alpha 命中，全透明会让点击穿透到任务栏
    image = Image.new("RGBA", (ink.width, ink.height), (0, 0, 0, 1))
    image.alpha_composite(ink, (0, 0))
    return image


# ---------------------------------------------------------------- 悬停卡片
CARD_PAD = 16
CARD_RADIUS = 10


def render_tip_card(view: QuotaView, palette: Palette, base_px: int) -> Image.Image:
    title_f = Fonts(round(base_px * 1.02))
    label_f = Fonts(round(base_px * 0.95))
    value_f = Fonts(round(base_px * 1.42))
    sub_f = Fonts(round(base_px * 0.86))

    title = "Codex 额度"
    rows: list[dict] = []
    if view.failed:
        rows = [{"kind": "status", "text": "读取失败"},
                {"kind": "sub", "text": view.message or "额度接口暂不可用"}]
    elif view.loading:
        rows = [{"kind": "status", "text": "正在读取额度…"}]
    else:
        for label, pct, reset in (("5 小时", view.five, view.five_reset),
                                  ("每周", view.week, view.week_reset)):
            rows.append({"kind": "group", "label": label,
                         "value": "—" if pct is None else f"{pct}%",
                         "sub": reset or "重置时间未知"})
            rows.append({"kind": "gap"})

    # 量尺寸
    title_w = title_f.width(title)
    group_label_w = max([label_f.width(r["label"]) for r in rows if r["kind"] == "group"] or [0])
    group_value_w = max([value_f.width(r["value"]) for r in rows if r["kind"] == "group"] or [0])
    sub_w = max([sub_f.width(r.get("sub") or r.get("text") or "")
                 for r in rows if r["kind"] in ("group", "sub")] or [0])
    status_w = max([value_f.width(r["text"]) for r in rows if r["kind"] == "status"] or [0])

    body_w = max(group_label_w + 24 + group_value_w, sub_w, status_w)
    content_w = max(title_w, body_w)

    line_h = round(sub_f.line_height * 1.35)
    height = CARD_PAD * 2 + round(title_f.line_height * 1.2) + 10 + 1 + 10
    for row in rows:
        if row["kind"] == "group":
            height += round(value_f.line_height * 1.12) + line_h
        elif row["kind"] == "status":
            height += round(value_f.line_height * 1.12)
        elif row["kind"] == "sub":
            height += line_h
        else:
            height += round(line_h * 0.72)

    width = int(round(content_w)) + CARD_PAD * 2
    height += CARD_PAD - 10  # 末行与底边留出和内边距一致的空间
    img = Image.new("RGBA", (max(120, width), max(60, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=CARD_RADIUS,
                           fill=palette.card_bg, outline=palette.card_border, width=1)

    x0 = CARD_PAD
    y = CARD_PAD
    # 标题
    title_f.draw(draw, x0, y + title_f.ascent, title, palette.card_title)
    y += round(title_f.line_height * 1.2) + 10
    draw.line([(x0, y), (img.width - 1 - CARD_PAD, y)], fill=palette.card_border, width=1)
    y += 10

    value_right = img.width - 1 - CARD_PAD
    for row in rows:
        if row["kind"] == "group":
            baseline = y + value_f.ascent
            label_f.draw(draw, x0, y + value_f.ascent, row["label"],
                         palette.card_label)
            value_w_row = value_f.width(row["value"])
            value_f.draw(draw, value_right - value_w_row, baseline, row["value"],
                         palette.card_value)
            y += round(value_f.line_height * 1.12)
            sub_f.draw(draw, x0, y + sub_f.ascent, row["sub"], palette.card_secondary)
            y += line_h
        elif row["kind"] == "status":
            value_f.draw(draw, x0, y + value_f.ascent, row["text"], palette.card_value)
            y += round(value_f.line_height * 1.12)
        elif row["kind"] == "sub":
            sub_f.draw(draw, x0, y + sub_f.ascent, row["text"], palette.card_secondary)
            y += line_h
        else:
            y += round(line_h * 0.72)
    return img


# ---------------------------------------------------------------- 绘图基础
def _to_bgra_premultiplied(img: Image.Image) -> bytes:
    img = img.convert("RGBA")
    data = img.tobytes()
    out = bytearray(len(data))
    for i in range(0, len(data), 4):
        r, g, b, a = data[i], data[i + 1], data[i + 2], data[i + 3]
        if a == 255:
            out[i], out[i + 1], out[i + 2], out[i + 3] = b, g, r, 255
        elif a == 0:
            out[i] = out[i + 1] = out[i + 2] = out[i + 3] = 0
        else:
            out[i] = b * a // 255
            out[i + 1] = g * a // 255
            out[i + 2] = r * a // 255
            out[i + 3] = a
    return bytes(out)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (r.left, r.top, r.right, r.bottom)


def apply_layered(hwnd: int, img: Image.Image, x: int, y: int) -> bool:
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = img.width
    bmi.bmiHeader.biHeight = -img.height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                  ctypes.byref(bits), None, 0)
    result = False
    old = None
    try:
        if not hbmp or not bits:
            return False
        payload = _to_bgra_premultiplied(img)
        ctypes.memmove(bits, payload, len(payload))
        old = gdi32.SelectObject(hdc_mem, hbmp)
        size = SIZE(img.width, img.height)
        src = POINT(0, 0)
        dst = POINT(int(x), int(y))
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        result = bool(user32.UpdateLayeredWindow(
            hwnd, hdc_screen, ctypes.byref(dst), ctypes.byref(size),
            hdc_mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA))
    finally:
        if hbmp:
            if old:
                gdi32.SelectObject(hdc_mem, old)
            gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
    return result


def system_is_light() -> bool:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
        return bool(value)
    except OSError:
        return False


def _fullscreen_foreground() -> bool:
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    cls = w32.get_window_class(fg)
    if cls in ("Progman", "WorkerW", "Shell_TrayWnd") or "CodexQuota" in cls:
        return False
    left, top, right, bottom = _rect(fg)
    if right - left <= 0 or bottom - top <= 0:
        return False
    monitor = user32.MonitorFromWindow(fg, 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    mon = info.rcMonitor
    mon_w = mon.right - mon.left
    mon_h = mon.bottom - mon.top
    if mon_w <= 0 or mon_h <= 0:
        return False
    return ((right - left) >= mon_w * 0.98) and ((bottom - top) >= mon_h * 0.98)


class TaskbarLabel:
    """任务栏上的额度文字，接口：run / update / stop。"""

    def __init__(self, on_refresh: Callable[[], None],
                 on_exit: Callable[[str], None], log=None,
                 class_name: str = "CodexQuotaLabelWnd_1",
                 tip_class: str = "CodexQuotaTipWnd_1") -> None:
        self._on_refresh = on_refresh
        self._on_exit = on_exit
        self._log = log
        self._class_name = class_name
        self._tip_class = tip_class
        self._hwnd = 0
        self._tip_hwnd = 0
        self._wndproc_ref = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._view = QuotaView()
        self._palette = PALETTE_DARK
        self._line_px = 22
        self._image: Optional[Image.Image] = None
        self._pos = (0, 0)
        self._shown = False
        self._closed = False
        self._last_geom = None
        self._tracking = False
        self._tip_visible = False
        self._none_reason = ""

    # ---------------------------------------------------------- 生命周期
    @property
    def hwnd(self) -> int:
        return self._hwnd

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._ready.wait(timeout)

    def run(self) -> None:
        self._wndproc_ref = w32.WNDPROC(self._dispatch)
        hinst = ctypes.WinDLL("kernel32").GetModuleHandleW(None)
        for name in (self._class_name, self._tip_class):
            wc = w32.WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p).value
            wc.hInstance = hinst
            wc.lpszClassName = name
            if not user32.RegisterClassW(ctypes.byref(wc)):
                err = ctypes.get_last_error()
                if err not in (0, 1410):
                    raise OSError(f"RegisterClass 失败: {err}")

        ex_style = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        self._hwnd = user32.CreateWindowExW(
            ex_style, self._class_name, "Codex 额度", WS_POPUP,
            0, 0, 8, 8, None, None, hinst, None)
        if not self._hwnd:
            raise OSError(f"CreateWindow 失败: {ctypes.get_last_error()}")
        self._tip_hwnd = user32.CreateWindowExW(
            ex_style, self._tip_class, "Codex 额度提示", WS_POPUP,
            0, 0, 8, 8, None, None, hinst, None)

        self._refresh_palette()
        user32.SetTimer(self._hwnd, TIMER_GEOM, GEOM_INTERVAL_MS, None)
        user32.SetTimer(self._hwnd, TIMER_THEME, THEME_INTERVAL_MS, None)
        self._ready.set()
        self._rebuild()
        w32.message_loop({})
        self._closed = True

    def stop(self) -> None:
        if self._hwnd:
            w32.PostMessageW(self._hwnd, w32.WM_CLOSE, 0, 0)

    def update(self, view: QuotaView) -> None:
        with self._lock:
            self._view = view
        if self._hwnd and not self._closed:
            w32.PostMessageW(self._hwnd, WM_REFRESH_VIEW, 0, 0)

    # ---------------------------------------------------------- 消息
    def _dispatch(self, hwnd, msg, wparam, lparam):
        try:
            return self._handle(hwnd, msg, wparam, lparam)
        except Exception as exc:
            if self._log:
                self._log.exception("任务栏文字 wndproc 异常: %s", exc)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle(self, hwnd, msg, wparam, lparam):
        if hwnd != self._hwnd:
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        if msg == WM_REFRESH_VIEW:
            self._rebuild()
            return 0
        if msg == 0x0084:  # WM_NCHITTEST
            return 1  # HTCLIENT
        if msg == 0x0021:  # WM_MOUSEACTIVATE
            return MA_NOACTIVATE
        if msg == 0x0200:  # WM_MOUSEMOVE
            if not self._tracking:
                self._tracking = True
                tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE,
                                      self._hwnd, 0)
                user32.TrackMouseEvent(ctypes.byref(tme))
                user32.SetTimer(self._hwnd, TIMER_TIP, TIP_DELAY_MS, None)
            return 0
        if msg == 0x02A3:  # WM_MOUSELEAVE
            self._tracking = False
            user32.KillTimer(self._hwnd, TIMER_TIP)
            self._hide_tip()
            return 0
        if msg in (0x0201, 0x0202, 0x0203):  # 左键只吞掉
            return 0
        if msg == w32.WM_RBUTTONUP:
            self._hide_tip()
            self._show_menu(hwnd)
            return 0
        if msg == w32.WM_CLOSE:
            self._hide_tip()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == w32.WM_DESTROY:
            for timer_id in (TIMER_GEOM, TIMER_THEME, TIMER_TIP):
                user32.KillTimer(hwnd, timer_id)
            if self._tip_hwnd:
                user32.DestroyWindow(self._tip_hwnd)
                self._tip_hwnd = 0
            user32.PostQuitMessage(0)
            return 0
        if msg == 0x0113:  # WM_TIMER
            timer_id = int(wparam)
            if timer_id == TIMER_THEME:
                self._refresh_palette()
                self._rebuild()
            elif timer_id == TIMER_GEOM:
                self._sync_geometry()
            elif timer_id == TIMER_TIP:
                user32.KillTimer(self._hwnd, TIMER_TIP)
                self._show_tip()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self, hwnd) -> None:
        # 弹菜单需要本窗口是前台，否则点菜单外面不会消失；
        # 菜单结束后把焦点还给原来的窗口。
        previous = user32.GetForegroundWindow()
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        try:
            command = w32.show_menu(hwnd, [
                (CMD_REFRESH, "立即刷新"),
                (0, "-"),
                (CMD_EXIT, "退出"),
            ])
        finally:
            if previous and previous != hwnd and user32.IsWindow(previous):
                try:
                    user32.SetForegroundWindow(previous)
                except Exception:
                    pass
        if command == CMD_REFRESH:
            self._on_refresh()
        elif command == CMD_EXIT:
            self._on_exit("用户选择退出")

    # ---------------------------------------------------------- 绘制
    def _refresh_palette(self) -> None:
        self._palette = PALETTE_LIGHT if system_is_light() else PALETTE_DARK

    def _rebuild(self) -> None:
        if not self._hwnd or self._closed:
            return
        with self._lock:
            view = self._view
        placement = self._compute_placement()
        if placement is None:
            self._hide(self._none_reason or "几何不可用")
            return
        self._line_px = max(11, round(placement["thickness"] * FONT_RATIO))
        image = render_text(view, self._palette, self._line_px)
        if not apply_layered(self._hwnd, image, placement["x"], placement["y"]):
            self._hide("UpdateLayeredWindow 失败")
            return
        self._image = image
        self._pos = (placement["x"], placement["y"])
        self._last_geom = (placement["x"], placement["y"], placement["thickness"], image.size)
        self._show()

    def _sync_geometry(self) -> None:
        if not self._hwnd or self._closed:
            return
        placement = self._compute_placement()
        if placement is None:
            self._hide(self._none_reason or "几何不可用")
            return
        want_line = max(11, round(placement["thickness"] * FONT_RATIO))
        key = (placement["x"], placement["y"], placement["thickness"],
               self._image.size if self._image else None)
        if want_line != self._line_px:
            self._rebuild()
            return
        if key != self._last_geom:
            self._last_geom = key
            with self._lock:
                view = self._view
            image = render_text(view, self._palette, self._line_px)
            apply_layered(self._hwnd, image, placement["x"], placement["y"])
            self._image = image
            self._pos = (placement["x"], placement["y"])
        self._show()
        self._ensure_on_top()

    def _ensure_on_top(self) -> None:
        """任务栏激活后会升到置顶层顶部把我们压住，检测到才重新置顶。

        只在真被压住时才调用：频繁 SetWindowPos 会打断悬停卡片。
        """
        if not self._image:
            return
        cx = self._pos[0] + self._image.width // 2
        cy = self._pos[1] + self._image.height // 2
        if user32.WindowFromPoint(POINT(cx, cy)) != self._hwnd:
            user32.SetWindowPos(
                self._hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)

    def _compute_placement(self) -> Optional[dict]:
        shell = user32.FindWindowW("Shell_TrayWnd", None)
        if not shell or not user32.IsWindowVisible(shell):
            self._none_reason = "找不到任务栏窗口"
            return None
        left, top, right, bottom = _rect(shell)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None
        horizontal = width >= height
        thickness = height if horizontal else width
        if thickness < 24:
            self._none_reason = f"任务栏厚度异常 {thickness}px"
            return None

        monitor = user32.MonitorFromWindow(shell, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            mon = info.rcMonitor
            inter_w = max(0, min(right, mon.right) - max(left, mon.left))
            inter_h = max(0, min(bottom, mon.bottom) - max(top, mon.top))
            if horizontal and inter_h < thickness * 0.6:
                self._none_reason = f"任务栏已自动隐藏（可见高 {inter_h}px）"
                return None
            if not horizontal and inter_w < thickness * 0.6:
                self._none_reason = f"任务栏已自动隐藏（可见宽 {inter_w}px）"
                return None

        if _fullscreen_foreground():
            self._none_reason = "前台是全屏应用"
            return None

        tray = user32.FindWindowExW(shell, None, "TrayNotifyWnd", None)
        if tray:
            t_left, t_top = _rect(tray)[0], _rect(tray)[1]
        else:
            t_left, t_top = right, top

        line_px = max(11, round(thickness * FONT_RATIO))
        with self._lock:
            view = self._view
        image = self._image if self._line_px == line_px and self._image else None
        if image is None:
            image = render_text(view, self._palette, line_px)

        if horizontal:
            x = t_left - GAP_TO_TRAY - image.width
            y = top + (height - image.height) // 2
            found: list[int] = []

            def cb(h, _):
                if w32.get_window_class(h) == "MSTaskSwWClass":
                    found.append(h)
                return True

            user32.EnumChildWindows(shell, w32.WNDENUMPROC(cb), 0)
            if found:
                limit = _rect(found[0])[2] + GAP_TO_TASKLIST
                if x < limit:
                    x = limit
                    if x + image.width > t_left - 4:
                        self._none_reason = "任务栏剩余空间不足"
                        return None
        else:
            x = left + (width - image.width) // 2
            y = t_top - GAP_TO_TRAY - image.height
        return {"x": x, "y": y, "thickness": thickness, "horizontal": horizontal}

    def _show(self) -> None:
        if not self._shown:
            user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
            self._shown = True

    def _hide(self, reason: str = "") -> None:
        self._hide_tip()
        if self._shown:
            user32.ShowWindow(self._hwnd, SW_HIDE)
            self._shown = False
            if self._log:
                self._log.info("文字窗口隐藏：%s", reason or "未知")

    # ---------------------------------------------------------- 悬停卡片
    def _show_tip(self) -> None:
        if not self._tip_hwnd or not self._image or not self._shown:
            return
        with self._lock:
            view = self._view
        base_px = max(13, round(self._line_px * CARD_FONT_RATIO))
        card = render_tip_card(view, self._palette, base_px)

        shell = user32.FindWindowW("Shell_TrayWnd", None)
        shell_top = _rect(shell)[1] if shell else self._pos[1]
        x = self._pos[0] + self._image.width - card.width
        y = shell_top - card.height - 8

        monitor = user32.MonitorFromWindow(self._hwnd, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            mon = info.rcMonitor
            x = max(mon.left + 4, min(x, mon.right - card.width - 4))
            # 上方放不下就整体贴到屏幕顶，绝不被任务栏压住
            y = max(mon.top + 4, y)
            if y + card.height > shell_top - 4:
                y = max(mon.top + 4, shell_top - card.height - 4)
        if not apply_layered(self._tip_hwnd, card, x, y):
            return
        user32.ShowWindow(self._tip_hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(self._tip_hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        self._tip_visible = True

    def _hide_tip(self) -> None:
        if self._tip_hwnd and self._tip_visible:
            user32.ShowWindow(self._tip_hwnd, SW_HIDE)
            self._tip_visible = False
