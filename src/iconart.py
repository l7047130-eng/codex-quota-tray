# -*- coding: utf-8 -*-
"""托盘图标绘制：把「剩余百分比 + 额度类型」画成 16px 级别的小图标。

16×16 是 100% 缩放下任务栏的真实图标尺寸，设计取舍：
  * 数字最大最粗，保证一眼可读；
  * 底部深色条放「5h / 周」，这是唯一能给标签留出的空间；
  * 两种额度用形状区分（5h = 圆角方，周 = 圆形），即使标签糊了也能分辨；
  * 底色随余量变化（绿 / 琥珀 / 红 / 灰），显示已用尽或不可用。

另一部分是**程序图标**（exe / 桌面快捷方式用）：六边形环 + 内部液位，
单色、无底板，六边形取自 Codex 标志中心的孔形，液位高低表示剩余额度。
"""
from __future__ import annotations

import io
import struct
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

FONT_CJK_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_CJK = r"C:\Windows\Fonts\msyh.ttc"
FONT_LATIN_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"
FONT_LATIN = r"C:\Windows\Fonts\segoeui.ttf"

COLOR_HEALTHY = (46, 125, 79)
COLOR_WARN = (176, 122, 20)
COLOR_CRITICAL = (176, 56, 56)
COLOR_UNAVAILABLE = (108, 116, 126)

SS = 4  # 超采样倍数


def _load_font(path: str, size: float) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, max(4, int(round(size))))
    except OSError:
        return ImageFont.load_default()


def _fit_font(candidates: tuple[str, ...], text: str, max_w: float, max_h: float,
              start: float) -> ImageFont.FreeTypeFont:
    size = start
    while size > 3:
        for path in candidates:
            try:
                font = _load_font(path, size)
            except Exception:
                continue
            left, top, right, bottom = font.getbbox(text)
            if (right - left) <= max_w and (bottom - top) <= max_h:
                return font
        size -= 0.5
    return _load_font(candidates[0], 4)


def _draw_centered(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
                   text: str, font: ImageFont.FreeTypeFont, fill) -> None:
    x0, y0, x1, y1 = box
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    w, h = right - left, bottom - top
    draw.text((x0 + (x1 - x0 - w) / 2 - left, y0 + (y1 - y0 - h) / 2 - top),
              text, font=font, fill=fill)


def level_color(remaining: int | None) -> tuple[int, int, int]:
    if remaining is None:
        return COLOR_UNAVAILABLE
    if remaining <= 15:
        return COLOR_CRITICAL
    if remaining <= 40:
        return COLOR_WARN
    return COLOR_HEALTHY


def render(bucket: str, remaining: int | None, size: int) -> Image.Image:
    """bucket: 'five_hour' | 'weekly'"""
    weekly = bucket == "weekly"
    label = "周" if weekly else "5h"
    text = "—" if remaining is None else str(int(remaining))
    color = level_color(remaining)

    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if weekly:
        draw.ellipse([0, 0, S - 1, S - 1], fill=color + (255,))
    else:
        draw.rounded_rectangle([0, 0, S - 1, S - 1], radius=round(S * 0.20),
                               fill=color + (255,))

    bar_h = max(2, round(S * 0.34))
    bar_top = S - bar_h
    overlay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle([0, bar_top, S - 1, S - 1], fill=(0, 0, 0, 105))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # 数字区：能用多大多大，3 位数自动缩小到宽度以内
    inset = round(S * 0.02)
    f_num = _fit_font((FONT_LATIN_BOLD, FONT_LATIN, FONT_CJK_BOLD), text,
                      S - 2 * inset, bar_top * 0.98, S * 0.62)
    _draw_centered(draw, (inset, 0, S - inset, bar_top), text, f_num,
                   (255, 255, 255, 255))

    # 标签区：字号略大于底栏高度，靠裁切保留笔画，比小字号更清楚
    label_path = FONT_CJK_BOLD if weekly else FONT_LATIN_BOLD
    f_lbl = _fit_font((label_path,), label, S * 0.96, S * 4, S * 0.52)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _draw_centered(ImageDraw.Draw(layer), (0, bar_top, S, S), label, f_lbl,
                   (255, 255, 255, 255))
    img.alpha_composite(layer.crop((0, bar_top, S, S)), (0, bar_top))

    return img.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=64)
def tray_icon_bytes(bucket: str, remaining: int | None, size: int) -> bytes:
    """渲染并编码成 ICO 字节（带缓存，避免每 15 秒重复绘制）。"""
    image = render(bucket, remaining, size)
    buf = io.BytesIO()
    image.save(buf, format="ICO", sizes=[(size, size)])
    return buf.getvalue()


# ---------------------------------------------------------------- 程序图标
APP_ICON_CANVAS = 1024
APP_ICON_INK = (0, 0, 0, 255)

# 小尺寸笔画补偿：降采样后笔画会变细，按尺寸适度加粗，保证 16px 仍分得清
APP_ICON_WEIGHTS = {
    16: 1.68, 20: 1.60, 24: 1.45, 32: 1.30,
    40: 1.20, 48: 1.16, 64: 1.10, 128: 1.03, 256: 1.00,
}


def _hex_points(cx: float, cy: float, radius: float, rotation: float = -90.0):
    import math

    return [(cx + radius * math.cos(math.radians(rotation + i * 60)),
             cy + radius * math.sin(math.radians(rotation + i * 60)))
            for i in range(6)]


def _app_icon_master(weight: float) -> Image.Image:
    """在 1024 画布上画六边形环 + 液位。weight 用来按目标尺寸补偿笔画。"""
    S = APP_ICON_CANVAS
    c = S / 2
    ink = APP_ICON_INK
    stroke = 86 * weight
    r_out = 442
    r_in = r_out - stroke

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 环：外多边形减去内多边形，边界精确（描边中心线会让内外比例失真）
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(ring).polygon(_hex_points(c, c, r_out), fill=ink)
    hole = Image.new("L", (S, S), 0)
    ImageDraw.Draw(hole).polygon(_hex_points(c, c, r_in), fill=255)
    ring.putalpha(Image.composite(Image.new("L", (S, S), 0),
                                  ring.getchannel("A"), hole))
    img = Image.alpha_composite(img, ring)

    # 液位：内六边形裁出的矩形，顶边平直；与环之间留一道细缝
    fill_r = r_in - 14 * weight
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(_hex_points(c, c, fill_r), fill=255)
    fill = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    top = c + fill_r * 0.04          # 液面略高于中线
    ImageDraw.Draw(fill).rectangle([0, top, S, S], fill=ink)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    layer.paste(fill, (0, 0), Image.composite(fill.getchannel("A"),
                                             Image.new("L", (S, S), 0), mask))
    return Image.alpha_composite(img, layer)


def app_icon_image(size: int = 256) -> Image.Image:
    """程序图标：六边形环 + 液位，单色无底板。"""
    weight = APP_ICON_WEIGHTS.get(size, 1.0 if size >= 64 else 1.16)
    master = _app_icon_master(weight)
    return master.resize((size, size), Image.LANCZOS)


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_app_ico(path: str) -> None:
    """写多尺寸 ICO。每个尺寸单独渲染（各自笔画补偿），不用一张图缩放。

    ICO 允许每个尺寸用 PNG 压缩存放（Vista 起支持），这里直接按规范写，
    避免 Pillow 的 sizes= 只能缩放同一张图。
    """
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    blobs = [(size, _png_bytes(app_icon_image(size))) for size in sizes]

    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for size, blob in blobs:
        entries += struct.pack("<BBBBHHII",
                               0 if size >= 256 else size,
                               0 if size >= 256 else size,
                               0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, "wb") as handle:
        handle.write(header + entries + b"".join(blob for _s, blob in blobs))
