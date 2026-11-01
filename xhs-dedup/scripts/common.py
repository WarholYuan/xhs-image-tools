"""小红书图片去重 公用函数：找输入、定输出名、写 manifest、随机参数、找中文字体。

只给同目录的脚本 import，不单独运行。依赖由调用它的脚本在 `# /// script` 里声明。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import random
import shutil
import subprocess
import sys
import time

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".tif", ".tiff", ".avif"}
OUT_SUFFIX = "_去重"
MANIFEST = "manifest.json"

# 中文字体候选（先找苹方，没有就用系统自带的冬青黑体 / 黑体）
FONT_CANDIDATES = [
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/PrivateFrameworks/FontServices.framework/Versions/A/Resources/Reserved/PingFangUI.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),  # W6 粗体
    ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
]


# ---------------- 输出与报错 ----------------

def die(msg: str, code: int = 1) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


def even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def parse_size(s: str | None) -> tuple[int, int] | None:
    if not s or s == "keep":
        return None
    try:
        w, h = s.lower().replace("×", "x").split("x")
        return even(int(w)), even(int(h))
    except ValueError:
        die(f"尺寸写成 1920x1080 这样：{s}")
    return None


def parse_aspect(s: str) -> float:
    s = s.replace("：", ":").replace("x", ":").replace("X", ":")
    try:
        a, b = s.split(":")
        r = float(a) / float(b)
    except (ValueError, ZeroDivisionError):
        die(f"比例写成 3:4 或 9:16 这样：{s}")
    return r


# ---------------- 输入与输出 ----------------

CLEAN_SUFFIX = "_clean"  # clean-ai-info 的输出名后缀，起输出名时去掉


def bare(name: str) -> str:
    return name[: -len(CLEAN_SUFFIX)] if name.endswith(CLEAN_SUFFIX) else name


AI_CHECK = pathlib.Path(__file__).resolve().parents[2] / "clean-ai-info" / "scripts" / "ai_text.py"


def require_ai_cleaned(inputs: list[str], skip: bool) -> None:
    """去重前必须先过 clean-ai-info：输入要是它的 _clean 输出（文件名或所在文件夹名带 _clean），
    并且 ai_text.py check 查不到 AI 字样。--skip-ai-clean 才能跳过，给明确要跳过的情况用。"""
    if skip:
        return
    raw_files = []
    for raw in inputs:
        p = pathlib.Path(raw).expanduser().resolve()
        cleaned = p.name.endswith(CLEAN_SUFFIX) if p.is_dir() else (
            p.stem.endswith(CLEAN_SUFFIX) or p.parent.name.endswith(CLEAN_SUFFIX))
        if not cleaned:
            raw_files.append(str(p))
    if raw_files:
        die("这些还没过 clean-ai-info，先清 AI 信息（输出名带 _clean）再去重：\n  " + "\n  ".join(raw_files)
            + "\n确实要跳过，加 --skip-ai-clean")
    if not AI_CHECK.exists():
        die(f"找不到 clean-ai-info 的检查脚本：{AI_CHECK}")
    r = subprocess.run(["uv", "run", str(AI_CHECK), "check", *inputs], capture_output=True, text=True)
    if r.returncode:
        die("clean-ai-info 检查还有 AI 字样，先清完再去重：\n" + (r.stdout.strip() or r.stderr.strip()))


def default_out(p: pathlib.Path) -> pathlib.Path:
    """单个文件的默认输出文件夹：<文件名>_去重/。
    文件已经在 xxx_去重/ 里（比如上一步的输出）时，放回同一个文件夹，不再套一层。"""
    return p.parent if p.parent.name.endswith(OUT_SUFFIX) else p.parent / f"{bare(p.stem)}{OUT_SUFFIX}"


def collect(inputs: list[str], exts: set[str]) -> list[tuple[pathlib.Path, pathlib.Path]]:
    """返回 [(文件, 输出文件夹)]。文件夹只读第一层，跳过隐藏文件。"""
    pairs: list[tuple[pathlib.Path, pathlib.Path]] = []
    for raw in inputs:
        p = pathlib.Path(raw).expanduser().resolve()
        if not p.exists():
            die(f"找不到：{p}")
        if p.is_dir():
            out = p.parent / f"{bare(p.name)}{OUT_SUFFIX}"
            files = sorted(f for f in p.iterdir()
                           if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in exts)
            if not files:
                print(f"! 文件夹里没有能处理的文件：{p}")
            pairs += [(f, out) for f in files]
        else:
            if p.suffix.lower() not in exts:
                print(f"! 跳过（格式不对）：{p.name}")
                continue
            pairs.append((p, default_out(p)))
    return pairs


def out_dir_from(arg: str | None, default: pathlib.Path) -> pathlib.Path:
    d = pathlib.Path(arg).expanduser().resolve() if arg else default
    d.mkdir(parents=True, exist_ok=True)
    return d


def next_path(out_dir: pathlib.Path, stem: str, tag: str, ext: str, sources: list[pathlib.Path] = ()) -> pathlib.Path:
    """找下一个没用过的编号：stem_tag01.ext、stem_tag02.ext…… 绝不覆盖已有文件和原文件。"""
    src = {s.resolve() for s in sources}
    stem = bare(stem)
    for i in range(1, 1000):
        cand = out_dir / f"{stem}_{tag}{i:02d}{ext}"
        sibling_exists = any((out_dir / f"{stem}_{tag}{i:02d}{e}").exists() for e in (".JPG", ".jpg", ".PNG", ".png"))
        if not cand.exists() and not sibling_exists and cand.resolve() not in src:
            return cand
    raise RuntimeError("输出编号用完了（999）")


def md5(path: pathlib.Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Manifest:
    """每个输出文件夹一个 manifest.json，每次运行追加一条 run。"""

    def __init__(self, tool: str, args: dict, seed: int):
        self.run = {"工具": tool, "时间": _dt.datetime.now().isoformat(timespec="seconds"),
                    "参数": args, "随机种子": seed, "输出": []}
        self.dirs: set[pathlib.Path] = set()

    def add(self, out_dir: pathlib.Path, item: dict) -> None:
        self.dirs.add(out_dir)
        item = dict(item)
        item["_dir"] = str(out_dir)
        self.run["输出"].append(item)

    def save(self) -> list[pathlib.Path]:
        written = []
        for d in sorted(self.dirs):
            path = d / MANIFEST
            data = {"说明": "小红书素材去重 生成记录：每次运行一条，列出每个输出用了哪些操作、参数和相似度", "runs": []}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            run = dict(self.run)
            run["输出"] = [{k: v for k, v in it.items() if k != "_dir"}
                         for it in self.run["输出"] if it["_dir"] == str(d)]
            data.setdefault("runs", []).append(run)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(path)
        return written


# ---------------- 随机 ----------------

class Rnd:
    def __init__(self, seed: int | None):
        self.seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        self.r = random.Random(self.seed)

    def uni(self, a: float, b: float, nd: int = 3) -> float:
        return round(self.r.uniform(a, b), nd)

    def sym(self, a: float, b: float, nd: int = 3) -> float:
        """幅度在 a–b 之间，正负随机。"""
        return round(self.r.uniform(a, b) * self.r.choice((-1, 1)), nd)

    def int(self, a: int, b: int) -> int:
        return self.r.randint(a, b)

    def choice(self, seq):
        return self.r.choice(seq)

    def shuffle(self, seq) -> None:
        self.r.shuffle(seq)

    def chance(self, p: float) -> bool:
        return self.r.random() < p


# ---------------- 字体与文字图 ----------------

EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF), (0x2300, 0x23FF), (0xFE00, 0xFE0F),
                (0x200D, 0x200D), (0x203C, 0x203C), (0x2049, 0x2049), (0x20E3, 0x20E3), (0xE0020, 0xE007F),
                (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297), (0x3299, 0x3299))


def strip_emoji(text: str) -> tuple[str, str]:
    """苹方、冬青黑体都没有表情符号，画出来是方框（2026-10-16 实测「🥳」变成方框）。
    返回 (去掉表情后的字, 去掉了哪些)。"""
    keep, gone = [], []
    for ch in text:
        (gone if any(a <= ord(ch) <= b for a, b in EMOJI_RANGES) else keep).append(ch)
    return "".join(keep).strip(), "".join(gone)


def find_font(user_font: str | None = None):
    from PIL import ImageFont

    cands = [(user_font, 0)] if user_font else []
    cands += FONT_CANDIDATES
    for path, idx in cands:
        if path and pathlib.Path(path).exists():
            try:
                ImageFont.truetype(path, 20, index=idx)
                return path, idx
            except OSError:
                continue
    die("找不到中文字体。用 --font 指定一个 .ttf/.ttc 文件")
    return None


def text_image(text: str, px: int, color=(255, 255, 255), opacity: float = 1.0,
               stroke_ratio: float = 0.08, font: str | None = None):
    """把文字画成透明底 PNG（RGBA），供图片加字用。"""
    from PIL import Image, ImageDraw, ImageFont

    path, idx = find_font(font)
    f = ImageFont.truetype(path, px, index=idx)
    stroke = max(1, int(px * stroke_ratio))
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = tmp.textbbox((0, 0), text, font=f, stroke_width=stroke)
    img = Image.new("RGBA", (r - l + 4, b - t + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((2 - l, 2 - t), text, font=f, fill=color + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    if opacity < 1:
        a = img.getchannel("A").point(lambda v: int(v * opacity))
        img.putalpha(a)
    return img


def fmt_ops(ops: list[dict]) -> str:
    parts = []
    for o in ops:
        p = o.get("参数") or {}
        brief = " ".join(f"{k}{v}" for k, v in p.items() if not isinstance(v, (list, dict)))
        parts.append(f"{o['名称']}{('(' + brief + ')') if brief else ''}")
    return "，".join(parts)


def stopwatch():
    t0 = time.time()
    return lambda: f"{time.time() - t0:.1f}s"
