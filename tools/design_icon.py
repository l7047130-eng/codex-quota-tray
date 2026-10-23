# -*- coding: utf-8 -*-
"""图标设计稿 v5：不用 Q 形。

三个方向，都保持 Codex 的视觉语言：单色、纯线条、无底板、六重对称。

  A  分段环：外圈 6 段（顶端一段更短）+ 中心小六边形
  B  六边形刻度：粗六边形轮廓，内部从底部按比例填充
  C  三叶环：三段圆弧风车式排布，读作「表盘 / 余量」

只输出 docs/images/icon-proposal.png（设计记录），不覆盖 assets/app.ico。
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFont

SS = 1024
C = SS / 2
INK = (0, 0, 0, 255)


# ------------------------------------------------------------------ 基础图形
def annulus(draw, r_out, r_in, a0, a1, fill=INK, steps=160):
    """解析式圆环扇区：外弧 + 内弧闭合，边缘干净，无 PIL 弧线接缝。"""
    outer, inner = [], []
    for i in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * i / steps)
        outer.append((C + r_out * math.cos(a), C + r_out * math.sin(a)))
        inner.append((C + r_in * math.cos(a), C + r_in * math.sin(a)))
    draw.polygon(outer + inner[::-1], fill=fill)


def hex_pts(radius, rot_deg=-90.0):
    return [(C + radius * math.cos(math.radians(rot_deg + i * 60)),
             C + radius * math.sin(math.radians(rot_deg + i * 60))) for i in range(6)]


def hexagon_ring(draw, radius, stroke, fill=INK):
    pts = hex_pts(radius)
    # 末尾多补两点，让起点/终点相接处的圆角真正闭合（只补一个点会在顶点留缺口）
    draw.line(pts + [pts[0], pts[1]], fill=fill, width=int(stroke), joint="curve")


# ------------------------------------------------------------------ 方案 A
def variant_ring(weight=1.0):
    """外圈 6 段，顶端一段短一截；中心小六边形。"""
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stroke = 62 * weight
    r_out = 470
    r_in = r_out - stroke
    gap = 9
    for i in range(6):
        a0 = -90 + i * 60 + gap / 2
        span = 60 - gap
        if i == 0:               # 顶端那段只走一半，像余量刻度
            span = (60 - gap) * 0.42
        annulus(d, r_out, r_in, a0, a0 + span)
    hexagon_ring(d, 205, 54 * weight)
    return img


# ------------------------------------------------------------------ 方案 B
def variant_fill(weight=1.0):
    """六边形环 + 内部按比例填充（液位）。

    环用「外多边形减内多边形」画，边界精确；填充与环之间留一道细缝，
    小尺寸下会自然并成一块，不会糊。
    """
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    stroke = 86 * weight
    r_out = 442
    r_in = r_out - stroke

    ring = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    ImageDraw.Draw(ring).polygon(hex_pts(r_out), fill=INK)
    hole = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(hole).polygon(hex_pts(r_in), fill=255)
    ring.putalpha(Image.composite(Image.new("L", (SS, SS), 0),
                                  ring.getchannel("A"), hole))
    img = Image.alpha_composite(img, ring)

    fill_r = r_in - 14 * weight
    mask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(mask).polygon(hex_pts(fill_r), fill=255)
    fill = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    top = C + fill_r * 0.04          # 液面位置：略高于中线
    ImageDraw.Draw(fill).rectangle([0, top, SS, SS], fill=INK)
    layer = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    layer.paste(fill, (0, 0), Image.composite(fill.getchannel("A"),
                                             Image.new("L", (SS, SS), 0), mask))
    return Image.alpha_composite(img, layer)


# ------------------------------------------------------------------ 方案 C
def variant_pinwheel(weight=1.0):
    """三段 78° 圆弧风车排布，缺口朝三个方向，读作表盘/进度。"""
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stroke = 96 * weight
    r_out = 446
    r_in = r_out - stroke
    for i in range(3):
        a0 = -90 + i * 120 + 21
        annulus(d, r_out, r_in, a0, a0 + 78)
    hexagon_ring(d, 168, 52 * weight)
    return img


VARIANTS = {
    "B  六边形液位（推荐）": variant_fill,
    "A  分段环": variant_ring,
    "C  三叶环": variant_pinwheel,
}

# 小尺寸补偿：笔画按比例加粗，避免降采样后糊成一团
WEIGHTS = {256: 1.0, 96: 1.06, 64: 1.1, 48: 1.16, 32: 1.3, 24: 1.45, 16: 1.68}


def render(kind: str, size: int) -> Image.Image:
    weight = WEIGHTS.get(size, 1.2 if size < 24 else 1.0)
    return VARIANTS[kind](weight).resize((size, size), Image.LANCZOS)


def tint(img, rgb):
    out = img.copy()
    px = out.load()
    w, h = out.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a:
                px[x, y] = (*rgb, a)
    return out


def build_sheet(out_path="docs/images/icon-proposal.png"):
    names = list(VARIANTS)
    pad, col_w = 28, 300
    W = pad * 2 + col_w * len(names)
    block_h = 412
    scene_h = 210
    H = 96 + block_h * 2 + 12 + scene_h
    sheet = Image.new("RGB", (W, H), (247, 248, 250))
    d = ImageDraw.Draw(sheet)
    try:
        title = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 18)
        body = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 12)
    except OSError:
        title = body = ImageFont.load_default()

    d.text((pad, 14), "图标提案 v2（不用 Q 形）", font=title, fill=(24, 24, 26))
    d.text((pad, 42), "延续 Codex 的单色线稿语言：无底板、六重对称、中心六边形孔；用六边形填充 / 环的缺口 / 分段表达额度。",
           font=body, fill=(110, 110, 116))
    d.text((pad, 64), "均为设计稿，尚未替换 assets/app.ico。", font=body, fill=(150, 150, 156))

    y = 96
    for cap, bg, fg, capfg in (
        ("浅色背景", (255, 255, 255), (22, 22, 24), (120, 120, 126)),
        ("深色背景", (30, 30, 32), (242, 242, 244), (170, 170, 176)),
    ):
        d.rectangle([0, y, W, y + block_h], fill=bg)
        d.text((pad, y + 10), cap, font=body, fill=capfg)
        for i, n in enumerate(names):
            x = pad + i * col_w
            big = tint(render(n, 256), fg)
            sheet.paste(big, (x + 10, y + 32), big)
            d.text((x + 10, y + 298), n, font=title,
                   fill=(28, 28, 30) if bg[0] > 128 else (238, 238, 240))
            sx = x + 10
            for size in (48, 32, 24, 16):
                sm = tint(render(n, size), fg)
                sheet.paste(sm, (sx, y + 386 - size), sm)
                sx += size + 12
        y += block_h + 12

    d.text((pad, y + 2), "实际场景：任务栏 28px / 桌面快捷方式 48px",
           font=body, fill=(120, 120, 126))
    sy = y + 26
    for i, n in enumerate(names):
        x = pad + i * col_w
        seg = Image.new("RGB", (col_w - 16, 50), (250, 250, 251))
        sd = ImageDraw.Draw(seg)
        sd.rounded_rectangle([0, 0, seg.width - 1, 49], radius=8, fill=(250, 250, 251))
        sd.text((12, 17), "5h 98%", font=body, fill=(30, 30, 32))
        ic = tint(render(n, 28), (22, 22, 24))
        seg.paste(ic, (seg.width - 40, 11), ic)
        sheet.paste(seg, (x, sy))

        sc = Image.new("RGB", (col_w - 16, 76), (243, 244, 246))
        cd = ImageDraw.Draw(sc)
        ic48 = tint(render(n, 48), (22, 22, 24))
        sc.paste(ic48, (10, 6), ic48)
        cd.text((70, 15), "Codex 额度", font=title, fill=(30, 30, 32))
        cd.text((70, 41), "桌面快捷方式", font=body, fill=(130, 130, 136))
        sheet.paste(sc, (x, sy + 56))

    sheet.save(out_path)
    return sheet


if __name__ == "__main__":
    os.makedirs("docs/images", exist_ok=True)
    sheet = build_sheet()
    print("设计稿已输出 docs/images/icon-proposal.png", sheet.size)
