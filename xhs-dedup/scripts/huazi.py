# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "pillow-heif"]
# ///
"""小红书花字：把一段文字画成和画面一样大的透明图层（RGBA），给图片、封面叠字用。

只用 Pillow。
位置、字号、描边都按画面宽高的比例算，同一组参数画在不同尺寸的图上看起来一样。

样式照小红书上真实的字来：对标博主封面（二胖胖小厨娘那 20 张），和 2026-10-16 搜「live图加字」
「醒图花字」「花字教程」「手帐花字」各 20 篇的封面。用户挑过一轮：留 soft、note、big，
其余的（红边大字报、色块标签、斜标题等）嫌「太直男、太生硬」删掉了。

写法：
    「|」换行；【】里的字用强调色（【】本身不画出来）。
    emoji 用系统的 Apple Color Emoji 画（彩色、不描边）；几个 emoji 拼成的组合表情会拆开画。
    英文、数字自动用样式里的英文字体，中文用中文字体。
"""

from __future__ import annotations

import datetime as _dt
import glob
import math
import os
import random
import re

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

XHS_RED = "#FF2442"
SOFT_SHADOW = (0.02, 0.04, 0.10, (0, 0, 0, 120))   # (横移, 下移, 模糊, 颜色)，前三个是字号的倍数
THIN_EDGE = [(0.035, "#00000040")]                 # 很淡的细边，浅色背景上白字也看得清
INK_DARK = "#2A2522"                               # fill="auto" 在浅色背景上用的深色

# 样式。字段都可省：
#   desc 一句话说明（命令行帮助里显示）
#   font 中文字体，latin 英文字体（不写就跟中文一样）
#   fill 字色（auto：看背景，浅底深字、深底白字），emph 强调色，strokes 由外到内的描边 [(宽度, 颜色)]，shadow 投影
#   size 字号（画面宽的比例），pos 位置，align 对齐（left/center/right），tilt 倾斜角度（正数往右上翘）
#   stair 每行往右错开，tracking 字距，gap 行距（都是字号的倍数），sparkle 在字块两角点 ✨
#   card 整块底（color/radius/pad_x/pad_y，tail 消息气泡的小尾巴，torn 胶带撕边）
#   arrow 画一根手绘箭头指向 --point，arc 沿弧线排（半径是画面宽的倍数）
#   accent --color 没给时的强调色；{accent} 会换成它
STYLES = {
    "soft": {
        "desc": "随手白字，左上角，细淡描边+柔和投影（像「夯爆了～好吃到跺脚🥹」）；--font 可换圆体、翩翩体、手札体、报隶、行楷",
        "font": "pingfang-m", "fill": "#FFFFFF", "emph": "#FFE58F",
        "strokes": THIN_EDGE, "shadow": SOFT_SHADOW,
        "size": 0.058, "pos": "0.13", "align": "left", "gap": 0.32,
    },
    "note": {
        "desc": "手写便签：翩翩体 + Noteworthy 英文，白字淡投影，斜一点，两行错开",
        "font": "hanzipen", "latin": "noteworthy", "fill": "#FFFFFF", "emph": "#FFE58F",
        "strokes": THIN_EDGE, "shadow": SOFT_SHADOW,
        "size": 0.065, "pos": "0.15", "align": "left", "tilt": 5, "stair": 0.5, "gap": 0.25,
    },
    "big": {
        "desc": "大字报：黄字粗黑边 + 硬投影",
        "font": "pingfang", "fill": "#FFE14D", "emph": "#FFFFFF",
        "strokes": [(0.16, "#111111")], "shadow": (0.07, 0.08, 0.0, (17, 17, 17, 255)),
        "size": 0.10, "pos": "top",
    },
    "ink": {
        "desc": "手写墨水字：手札体，浅底黑字、深底白字，不描边，放右下（像直接拿笔写在照片上）",
        "font": "hannotate", "latin": "noteworthy", "fill": "auto", "emph": "auto",
        "strokes": [], "shadow": SOFT_SHADOW,
        "size": 0.052, "pos": "0.8", "align": "right", "tilt": -3, "gap": 0.35,
    },
    "film": {
        "desc": "电影字幕：一行细字，字距拉开，画面中间偏下，浅底深字、深底白字（像「我们总是擅长忘记快乐」）",
        "font": "pingfang-l", "latin": "baskerville-i", "fill": "auto", "emph": "auto",
        "strokes": [(0.04, "#00000040")], "shadow": (0.0, 0.03, 0.12, (0, 0, 0, 150)),
        "size": 0.04, "tracking": 0.12, "pos": "0.64", "gap": 0.6,
    },
    "serif": {
        "desc": "杂志英文小字：衬线体，字距宽，几行居中，浅底深字、深底白字（适合英文短句）",
        "font": "pingfang-l", "latin": "baskerville", "fill": "auto", "emph": "auto",
        "strokes": [], "shadow": (0.0, 0.02, 0.10, (0, 0, 0, 110)),
        "size": 0.034, "tracking": 0.10, "pos": "0.1", "gap": 0.55,
    },
    "script": {
        "desc": "英文花体诗句：Snell 花体 + 翩翩体中文，白字柔和投影（像「Time slows down…」）",
        "font": "hanzipen", "latin": "snell", "fill": "#FFFFFF", "emph": "#FFE58F",
        "strokes": THIN_EDGE, "shadow": SOFT_SHADOW,
        "size": 0.075, "pos": "0.14", "align": "left", "tilt": 4, "gap": 0.05,
    },
    "arrow": {
        "desc": "手写批注+箭头：手札体小字，画一根手绘弯箭头指向产品（--point 指哪，默认画面中间）",
        "font": "hannotate", "latin": "noteworthy", "fill": "auto", "emph": "auto",
        "strokes": [], "shadow": SOFT_SHADOW, "arrow": True,
        "size": 0.045, "pos": "0.2", "align": "right", "gap": 0.3,
    },
    "bubble": {
        "desc": "消息卡片：白色圆角小卡片带小尾巴，深色小字（像备忘录、聊天气泡）",
        "font": "pingfang-m", "fill": "#222222", "emph": "#FF7A45",
        "strokes": [], "shadow": (0.0, 0.05, 0.25, (0, 0, 0, 70)),
        "card": {"color": "{accent}", "radius": 0.55, "pad_x": 0.65, "pad_y": 0.42, "tail": True},
        "accent": "#FFFFFFEB", "size": 0.038, "pos": "0.12", "align": "left", "gap": 0.3,
    },
    "tape": {
        "desc": "手帐胶带：牛皮纸色胶带条，撕边，深棕手写字，斜贴（--color 换胶带颜色，比如 #F7C9D4E6 粉）",
        "font": "hanzipen", "latin": "noteworthy", "fill": "#4A3426", "emph": "#C2410C",
        "strokes": [], "shadow": (0.0, 0.03, 0.10, (0, 0, 0, 70)),
        "card": {"color": "{accent}", "radius": 0, "pad_x": 0.75, "pad_y": 0.32, "torn": True},
        "accent": "#EAD3A6E6", "size": 0.05, "pos": "0.16", "align": "left", "tilt": -5, "gap": 0.3,
    },
    "arc": {
        "desc": "弧形英文：字母沿弧线拱在产品上方，白字字距宽（像「Coffee」「Twinkle」，只排一行）",
        "font": "pingfang", "latin": "avenir", "fill": "#FFFFFF", "emph": "#FFE58F",
        "strokes": [(0.05, "#00000050")], "shadow": SOFT_SHADOW,
        "size": 0.075, "tracking": 0.14, "pos": "0.2", "arc": 0.34,
    },
    "stamp": {
        "desc": "胶片日期戳：右下角橙色发光等宽字，不给字就写今天日期（像老相机打的 '26 10 16）",
        "font": "pingfang", "latin": "menlo", "fill": "{accent}", "emph": "{accent}",
        "strokes": [], "shadow": (0.0, 0.0, 0.22, (255, 110, 20, 230)),
        "accent": "#FF9A3C", "size": 0.042, "tracking": 0.06, "pos": "0.93", "align": "right",
    },
    # 下面 4 种是底色字（参考图在 references/底色字参考/）
    "shade": {
        "desc": "半透明黑底：几行白字靠左，整块垫一层半透明黑色直角底（像「适合食补的印度姜黄粉…」）",
        "font": "pingfang-m", "fill": "#FFFFFF", "emph": "#FFE58F",
        "strokes": [], "shadow": None,
        "card": {"color": "{accent}", "radius": 0, "pad_x": 0.35, "pad_y": 0.35},
        "accent": "#000000A6", "size": 0.05, "pos": "0.45", "align": "left", "gap": 0.42,
    },
    "label": {
        "desc": "白底条黑字：每行一条白色直角底条，黑色粗字居中，可带 ⚠️❌ 这类 emoji（像「⚠️一定要选未碱化可可粉」）",
        "font": "pingfang", "fill": "#111111", "emph": "#E03E2D",
        "strokes": [], "shadow": None,
        "card": {"color": "{accent}", "radius": 0, "pad_x": 0.28, "pad_y": 0.16, "per_line": True},
        "accent": "#FFFFFFF2", "size": 0.05, "pos": "0.14", "align": "center", "gap": 0.62,
    },
    "song": {
        "desc": "白纱底宋体：每行一条半透明白底，黑色宋体字（像「取姜黄粉、肉桂粉各一小勺」）",
        "font": "songti", "fill": "#111111", "emph": "#8A3B12",
        "strokes": [], "shadow": None,
        "card": {"color": "{accent}", "radius": 0, "pad_x": 0.3, "pad_y": 0.2, "per_line": True},
        "accent": "#FFFFFFB8", "size": 0.052, "pos": "0.2", "align": "center", "gap": 0.75,
    },
    "ochre": {
        "desc": "棕黄底白字：每行一条棕黄色直角底，白字（像「品牌名+产品名」；某类产品可指定一半的图用它）",
        "font": "pingfang-m", "fill": "#FFFFFF", "emph": "#FFF1C2",
        "strokes": [], "shadow": None,
        "card": {"color": "{accent}", "radius": 0, "pad_x": 0.3, "pad_y": 0.18, "per_line": True},
        "accent": "#B58A43F2", "size": 0.052, "pos": "0.8", "align": "right", "gap": 0.5,
    },
}
DEFAULTS = {"size": 0.07, "pos": "top", "align": "center", "tilt": 0.0, "stair": 0.0, "tracking": 0.0,
            "sparkle": False, "gap": 0.25}

# 字体名 → (文件名, 字体族开头, 粗细优先顺序)。系统字体和「字体册」下载的字体都找。
FONTS = {
    "pingfang": (["PingFang.ttc", "PingFangUI.ttc"], "PingFang SC", ["Semibold", "Medium"]),
    "pingfang-m": (["PingFang.ttc", "PingFangUI.ttc"], "PingFang SC", ["Medium", "Semibold"]),
    "pingfang-l": (["PingFang.ttc", "PingFangUI.ttc"], "PingFang SC", ["Light", "Regular"]),
    "yuanti": (["Yuanti.ttc"], "Yuanti SC", ["Bold", "Regular"]),
    "hanzipen": (["Hanzipen.ttc"], "HanziPen SC", ["Bold", "Regular"]),
    "hannotate": (["Hannotate.ttc"], "Hannotate SC", ["Bold", "Regular"]),
    "baoli": (["Baoli.ttc"], "Baoli SC", ["Regular"]),
    "libian": (["Libian.ttc"], "Libian SC", ["Regular"]),
    "xingkai": (["Xingkai.ttc"], "Xingkai SC", ["Bold", "Light"]),
    "kaiti": (["Kaiti.ttc"], "Kaiti SC", ["Bold", "Regular"]),
    "songti": (["Songti.ttc"], "Songti SC", ["Regular", "Bold"]),
    "hiragino": (["Hiragino Sans GB.ttc"], "Hiragino Sans GB", ["W6", "W3"]),
    "hiragino-l": (["Hiragino Sans GB.ttc"], "Hiragino Sans GB", ["W3"]),
    "heiti": (["STHeiti Medium.ttc"], "Heiti SC", ["Medium"]),
    # 英文
    "avenir-hi": (["Avenir Next.ttc"], "Avenir Next", ["Heavy Italic", "Bold Italic"]),
    "avenir": (["Avenir Next.ttc"], "Avenir Next", ["Demi Bold", "Bold"]),
    "rounded": (["Arial Rounded Bold.ttf"], "Arial Rounded", ["Regular"]),
    "georgia": (["Georgia Bold.ttf"], "Georgia", ["Bold"]),
    "baskerville": (["Baskerville.ttc"], "Baskerville", ["SemiBold", "Regular"]),
    "baskerville-i": (["Baskerville.ttc"], "Baskerville", ["SemiBold Italic", "Italic"]),
    "typewriter": (["AmericanTypewriter.ttc"], "American Typewriter", ["Regular"]),
    "noteworthy": (["Noteworthy.ttc"], "Noteworthy", ["Bold", "Light"]),
    "snell": (["SnellRoundhand.ttc"], "Snell Roundhand", ["Bold", "Black"]),
    "menlo": (["Menlo.ttc"], "Menlo", ["Bold"]),
}
FONT_FALLBACK = {"pingfang-l": ["hiragino-l"], "pingfang-m": ["hiragino"], "pingfang": ["hiragino"]}
FONT_DIRS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/System/Library/PrivateFrameworks/FontServices.framework/Versions/A/Resources/Reserved",
    "/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
]
FALLBACK = ["pingfang", "hiragino", "heiti"]
LATIN_FALLBACK = ["avenir", "rounded"]

EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
_EMOJI_RANGES = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF)]
_EMOJI_SINGLES = [0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299]
EMOJI = re.compile("([" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _EMOJI_RANGES)
                   + "".join(chr(c) for c in _EMOJI_SINGLES) + "]+)")
INVISIBLE = re.compile("[" + chr(0xFE0E) + chr(0xFE0F) + chr(0x200D) + chr(0x20E3)
                       + chr(0xE0000) + "-" + chr(0xE007F) + "]")
LATIN = re.compile("([" + chr(0x20) + "-" + chr(0x7E) + chr(0xC0) + "-" + chr(0x24F)
                   + chr(0x2018) + "-" + chr(0x201D) + "]+)")
_emoji_cache: dict = {}
_font_cache: dict = {}


def emoji_image(ch: str, px: int):
    """一个 emoji 画成约 px 大的彩色小图。苹果的 emoji 字体只有固定几个尺寸，先按 160 画再缩。"""
    key = (ch, px)
    if key not in _emoji_cache:
        im = None
        try:
            f = ImageFont.truetype(EMOJI_FONT, 160)
            im = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((10, 10), ch, font=f, embedded_color=True)
            bb = im.getbbox()
            im = im.crop(bb) if bb else None
        except OSError:
            im = None
        if im is not None:
            s = px * 0.92 / max(im.size)
            im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)
        _emoji_cache[key] = im
    return _emoji_cache[key]


def _faces(path: str):
    i = 0
    while True:
        try:
            f = ImageFont.truetype(path, 20, index=i)
        except OSError:
            return
        yield i, f.getname()
        i += 1


def resolve_font(name: str, fallback: list[str] = FALLBACK) -> tuple[str, int, str]:
    """返回 (文件路径, ttc 里第几个, 说明)。name 可以是 FONTS 里的名字，也可以是 .ttf/.ttc/.otf 文件。"""
    if name in _font_cache:
        return _font_cache[name]
    if os.path.isfile(os.path.expanduser(name)):
        p = os.path.expanduser(name)
        return p, 0, os.path.basename(p)
    chain = [name] + FONT_FALLBACK.get(name, []) + [k for k in fallback if k != name]
    for key in chain:
        if key not in FONTS:
            continue
        files, family, weights = FONTS[key]
        for fn in files:
            for d in FONT_DIRS:
                for p in glob.glob(os.path.join(d, fn)):
                    faces = list(_faces(p))
                    for w in weights:
                        for i, (fam, sty) in faces:
                            if fam.lstrip(".").startswith(family) and sty == w:
                                res = (p, i, f"{family} {w}" + ("" if key == name else f"（没有 {name}，改用）"))
                                _font_cache[name] = res
                                return res
    raise RuntimeError(f"找不到字体 {name}。可以在「字体册」里下载，或用 --font 给 .ttf/.ttc/.otf 文件")


def _font(name: str, px: int, latin: bool = False):
    path, idx, _ = resolve_font(name, LATIN_FALLBACK if latin else FALLBACK)
    return ImageFont.truetype(path, px, index=idx)


def _rgba(c, accent: str):
    if isinstance(c, tuple):
        return c
    c = c.replace("{accent}", accent)
    if len(c) == 9 and c.startswith("#"):  # #RRGGBBAA
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5, 7))
    return ImageColor.getrgb(c)[:3] + (255,)


def parse(text: str) -> list[list[tuple[str, str]]]:
    """拆成行，每行是 [(文字, 种类)]，种类：n 普通、e 强调、emoji。"""
    lines = []
    for raw in INVISIBLE.sub("", text).replace("\\n", "\n").replace("|", "\n").split("\n"):
        runs, emph = [], False
        for part in re.split(r"([【】])", raw):
            if part == "【":
                emph = True
            elif part == "】":
                emph = False
            elif part:
                for i, seg in enumerate(EMOJI.split(part)):
                    if not seg:
                        continue
                    if i % 2:
                        runs += [(ch, "emoji") for ch in seg]
                    else:
                        runs.append((seg, "e" if emph else "n"))
        lines.append(runs or [("", "n")])
    return lines


class Line:
    """一行字：拆成一段段（中文 / 英文 / emoji），各段用各自的字体，基线对齐。strokes、tracking 都是像素。"""

    def __init__(self, runs, px, cjk_font, latin_font, fill, emph, strokes, tracking=0):
        self.px, self.fill, self.emph, self.strokes, self.tracking = px, fill, emph, strokes, tracking
        self.segs = []  # (文字, 种类, 字体或 None)
        fc = _font(cjk_font, px)
        fl = _font(latin_font, px, latin=True) if latin_font else fc
        for t, k in runs:
            if k == "emoji":
                self.segs.append((t, k, None))
                continue
            for i, part in enumerate(LATIN.split(t)):
                if part:
                    self.segs.append((part, k, fl if i % 2 else fc))
        fonts = [f for _, _, f in self.segs if f] or [fc]
        self.asc = max(f.getmetrics()[0] for f in fonts)
        self.desc = max(f.getmetrics()[1] for f in fonts)
        self.h = self.asc + self.desc
        self.smax = strokes[0][0] if strokes else 0
        self.w = sum(self.seg_w(s) for s in self.segs) - (tracking if self.segs else 0)

    def seg_w(self, seg) -> float:
        t, k, f = seg
        if k == "emoji":
            return self.px * 1.08 + self.tracking
        return f.getlength(t) + self.tracking * len(t)

    def draw(self, canvas: Image.Image, x: float, top: float, layer) -> None:
        d = ImageDraw.Draw(canvas)
        cx = x
        for seg in self.segs:
            t, k, f = seg
            if k == "emoji":
                if layer == "fill":
                    em = emoji_image(t, self.px)
                    if em is not None:
                        canvas.alpha_composite(em, (round(cx + (self.px - em.width) / 2),
                                                    round(top + (self.h - em.height) / 2)))
                cx += self.seg_w(seg)
                continue
            y = top + self.asc - f.getmetrics()[0]  # 基线对齐
            pieces = list(t) if self.tracking else [t]
            for piece in pieces:
                if layer == "fill":
                    d.text((cx, y), piece, font=f, fill=self.emph if k == "e" else self.fill)
                else:
                    s, c = layer
                    d.text((cx, y), piece, font=f, fill=c, stroke_width=s, stroke_fill=c)
                cx += f.getlength(piece) + self.tracking


def _sparkle(canvas: Image.Image, x: float, y: float, px: int) -> None:
    em = emoji_image("✨", px)
    if em is not None:
        canvas.alpha_composite(em, (max(0, round(x)), max(0, round(y))))


def _anchor(pos: str, H: int) -> float:
    try:
        return float(pos) * H
    except ValueError:
        return {"top": 0.17, "center": 0.5, "bottom": 0.83}[pos] * H


def _lum(bg: Image.Image | None, W: int, H: int, x0: float, y0: float, x1: float, y1: float) -> float | None:
    """底图在 (x0,y0)-(x1,y1)（图层坐标）这块的平均亮度，0–1。"""
    if bg is None:
        return None
    sx, sy = bg.width / W, bg.height / H
    b = (max(0, x0 * sx), max(0, y0 * sy), min(bg.width, x1 * sx), min(bg.height, y1 * sy))
    if b[2] <= b[0] or b[3] <= b[1]:
        return None
    region = bg.crop(tuple(round(v) for v in b)).convert("L").resize((32, 32))
    return sum(region.getdata()) / (32 * 32 * 255)


def _busy_map(bg: Image.Image):
    """底图缩小后的边缘图和亮度图，给自动避让打分用。"""
    g = bg.convert("L").resize((120, max(1, round(120 * bg.height / bg.width))))
    return g.filter(ImageFilter.FIND_EDGES), g


def _spot_score(maps, W: int, H: int, box, white_text: bool) -> float:
    """字块放在 box（图层坐标）这里有多「挤」：边缘多、压在画面中间（产品一般在中间）、白字落在浅色上都扣分。越小越好。"""
    edges, gray = maps
    sx, sy = edges.width / W, edges.height / H
    b = (max(0, round(box[0] * sx)), max(0, round(box[1] * sy)),
         min(edges.width, round(box[2] * sx)), min(edges.height, round(box[3] * sy)))
    if b[2] <= b[0] or b[3] <= b[1]:
        return 9.0
    n = (b[2] - b[0]) * (b[3] - b[1])
    mean_edge = sum(edges.crop(b).getdata()) / (n * 255)
    # 字块切成 6×2 小格，取最挤的一格：字只蹭到产品一角时，平均值看不出来，最大值看得出来
    worst = 0.0
    cw_, ch_ = max(1, (b[2] - b[0]) / 6), max(1, (b[3] - b[1]) / 2)
    for i in range(6):
        for j in range(2):
            c = (round(b[0] + i * cw_), round(b[1] + j * ch_), round(b[0] + (i + 1) * cw_), round(b[1] + (j + 1) * ch_))
            if c[2] > c[0] and c[3] > c[1]:
                m = (c[2] - c[0]) * (c[3] - c[1])
                worst = max(worst, sum(edges.crop(c).getdata()) / (m * 255))
    edge = mean_edge * 0.5 + worst * 0.5
    lum = sum(gray.crop(b).getdata()) / (n * 255)
    # 和画面中间那块（横 20–80%、竖 28–88%）重叠了多少。磨砂玻璃杯这类东西很平滑，边缘分不出来，靠这条躲开
    cx0, cy0, cx1, cy1 = W * 0.20, H * 0.28, W * 0.80, H * 0.88
    ov = max(0, min(box[2], cx1) - max(box[0], cx0)) * max(0, min(box[3], cy1) - max(box[1], cy0))
    center = ov / max(1, (box[2] - box[0]) * (box[3] - box[1]))
    return edge * 4 + center * 0.8 + (max(0.0, lum - 0.6) * 0.6 if white_text else 0.0)  # 浅底白字会加描边，扣得轻


def _x_left(align: str, W: int, width: float) -> float:
    return {"left": W * 0.06, "right": W * 0.94 - width}.get(align, (W - width) / 2)


def _shadowed(canvas: Image.Image, shadow, px: int) -> Image.Image:
    if not shadow:
        return canvas
    dx, dy, blur = round(px * shadow[0]), round(px * shadow[1]), round(px * shadow[2])
    sh = Image.new("RGBA", canvas.size, shadow[3][:3] + (0,))
    sh.putalpha(canvas.getchannel("A").point(lambda v: v * shadow[3][3] // 255))
    if blur:
        sh = sh.filter(ImageFilter.GaussianBlur(blur))
    base = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    base.alpha_composite(sh, (dx, dy))
    return Image.alpha_composite(base, canvas)


def _torn_rect(d: ImageDraw.ImageDraw, x0, y0, x1, y1, color, rnd: random.Random, tooth: float) -> None:
    """胶带：上下直边，左右两头锯齿撕边。"""
    pts = [(x0, y0), (x1, y0)]
    n = max(4, round((y1 - y0) / tooth))
    for i in range(1, n + 1):
        pts.append((x1 + (rnd.uniform(-1, 1) * tooth * 0.6 if i < n else 0), y0 + (y1 - y0) * i / n))
    pts.append((x0, y1))
    for i in range(n - 1, 0, -1):
        pts.append((x0 + rnd.uniform(-1, 1) * tooth * 0.6, y0 + (y1 - y0) * i / n))
    d.polygon(pts, fill=color)


def _arrow(layer: Image.Image, start, end, px: int, color, shadow, rnd: random.Random) -> None:
    """手绘弯箭头：二次曲线 + 箭头两撇，带同样的投影。"""
    art = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(art)
    (sx, sy), (ex, ey) = start, end
    mx, my = (sx + ex) / 2, (sy + ey) / 2
    L = math.hypot(ex - sx, ey - sy) or 1
    nx, ny = -(ey - sy) / L, (ex - sx) / L
    bend = L * 0.28 * (1 if rnd.random() < 0.5 else -1)
    cx, cy = mx + nx * bend, my + ny * bend
    pts = []
    for i in range(41):
        t = i / 40
        pts.append(((1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * ex,
                    (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * ey))
    w = max(2, round(px * 0.07))
    d.line(pts, fill=color, width=w, joint="curve")
    ang = math.atan2(ey - pts[-4][1], ex - pts[-4][0])
    head = px * 0.55
    for da in (2.6, -2.6):
        d.line([(ex, ey), (ex + head * math.cos(ang + da), ey + head * math.sin(ang + da))], fill=color, width=w)
    for p in ((sx, sy), (ex, ey)):
        d.ellipse([p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2], fill=color)
    layer.alpha_composite(_shadowed(art, shadow, px))


def _arc_canvas(line: Line, R: float, margin: int) -> Image.Image:
    """一行字沿圆弧排在圆的顶上：每个字单独画好再转角度贴上去。"""
    chars = []
    for seg in line.segs:
        t, k, f = seg
        for ch in ([t] if k == "emoji" else list(t)):
            w = line.px * 1.08 if k == "emoji" else f.getlength(ch)
            chars.append((ch, k, f, w))
    total = sum(c[3] for c in chars) + line.tracking * max(0, len(chars) - 1)
    span = total / R
    pad = line.smax + 4
    ch_h = line.h + 2 * pad
    size = round(2 * R + 2 * ch_h + 2 * margin)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ocx, ocy = size / 2, size / 2
    s = 0.0
    for ch, k, f, w in chars:
        theta = -span / 2 + (s + w / 2) / R
        s += w + line.tracking
        tile = Image.new("RGBA", (round(w + 2 * pad), round(ch_h)), (0, 0, 0, 0))
        if k == "emoji":
            em = emoji_image(ch, line.px)
            if em is not None:
                tile.alpha_composite(em, (round((tile.width - em.width) / 2), round((tile.height - em.height) / 2)))
        else:
            d = ImageDraw.Draw(tile)
            y = pad + line.asc - f.getmetrics()[0]
            for sw, sc in line.strokes:
                d.text((pad, y), ch, font=f, fill=sc, stroke_width=sw, stroke_fill=sc)
            d.text((pad, y), ch, font=f, fill=line.emph if k == "e" else line.fill)
        tile = tile.rotate(-math.degrees(theta), resample=Image.Resampling.BICUBIC, expand=True)
        cx, cy = ocx + R * math.sin(theta), ocy - R * math.cos(theta)
        canvas.alpha_composite(tile, (round(cx - tile.width / 2), round(cy - tile.height / 2)))
    bb = canvas.getbbox()
    return canvas.crop((max(0, bb[0] - margin), max(0, bb[1] - margin),
                        min(size, bb[2] + margin), min(size, bb[3] + margin))) if bb else canvas


def render(text: str, W: int, H: int, style: str = "soft", size: float | None = None, pos: str | None = None,
           align: str | None = None, color: str | None = None, fill: str | None = None,
           font: str | None = None, tilt: float | None = None, tag: str | None = None,
           sparkle: bool | None = None, point: tuple[float, float] | None = None,
           max_width: float = 0.88, bg: Image.Image | None = None, avoid: bool = True,
           _shrink: int = 0) -> tuple[Image.Image, dict]:
    """画一层 W×H 的透明花字图层。没给的参数用样式自己的默认值。
    size：字号占画面宽的比例；pos：top / center / bottom 或 0–1（字块中心在画面高的位置）；tilt 正数往右上翘。
    tag：最上面加一行小字（正文的样子、0.6 倍大）。point：arrow 样式箭头指向哪（0–1 的比例坐标）。
    bg：底图（任意尺寸，会按比例对上）。给了就看字落的地方亮不亮：fill=auto 选深字或白字；浅底上的白字加重投影和细边。
    avoid：没给 pos 又给了底图时，样式默认的位置太挤（压到产品、边缘多、白字落在浅色上）就上下挪到最近的空处。"""
    pos_given = pos is not None
    st = {**DEFAULTS, **STYLES[style]}
    size = st["size"] if size is None else size
    pos = st["pos"] if pos is None else pos
    align = st["align"] if align is None else align
    tilt = st["tilt"] if tilt is None else tilt
    sparkle = st["sparkle"] if sparkle is None else sparkle
    accent = color or st.get("accent", XHS_RED)
    if style == "stamp" and not (text or "").strip():
        text = _dt.datetime.now().strftime("'%y %m %d")
    cjk = font or st["font"]
    latin = st.get("latin")
    _, _, font_desc = resolve_font(cjk)
    shadow = st.get("shadow")
    strokes = [(w, _rgba(c, accent)) for w, c in st["strokes"]]
    card = st.get("card")
    is_arc = bool(st.get("arc"))
    px = max(12, round(W * size))
    cy = _anchor(pos, H)

    # 字色：auto 看字落的地方亮不亮
    fill_s, emph_s = fill or st["fill"], st["emph"]
    xl = _x_left(align, W, W * 0.45)
    lum = _lum(bg, W, H, xl, cy - H * 0.06, xl + W * 0.45, cy + H * 0.06)
    auto_dark = fill_s == "auto" and lum is not None and lum > 0.55
    if fill_s == "auto":
        fill_s = INK_DARK if auto_dark else "#FFFFFF"
        if auto_dark:  # 深色字：不要黑投影，加一圈淡白晕，落在花一点的地方也看得清
            shadow, strokes = None, [(0.05, (255, 255, 255, 150))]
    if emph_s == "auto":
        emph_s = fill_s
    fill_c, emph_c = _rgba(fill_s, accent), _rgba(emph_s, accent)
    boost = not auto_dark and not card and lum is not None and lum > 0.58 and sum(fill_c[:3]) / 765 > 0.7
    if boost:  # 浅色背景上的白字：加一圈深灰细描边，投影加深，不然白字糊在白底上看不清
        if shadow:
            shadow = (shadow[0], shadow[1] * 1.2, shadow[2], shadow[3][:3] + (min(220, round(shadow[3][3] * 1.8)),))
        if all(c[3] < 128 for _, c in strokes):  # 原来只有淡细边（或没有边）才换；big 这类本来就有粗边的不动
            strokes = [(max(0.06, max((w for w, _ in strokes), default=0)), (55, 55, 55, 215))]

    lines_runs = parse(text) if text else []
    if is_arc and lines_runs:  # 弧形只排一行
        lines_runs = [[r for ln in lines_runs for r in ln]]
    pad_x = 0
    while True:  # 太宽就缩小
        sw = [(round(px * w), c) for w, c in strokes]
        trk = round(px * st["tracking"])
        lines = [Line(r, px, cjk, latin, fill_c, emph_c, sw, trk) for r in lines_runs]
        if tag:
            lines.insert(0, Line(parse(tag)[0], max(10, round(px * 0.6)), cjk, latin, fill_c, emph_c,
                                 [(max(1, round(w * 0.6)), c) for w, c in sw], round(trk * 0.6)))
        if not lines:
            raise RuntimeError("没有文字")
        if is_arc:
            break
        pad_x = round(px * card["pad_x"]) if card else 0
        stair = round(px * st["stair"])
        block_w = max(ln.w + 2 * (ln.smax + pad_x) + i * stair for i, ln in enumerate(lines))
        if block_w <= W * max_width or px <= 12:
            break
        px = max(12, int(px * 0.93))

    smax = max((ln.smax for ln in lines), default=0)
    sh_ext = (max(abs(round(px * shadow[0])), abs(round(px * shadow[1]))) + round(px * shadow[2]) * 2) if shadow else 0
    margin = smax + sh_ext + round(px * (0.6 if sparkle else 0.2)) + 4

    if is_arc:
        canvas = _arc_canvas(lines[0], W * st["arc"], margin)
    else:
        gap = round(px * st["gap"])
        pad_y = round(px * card["pad_y"]) if card else 0
        cw = int(block_w + 2 * margin)
        ch = int(sum(ln.h for ln in lines) + gap * (len(lines) - 1) + 2 * (margin + pad_y))
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)
        rows = []  # (行, x, 字顶 y)
        y = margin + pad_y
        for i, ln in enumerate(lines):
            if align == "left":
                x = margin + ln.smax + pad_x + i * stair
            elif align == "right":
                x = cw - margin - ln.smax - pad_x - ln.w - (len(lines) - 1 - i) * stair
            else:
                x = (cw - ln.w) / 2 + i * stair / 2
            rows.append((ln, x, y))
            y += ln.h + gap
        if card:
            cc = _rgba(card["color"], accent)
            x0 = min(x for _, x, _ in rows) - pad_x
            x1 = max(x + ln.w for ln, x, _ in rows) + pad_x
            y0, y1 = margin, ch - margin
            if card.get("per_line"):  # 每行一条底：按这一行的字宽画，行和行之间留空
                for ln, x, ty in rows:
                    d.rectangle([x - pad_x, ty - pad_y, x + ln.w + pad_x, ty + ln.h + pad_y], fill=cc)
            elif card.get("torn"):
                _torn_rect(d, x0, y0, x1, y1, cc, random.Random(text), tooth=px * 0.18)
            else:
                d.rounded_rectangle([x0, y0, x1, y1], radius=min(round(px * card["radius"]), (y1 - y0) // 2), fill=cc)
            if card.get("tail"):
                tx = x0 + px * 0.9
                d.polygon([(tx, y1 - 2), (tx + px * 0.55, y1 - 2), (tx - px * 0.15, y1 + px * 0.4)], fill=cc)
        depth = max((len(ln.strokes) for ln in lines), default=0)
        for level in range(depth):  # 描边由外到内一层层画，先画完所有字的外层再画内层
            for ln, x, ty in rows:
                if level < len(ln.strokes):
                    ln.draw(canvas, x, ty, ln.strokes[level])
        for ln, x, ty in rows:
            ln.draw(canvas, x, ty, "fill")
        if sparkle and rows:
            ln, x, ty = rows[0]
            _sparkle(canvas, x + ln.w + px * 0.1, ty - px * 0.55, round(px * 0.85))
            ln, x, ty = rows[-1]
            _sparkle(canvas, x - px * 0.8, ty + ln.h * 0.45, round(px * 0.6))

    canvas = _shadowed(canvas, shadow, px)
    if tilt:
        canvas = canvas.rotate(tilt, resample=Image.Resampling.BICUBIC, expand=True)

    bw, bh = canvas.size
    def side_x(a: str) -> int:
        if a == "left":
            return round(W * 0.06) - margin
        if a == "right":
            return round(W * 0.94) - bw + margin
        return round((W - bw) / 2)
    x0 = side_x(align)
    y0 = round(cy - bh / 2)
    bb = canvas.getbbox()  # 按真正有字的范围，把字块挪进画面（留 3% 边）
    if bb:
        safe_x, safe_y = round(W * 0.03), round(H * 0.03)
        x0 += max(0, safe_x - (x0 + bb[0])) - max(0, (x0 + bb[2]) - (W - safe_x))
        y0 += max(0, safe_y - (y0 + bb[1])) - max(0, (y0 + bb[3]) - (H - safe_y))
    if avoid and not pos_given and bg is not None and bb and style != "stamp":
        maps = _busy_map(bg)
        white = sum(fill_c[:3]) / 765 > 0.7 and not card
        bh_real = bb[3] - bb[1]
        here = _spot_score(maps, W, H, (x0 + bb[0], y0 + bb[1], x0 + bb[2], y0 + bb[3]), white)
        if here > 0.3:
            best = (here, cy, align)
            step = H * 0.03
            # 左、中、右都试（换边多扣一点，同样空时不换），居中的样式也能挤到一边去
            sides = [(align, x0, 0.0)] + [(a, side_x(a), 0.05) for a in ("left", "center", "right") if a != align]
            for k in range(0, 34):
                for side, sx0, extra in sides:
                    for sgn in ((1,) if k == 0 else (1, -1)):
                        c2 = cy + sgn * k * step
                        top = c2 - bh / 2 + bb[1]
                        if top < H * 0.03 or top + bh_real > H * 0.97 or (k == 0 and side == align):
                            continue
                        sc = _spot_score(maps, W, H, (sx0 + bb[0], top, sx0 + bb[2], top + bh_real), white)
                        sc += k * 0.004 + extra  # 同样空的话，离样式默认位置近的优先
                        if sc < best[0]:
                            best = (sc, c2, side)
                if best[0] < 0.2:
                    break
            if best[0] > 0.4 and _shrink < 2:  # 哪儿都放不下：字缩小一档再找，最多两次（约 70%）
                layer, info = render(text, W, H, style, size * 0.85, None, align, color, fill, font, tilt,
                                     tag, sparkle, point, max_width, bg, avoid=True, _shrink=_shrink + 1)
                info.setdefault("缩小", f"放不下，字缩到 {0.85 ** (_shrink + 1):.0%}")
                return layer, info
            if (best[1], best[2]) != (cy, align):
                layer, info = render(text, W, H, style, size, f"{best[1] / H:.3f}", best[2], color, fill, font, tilt,
                                     tag, sparkle, point, max_width, bg, avoid=False)
                info["位置"] = f"自动避让到 {best[1] / H:.2f}" + (f"、{best[2]}" if best[2] != align else "") + f"（原 {pos}）"
                return layer, info
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(canvas, (x0, y0), canvas)

    if st.get("arrow") and bb:
        tx, ty = point or (0.5, 0.55)
        tx, ty = tx * W, ty * H
        bx0, by0, bx1, by1 = x0 + bb[0], y0 + bb[1], x0 + bb[2], y0 + bb[3]
        if by0 <= ty <= by1:  # 目标在字块左右：从侧边出
            sx, sy = (bx0 - px * 0.25 if tx < bx0 else bx1 + px * 0.25), (by0 + by1) / 2
        else:                 # 目标在上下：从上边或下边出
            sx, sy = min(max(tx, bx0 + px), bx1 - px), (by1 + px * 0.25 if ty > by1 else by0 - px * 0.25)
        L = math.hypot(tx - sx, ty - sy)
        k = max(0.0, 1 - (px * 1.5) / L) if L else 0  # 箭头停在目标前一点，不戳到产品上
        _arrow(layer, (sx, sy), (sx + (tx - sx) * k, sy + (ty - sy) * k), px, fill_c, shadow, random.Random(text))

    info = {"文字": text, "样式": style, "字体": font_desc, "字号px": px, "位置": pos, "对齐": align,
            "倾斜": tilt, "字色": fill_s.replace("{accent}", accent)}
    if boost:
        info["浅底加深"] = True
    if tag:
        info["小标签"] = tag
    return layer, info


def main() -> None:
    """静态图加字：uv run huazi.py 图 --style ochre --text "…" --out 新图.jpg"""
    import argparse
    from PIL import ImageOps
    ap = argparse.ArgumentParser(description="静态图加小红书花字")
    ap.add_argument("image")
    ap.add_argument("--text", required=True, help="「|」换行，【】强调")
    ap.add_argument("--style", default="soft", choices=list(STYLES))
    ap.add_argument("--size", type=float)
    ap.add_argument("--pos")
    ap.add_argument("--align", choices=["left", "center", "right"])
    ap.add_argument("--color")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    src = Image.open(a.image)
    icc = src.info.get("icc_profile")
    base = ImageOps.exif_transpose(src).convert("RGB")
    layer, info = render(a.text, base.width, base.height, a.style, a.size, a.pos, a.align, a.color, bg=base)
    out = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    out.save(a.out, quality=95, icc_profile=icc, subsampling=0)
    print(f"✓ {a.out}  {info['样式']} {info['字体']} {info['字号px']}px  位置 {info['位置']}")


if __name__ == "__main__":
    main()
