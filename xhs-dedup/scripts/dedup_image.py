# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "pillow-heif", "numpy", "imagehash"]
# ///
"""图片去重 / 拼图。输出 JPEG（重新编码，原图的元数据全部去掉），原图不动。

用法：
    uv run dedup_image.py run 图片或文件夹 [...] [--level light|medium] [--copies N] [各项开关]
    uv run dedup_image.py collage 图1 图2 [图3 图4] [--layout v2|h2|3|3h|3v|4] [--copies N]

默认写入新信息：iPhone 机型、拍摄时间、镜头和曝光参数，颜色存成 Display P3（见 meta.py）。
--no-meta 只去不写。
输出在 <输入>_去重/，另写 manifest.json（每份用了哪些操作、参数、和原图的相似度）。
"""

from __future__ import annotations

import argparse
import math
import pathlib
import sys

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

sys.dont_write_bytecode = True  # 不在 skill 目录里留 __pycache__
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402
import meta as M  # noqa: E402
import similarity as S  # noqa: E402

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # 没装也能处理 jpg/png
    pass

# 每档默认做什么。None 表示这档不做。数值是随机范围。
LEVELS = {
    "light": {
        "crop": (2.0, 4.0),        # 裁掉 2–4%，再放大回去
        "rotate": (0.15, 0.4),     # 转 0.15–0.4°，自动放大盖住黑角
        "resize": (0.97, 1.03),    # 输出尺寸放大或缩小 3% 以内
        "color": "light",          # 亮度/对比 ±0.5–2%，饱和 ±1–3%，色相 ±0.5–2°
        "rgb_shift": None,
        "grain": None,
        "mirror": False,
    },
    "medium": {
        "crop": (4.0, 8.0),
        "rotate": (0.3, 0.6),
        "resize": (0.95, 1.05),
        "color": "medium",         # 亮度/对比 ±1–3%，饱和 ±2–5%，色相 ±1–3.5°
        "rgb_shift": None,         # 2026-10-20 起默认关：会出彩边和光晕（像炫光），实测对相似度没帮助
        "grain": None,             # 同上，默认关
        "mirror": False,
    },
}
# 默认关、但可以用 --rgb-shift / --grain 手动打开的项，打开时用这里的范围
OPTIONAL = {
    "rgb_shift": (1, 2),       # 红、蓝通道各错开 1–2 像素
    "grain": (2.0, 3.5),       # 颗粒（高斯噪点）强度
}
COLOR = {
    "light": {"bc": (0.005, 0.02), "sat": (0.01, 0.03), "hue": (0.5, 2.0)},
    "medium": {"bc": (0.01, 0.03), "sat": (0.02, 0.05), "hue": (1.0, 3.5)},
}


# ---------------- 基础 ----------------

def load(path: pathlib.Path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    icc = img.info.get("icc_profile")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    if icc:  # 转成 sRGB 再丢掉色彩配置，避免颜色跑偏
        try:
            src = ImageCms.ImageCmsProfile(__import__("io").BytesIO(icc))
            img = ImageCms.profileToProfile(img, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:
            pass
    img.info = {}  # 丢掉原图带的注释、EXIF 等附加信息，保存时不会带出去
    return img


def save_jpeg(img: Image.Image, path: pathlib.Path, rnd: C.Rnd, quality: int | None, a=None) -> tuple[int, dict | None]:
    """原图的附加信息一律不带。a.meta 开着时，写入新信息（机型、拍摄时间、镜头和曝光参数）。"""
    dev = t = None
    if a is not None and a.meta:
        dev, t = M.pick_device(rnd, a.devices), M.shot_time(rnd, a.meta_days)
    q = M.save_jpeg(img, path, rnd, quality, dev, t)
    return q, (M.describe(dev, t) if dev else None)


def color_matrix(br: float, ct: float, sat: float, hue_deg: float, gains) -> tuple[np.ndarray, float]:
    """亮度、对比、饱和、色相、RGB 增益合成一个 3×3 矩阵。"""
    a = math.radians(hue_deg)
    c, s = math.cos(a), math.sin(a)
    lr, lg, lb = 0.213, 0.715, 0.072
    hue = np.array([
        [lr + c * (1 - lr) - s * lr, lg - c * lg - s * lg, lb - c * lb + s * (1 - lb)],
        [lr - c * lr + s * 0.143, lg + c * (1 - lg) + s * 0.140, lb - c * lb - s * 0.283],
        [lr - c * lr - s * (1 - lr), lg - c * lg + s * lg, lb + c * (1 - lb) + s * lb],
    ])
    sm = np.array([
        [lr + (1 - lr) * sat, lg - lg * sat, lb - lb * sat],
        [lr - lr * sat, lg + (1 - lg) * sat, lb - lb * sat],
        [lr - lr * sat, lg - lg * sat, lb + (1 - lb) * sat],
    ])
    m = (sm @ hue) * ct * br * np.asarray(gains)[:, None]
    return m, 128.0 * (1 - ct)


def shift_channel(ch: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """平移一个通道，空出来的边用边缘像素补。"""
    h, w = ch.shape
    pad = np.pad(ch, ((abs(dy), abs(dy)), (abs(dx), abs(dx))), mode="edge")
    y0, x0 = abs(dy) - dy, abs(dx) - dx
    return pad[y0:y0 + h, x0:x0 + w]


def blur_extend(img: Image.Image, aspect: float, rnd: C.Rnd) -> tuple[Image.Image, dict]:
    """模糊拓边：同一张图放大糊成底，原图缩小居中放上去，画布变成目标比例。"""
    w, h = img.size
    long_side = max(w, h)
    if aspect >= 1:
        cw, ch = long_side, round(long_side / aspect)
    else:
        cw, ch = round(long_side * aspect), long_side
    margin = rnd.uni(0.86, 0.94)
    bg = ImageOps.fit(img, (cw, ch), Image.Resampling.LANCZOS)
    radius = max(8, round(min(cw, ch) / 40))
    bg = bg.filter(ImageFilter.GaussianBlur(radius))
    bg = ImageEnhance.Brightness(bg).enhance(rnd.uni(0.82, 0.92))
    k = min(cw * margin / w, ch * margin / h)
    fg = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)
    ox = (cw - fg.width) // 2 + round(cw * rnd.sym(0, 0.01, 4))
    oy = (ch - fg.height) // 2 + round(ch * rnd.sym(0, 0.01, 4))
    bg.paste(fg, (ox, oy))
    return bg, {"画布": f"{cw}x{ch}", "前景占比": margin, "模糊半径": radius}


def draw_text(img: Image.Image, text: str, pos: str, size: float, color: str, band: bool,
              font_path: str | None, rnd: C.Rnd) -> dict:
    """封面痛点字：白字黑描边（可改颜色），太宽会自动缩小；可加半透明底条。"""
    path, idx = C.find_font(font_path)
    w, h = img.size
    text, gone = C.strip_emoji(text)
    if gone:
        print(f"  ! 字体里没有表情符号，画出来会是方框，已去掉：{gone}")
    lines = text.replace("\\n", "\n").replace("|", "\n").split("\n")
    px = max(12, round(w * size))
    d = ImageDraw.Draw(img)
    while True:
        f = ImageFont.truetype(path, px, index=idx)
        stroke = max(2, px // 12)
        boxes = [d.textbbox((0, 0), ln, font=f, stroke_width=stroke) for ln in lines]
        tw = max(b[2] - b[0] for b in boxes)
        if tw <= w * 0.9 or px <= 12:
            break
        px = int(px * 0.92)
    lh = max(b[3] - b[1] for b in boxes)
    gap = round(lh * 0.25)
    total = lh * len(lines) + gap * (len(lines) - 1)
    jitter = rnd.sym(0, 0.01, 4)
    y = {"top": h * (0.08 + jitter), "center": (h - total) / 2, "bottom": h * (0.92 + jitter) - total}[pos]
    fill = ImageColorSafe(color)
    if band:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle([0, y - gap, w, y + total + gap], fill=(0, 0, 0, 110))
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
        d = ImageDraw.Draw(img)
    for ln, b in zip(lines, boxes):
        x = (w - (b[2] - b[0])) / 2 - b[0]
        d.text((x, y - b[1]), ln, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += lh + gap
    return {"文字": text, "位置": pos, "字号px": px}


def ImageColorSafe(c: str):
    from PIL import ImageColor
    try:
        return ImageColor.getrgb(c)
    except ValueError:
        C.die(f"颜色写成 #FFFFFF 或 white 这样：{c}")


def load_overlays(spec: str | None) -> list[Image.Image]:
    if not spec:
        return []
    p = pathlib.Path(spec).expanduser()
    files = sorted(f for f in p.iterdir() if f.suffix.lower() in C.IMAGE_EXT) if p.is_dir() else [p]
    if not files:
        C.die(f"垫图文件夹里没有图片：{p}")
    return [load(f) for f in files]


# ---------------- 一份 ----------------

def make_copy(src: Image.Image, cfg: dict, a, rnd: C.Rnd, overlays: list[Image.Image]):
    ops: list[dict] = []
    W0, H0 = src.size

    # 1. 裁边
    cx, cy, cw, ch = 0.0, 0.0, float(W0), float(H0)
    if cfg["crop"]:
        cf = rnd.uni(*cfg["crop"], 2) / 100
        fx, fy = cf * rnd.uni(0.75, 1.25), cf * rnd.uni(0.75, 1.25)
        cw, ch = W0 * (1 - fx), H0 * (1 - fy)
        cx, cy = (W0 - cw) * rnd.uni(0, 1), (H0 - ch) * rnd.uni(0, 1)
        ops.append({"op": "crop", "名称": "裁边", "参数": {"比例%": round(cf * 100, 2), "宽%": round(fx * 100, 2), "高%": round(fy * 100, 2)}})

    # 2. 输出尺寸（放大缩小）
    s = rnd.uni(*cfg["resize"], 3) if cfg["resize"] else 1.0
    k = s * max(W0 / cw, H0 / ch)
    W, H = max(16, round(cw * k)), max(16, round(ch * k))
    if a.max_side and max(W, H) > a.max_side:
        r = a.max_side / max(W, H)
        W, H = round(W * r), round(H * r)
    img = src.resize((W, H), Image.Resampling.LANCZOS, box=(cx, cy, cx + cw, cy + ch))
    if cfg["resize"]:
        ops.append({"op": "resize", "名称": "缩放", "参数": {"倍数": s, "尺寸": f"{W}x{H}"}})

    # 3. 微转 + 放大盖黑角
    if cfg["rotate"]:
        ang = rnd.sym(*cfg["rotate"], 2)
        t = math.radians(abs(ang))
        zoom = (math.cos(t) + max(W / H, H / W) * math.sin(t)) * rnd.uni(1.0, 1.01, 4)
        img = img.rotate(ang, resample=Image.Resampling.BICUBIC, expand=False)
        zw, zh = W / zoom, H / zoom
        img = img.resize((W, H), Image.Resampling.LANCZOS, box=((W - zw) / 2, (H - zh) / 2, (W + zw) / 2, (H + zh) / 2))
        ops.append({"op": "rotate", "名称": "微转", "参数": {"角度°": ang, "放大": round(zoom, 4)}})

    # 4. 镜像
    if cfg["mirror"]:
        img = ImageOps.mirror(img)
        ops.append({"op": "mirror", "名称": "镜像", "参数": {}})

    # 5. 垫图
    if overlays:
        o = rnd.choice(overlays)
        cover = max(W / o.width, H / o.height) * rnd.uni(1.0, 1.3)
        ow, oh = round(o.width * cover), round(o.height * cover)
        layer = o.resize((ow, oh), Image.Resampling.LANCZOS)
        ox, oy = round(rnd.uni(W - ow, 0)), round(rnd.uni(H - oh, 0))
        alpha = min(1.0, max(0.005, a.overlay_opacity / 100 * rnd.uni(0.85, 1.15)))
        canvas = img.copy()
        canvas.paste(layer, (ox, oy))
        img = Image.blend(img, canvas, alpha)
        ops.append({"op": "overlay", "名称": "垫图", "参数": {"透明度%": round(alpha * 100, 2)}})

    # 6–8. 逐像素：调色、RGB 错位、颗粒
    if cfg["color"] or cfg["rgb_shift"] or cfg["grain"]:
        arr = np.asarray(img, dtype=np.float32)
        if cfg["color"]:
            c = COLOR[cfg["color"]]
            br, ct = 1 + rnd.sym(*c["bc"], 4), 1 + rnd.sym(*c["bc"], 4)
            sat, hue = 1 + rnd.sym(*c["sat"], 4), rnd.sym(*c["hue"], 2)
            gains = [rnd.uni(0.99, 1.01, 4) for _ in range(3)]
            m, off = color_matrix(br, ct, sat, hue, gains)
            arr = arr @ m.T.astype(np.float32) + off
            ops.append({"op": "color", "名称": "调色", "参数": {"亮度": br, "对比": ct, "饱和": sat, "色相°": hue, "RGB增益": gains}})
        if cfg["rgb_shift"]:
            n = rnd.int(*cfg["rgb_shift"])
            dxr, dyr = rnd.choice((-n, n)), rnd.choice((-1, 0, 1))
            arr[..., 0] = shift_channel(arr[..., 0], dxr, dyr)
            arr[..., 2] = shift_channel(arr[..., 2], -dxr, -dyr)
            ops.append({"op": "rgb_shift", "名称": "RGB错位", "参数": {"红x": dxr, "红y": dyr, "蓝x": -dxr, "蓝y": -dyr}})
        if cfg["grain"]:
            sigma = rnd.uni(*cfg["grain"], 2)
            noise = np.random.default_rng(rnd.int(0, 2**31)).normal(0, sigma, arr.shape[:2]).astype(np.float32)
            arr += noise[..., None]
            ops.append({"op": "grain", "名称": "颗粒", "参数": {"强度σ": sigma}})
        img = Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), "RGB")

    # 9. 模糊拓边
    if a.blur_extend:
        img, p = blur_extend(img, C.parse_aspect(a.blur_extend), rnd)
        p["比例"] = a.blur_extend
        ops.append({"op": "blur_extend", "名称": "模糊拓边", "参数": p})

    # 10. 封面字
    if a.text:
        p = draw_text(img, a.text, a.text_pos, a.text_size, a.text_color, a.text_band, a.font, rnd)
        ops.append({"op": "text", "名称": "加字", "参数": p})
    return img, ops


def resolve_cfg(a) -> dict:
    cfg = dict(LEVELS[a.level])
    if a.crop_pct is not None:
        cfg["crop"] = (a.crop_pct, a.crop_pct) if a.crop_pct > 0 else None
    for key, flag in (("rotate", a.rotate), ("resize", a.resize), ("grain", a.grain), ("rgb_shift", a.rgb_shift)):
        if flag is True and not cfg[key]:
            cfg[key] = LEVELS["medium"][key] or OPTIONAL[key]
        elif flag is False:
            cfg[key] = None
    if a.color is True and not cfg["color"]:
        cfg["color"] = "light"
    elif a.color is False:
        cfg["color"] = None
    if a.mirror is not None:
        cfg["mirror"] = a.mirror
    return cfg


# ---------------- run ----------------

def cmd_run(a) -> None:
    if not 1 <= a.copies <= 20:
        C.die("--copies 要在 1–20 之间")
    C.require_ai_cleaned(a.inputs, a.skip_ai_clean)
    pairs = C.collect(a.inputs, C.IMAGE_EXT)
    if not pairs:
        C.die("没有能处理的图片")
    cfg = resolve_cfg(a)
    rnd = C.Rnd(a.seed)
    overlays = load_overlays(a.overlay)
    if a.meta:
        M.model_range(a.devices)
    man = C.Manifest("dedup_image run", {k: v for k, v in vars(a).items() if k != "func"}, rnd.seed)
    print(f"共 {len(pairs)} 张，每张 {a.copies} 份，档位 {a.level}，种子 {rnd.seed}")
    if a.text:
        print("! 加了 --text：这批每张图都会加同样的字。通常只给封面加。")
    ok = fail = 0
    for src_path, default_out in pairs:
        out_dir = C.out_dir_from(a.out, default_out)
        try:
            src = load(src_path)
        except Exception as e:
            print(f"  ✗ {src_path.name}：读不了（{e}）")
            fail += a.copies
            continue
        src_md5 = C.md5(src_path)
        for _ in range(a.copies):
            try:
                img, ops = make_copy(src, cfg, a, rnd, overlays)
                out = C.next_path(out_dir, src_path.stem, "去重", ".jpg", [src_path])
                q, written = save_jpeg(img, out, rnd, a.quality, a)
                ops.append({"op": "jpeg", "名称": "重新编码", "参数": {"质量": q, "元数据": "原图的全部去掉"}})
                sim = S.image_distance(src, out)
                man.add(out_dir, {"原文件": str(src_path), "输出": out.name, "尺寸": f"{img.width}x{img.height}",
                                  "操作": ops, "相似度": sim, "MD5": C.md5(out), "原MD5": src_md5,
                                  "写入信息": written})
                print(f"  ✓ {out.name}  {C.fmt_ops(ops[:-1])}\n      相似度 {S.fmt_image(sim)}")
                ok += 1
            except Exception as e:
                print(f"  ✗ {src_path.name}：{e}")
                fail += 1
    for m in man.save():
        print(f"记录：{m}")
    print(f"完成：成功 {ok} 份，失败 {fail} 份。相似度 0–64，0 表示几乎一样，越大差得越多。")
    sys.exit(0 if fail == 0 else 2)


# ---------------- collage ----------------

def layout_cells(layout: str, W: int, H: int, g: int) -> list[tuple[int, int, int, int]]:
    """返回每格 (x, y, w, h)，格子之间和四周都留 g 像素。"""
    def col(x0, x1, rows):
        hh = (H - g * (rows + 1)) / rows
        return [(x0, round(g + i * (hh + g)), x1 - x0, round(hh)) for i in range(rows)]
    if layout == "h2":
        cw = (W - 3 * g) / 2
        return [(round(g + i * (cw + g)), g, round(cw), H - 2 * g) for i in range(2)]
    if layout == "v2":
        return col(g, W - g, 2)
    if layout == "3h":
        cw = (W - 4 * g) / 3
        return [(round(g + i * (cw + g)), g, round(cw), H - 2 * g) for i in range(3)]
    if layout == "3v":
        return col(g, W - g, 3)
    if layout == "3":  # 左大右二
        lw = round((W - 3 * g) * 0.58)
        return [(g, g, lw, H - 2 * g)] + col(2 * g + lw, W - g, 2)
    if layout == "4":
        cw, ch = (W - 3 * g) / 2, (H - 3 * g) / 2
        return [(round(g + c * (cw + g)), round(g + r * (ch + g)), round(cw), round(ch)) for r in range(2) for c in range(2)]
    C.die(f"不认识的拼法：{layout}")
    return []


def cmd_collage(a) -> None:
    files = [pathlib.Path(p).expanduser().resolve() for p in a.images]
    for f in files:
        if not f.is_file():
            C.die(f"找不到：{f}")
    n = len(files)
    if not 2 <= n <= 4:
        C.die("拼图要 2–4 张图")
    layout = a.layout or {2: "v2", 3: "3", 4: "4"}[n]
    need = {"h2": 2, "v2": 2, "3": 3, "3h": 3, "3v": 3, "4": 4}.get(layout)
    if need != n:
        C.die(f"拼法 {layout} 要 {need} 张图，现在给了 {n} 张")
    W, H = C.parse_size(a.size) or (1080, 1440)
    rnd = C.Rnd(a.seed)
    srcs = [load(f) for f in files]
    out_dir = C.out_dir_from(a.out, C.default_out(files[0]))
    if a.meta:
        M.model_range(a.devices)
    man = C.Manifest("dedup_image collage", {k: v for k, v in vars(a).items() if k != "func"}, rnd.seed)
    tile_cfg = dict(LEVELS["light"], resize=None)
    print(f"拼图 {layout}：{n} 张 → {W}x{H}，{a.copies} 份，种子 {rnd.seed}")
    for _ in range(a.copies):
        order = list(range(n))
        if a.shuffle:
            rnd.shuffle(order)
        if a.bg == "blur":
            canvas = ImageOps.fit(srcs[order[0]], (W, H)).filter(ImageFilter.GaussianBlur(max(8, W // 40)))
        else:
            canvas = Image.new("RGB", (W, H), ImageColorSafe(a.bg))
        tiles_ops = []
        for (x, y, cw, ch), i in zip(layout_cells(layout, W, H, a.gap), order):
            tile = srcs[i]
            t_ops: list[dict] = []
            if a.dedup:
                ta = argparse.Namespace(max_side=0, overlay_opacity=3, blur_extend=None, text=None)
                tile, t_ops = make_copy(tile, tile_cfg, ta, rnd, [])
            canvas.paste(ImageOps.fit(tile, (cw, ch), Image.Resampling.LANCZOS), (x, y))
            tiles_ops.append({"图": files[i].name, "位置": [x, y, cw, ch], "操作": t_ops})
        ops = [{"op": "collage", "名称": "拼图", "参数": {"拼法": layout, "尺寸": f"{W}x{H}", "间距": a.gap, "底色": a.bg}, "每格": tiles_ops}]
        if a.text:
            p = draw_text(canvas, a.text, a.text_pos, a.text_size, a.text_color, a.text_band, a.font, rnd)
            ops.append({"op": "text", "名称": "加字", "参数": p})
        out = C.next_path(out_dir, files[0].stem, f"拼图{layout}_", ".jpg", files)
        q, written = save_jpeg(canvas, out, rnd, a.quality, a)
        ops.append({"op": "jpeg", "名称": "重新编码", "参数": {"质量": q, "元数据": "原图的全部去掉"}})
        sims = {f.name: S.image_distance(s, out) for f, s in zip(files, srcs)}
        man.add(out_dir, {"原文件": [str(f) for f in files], "输出": out.name, "操作": ops, "相似度": sims, "MD5": C.md5(out),
                          "写入信息": written})
        print(f"  ✓ {out.name}  " + "；".join(f"对 {k}: {S.fmt_image(v)}" for k, v in sims.items()))
    for m in man.save():
        print(f"记录：{m}")


# ---------------- 参数 ----------------

def add_text_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("封面字")
    g.add_argument("--text", help="封面痛点字。用 | 或 \\n 换行")
    g.add_argument("--text-pos", choices=["top", "center", "bottom"], default="top", help="位置，默认 top")
    g.add_argument("--text-size", type=float, default=0.08, help="字高占图宽的比例，默认 0.08")
    g.add_argument("--text-color", default="#FFFFFF", help="字的颜色，默认白色（自带黑描边）")
    g.add_argument("--text-band", action="store_true", help="字后面加一条半透明黑底")
    g.add_argument("--font", help="字体文件，默认苹方，没有就用冬青黑体")


def main() -> None:
    ap = argparse.ArgumentParser(description="图片去重 / 拼图（输出到 <输入>_去重/）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    B = argparse.BooleanOptionalAction

    r = sub.add_parser("run", help="图片去重")
    r.add_argument("inputs", nargs="+", help="图片或文件夹（只读第一层）")
    r.add_argument("--level", choices=["light", "medium"], default="medium", help="档位，默认 medium（中度）")
    r.add_argument("--copies", type=int, default=1, help="每张出几份，1–20，默认 1")
    r.add_argument("--out", help="输出文件夹，默认 <输入>_去重/")
    r.add_argument("--seed", type=int, help="随机种子，同一个种子出同样结果")
    r.add_argument("--crop-pct", type=float, help="固定裁边百分比；0 表示不裁")
    r.add_argument("--rotate", action=B, default=None, help="微转（≤0.6°）")
    r.add_argument("--resize", action=B, default=None, help="放大缩小（±3–5%%）")
    r.add_argument("--color", action=B, default=None, help="调色")
    r.add_argument("--rgb-shift", action=B, default=None, help="RGB 错位 1–2 像素")
    r.add_argument("--grain", action=B, default=None, help="颗粒")
    r.add_argument("--mirror", action=B, default=None, help="左右镜像（有字会反，默认关）")
    r.add_argument("--max-side", type=int, default=0, help="长边最大像素，0 表示不限")
    r.add_argument("--overlay", help="垫图：一张图或一个文件夹")
    r.add_argument("--overlay-opacity", type=float, default=3.0, help="垫图透明度百分比，默认 3")
    r.add_argument("--blur-extend", nargs="?", const="3:4", help="模糊拓边到某比例，不写比例就是 3:4")
    r.add_argument("--quality", type=int, help="JPEG 质量，默认 90–95 随机")
    r.add_argument("--skip-ai-clean", action="store_true", help="跳过 clean-ai-info 检查（默认必须先清 AI 信息）")
    add_text_args(r)
    M.add_meta_args(r)
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("collage", help="拼图：双拼 / 三拼 / 四宫格")
    c.add_argument("images", nargs="+", help="2–4 张图，按顺序放")
    c.add_argument("--layout", choices=["h2", "v2", "3", "3h", "3v", "4"],
                   help="h2 左右双拼，v2 上下双拼，3 左大右二，3h 三竖条，3v 三横条，4 四宫格。默认按张数选")
    c.add_argument("--size", default="1080x1440", help="画布尺寸，默认 1080x1440（3:4）")
    c.add_argument("--gap", type=int, default=8, help="格子间距像素，默认 8")
    c.add_argument("--bg", default="#FFFFFF", help="底色，或 blur（第一张图糊成底），默认白")
    c.add_argument("--dedup", action=B, default=True, help="每格先做一遍轻度去重，默认开")
    c.add_argument("--shuffle", action="store_true", help="每份随机换格子顺序")
    c.add_argument("--copies", type=int, default=1, help="出几份，默认 1")
    c.add_argument("--out", help="输出文件夹，默认 <第一张图>_去重/")
    c.add_argument("--seed", type=int)
    c.add_argument("--quality", type=int)
    add_text_args(c)
    M.add_meta_args(c)
    c.set_defaults(func=cmd_collage)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
