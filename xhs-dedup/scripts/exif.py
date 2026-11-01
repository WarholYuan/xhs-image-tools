# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "pillow-heif", "numpy", "imagehash"]
# ///
"""只改图片的拍摄信息，不去重。只能在 Mac 上运行。只处理图片，不处理视频和 Live 图。

用法：
    uv run exif.py strip 图片/文件夹 ...   只删拍摄信息，不写新的
    uv run exif.py write 图片/文件夹 ...   只写新的拍摄信息，画面不做去重

strip：
    JPEG、PNG、WebP 不重新编码，画面一个像素不动，只删 EXIF（机型、时间、GPS、镜头参数）、XMP、IPTC、注释。
    色彩配置保留。iPhone JPEG 末尾附带的 HDR 增益图一起去掉。
    HEIC、TIFF、AVIF、BMP 没法原样删，转成 JPG（质量 95）。原图靠方向标记转正的，也先转正再存 JPG。
write：
    机型、拍摄时间、地点、镜头和曝光参数从 references/meta_pool.json、iphone_profiles.json 里挑，跟去重同一套。
    图片存成 JPG（质量 95，转 Display P3），不裁、不调色、不加任何去重操作。

输出在 <输入>_删信息/ 或 <输入>_写信息/，另写 manifest.json。原文件不动。
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402
import meta as M  # noqa: E402

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

LOSSLESS_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SUFFIX = {"strip": "_删信息", "write": "_写信息"}
TAG = {"strip": "删信息", "write": "写信息"}


# ---------------- 找输入 ----------------

def collect(inputs: list[str], suffix: str) -> list[tuple[list[pathlib.Path], pathlib.Path]]:
    """返回 [([文件], 输出文件夹)]，一个文件一项。文件夹只读第一层，只收图片。"""
    files: list[tuple[pathlib.Path, pathlib.Path]] = []
    for raw in inputs:
        p = pathlib.Path(raw).expanduser().resolve()
        if not p.exists():
            C.die(f"找不到：{p}")
        if p.is_dir():
            out = p.parent / f"{p.name}{suffix}"
            found = sorted(f for f in p.iterdir()
                           if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in C.IMAGE_EXT)
            if not found:
                print(f"! 文件夹里没有能处理的图片：{p}")
            files += [(f, out) for f in found]
        elif p.suffix.lower() in C.IMAGE_EXT:
            files.append((p, p.parent / f"{p.stem}{suffix}"))
        else:
            print(f"! 跳过（不是图片）：{p.name}")
    return [([f], out) for f, out in files]


def is_image(p: pathlib.Path) -> bool:
    return p.suffix.lower() in C.IMAGE_EXT


# ---------------- 原样删：JPEG / PNG / WebP ----------------

def strip_jpeg(data: bytes) -> bytes:
    """只留画面需要的段：SOI、APP0（JFIF）、APP2 里的色彩配置、APP14（Adobe 色彩转换）、量化表、霍夫曼表、帧头、扫描数据。
    删掉 APP1（EXIF、XMP）、其它 APP2（MPF）、APP3–APP13、APP15、注释。第一个 EOI 之后的附带图（HDR 增益图）不要。"""
    if data[:2] != b"\xff\xd8":
        raise ValueError("不是 JPEG")
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise ValueError(f"JPEG 结构不对（偏移 {i}）")
        while i < len(data) and data[i] == 0xFF:
            i += 1
        marker = data[i]
        i += 1
        if marker == 0xD9:
            out += b"\xff\xd9"
            return bytes(out)
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            out += bytes([0xFF, marker])
            continue
        seg_len = struct.unpack(">H", data[i:i + 2])[0]
        seg = data[i:i + seg_len]
        i += seg_len
        keep = True
        if marker == 0xE1 or marker == 0xFE or 0xE3 <= marker <= 0xED or marker == 0xEF:
            keep = False
        elif marker == 0xE2:
            keep = seg[2:14] == b"ICC_PROFILE\x00"
        if keep:
            out += bytes([0xFF, marker]) + seg
        if marker == 0xDA:  # 扫描数据：一直到下一个不是 00 / RST 的标记
            j = i
            while j < len(data) - 1:
                if data[j] == 0xFF and data[j + 1] != 0x00 and not (0xD0 <= data[j + 1] <= 0xD7):
                    break
                j += 1
            out += data[i:j]
            i = j
    raise ValueError("JPEG 没有结尾标记")


PNG_DROP = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def strip_png(data: bytes) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    if data[:8] != sig:
        raise ValueError("不是 PNG")
    out = bytearray(sig)
    i = 8
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        chunk = data[i:i + 12 + n]
        i += 12 + n
        if kind not in PNG_DROP:
            out += chunk
        if kind == b"IEND":
            break
    return bytes(out)


def strip_webp(data: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("不是 WebP")
    body = bytearray()
    i = 12
    while i + 8 <= len(data):
        kind = data[i:i + 4]
        n = struct.unpack("<I", data[i + 4:i + 8])[0]
        chunk = bytearray(data[i:i + 8 + n + (n & 1)])
        i += 8 + n + (n & 1)
        if kind in (b"EXIF", b"XMP "):
            continue
        if kind == b"VP8X":
            chunk[8] &= ~0x0C & 0xFF  # 去掉「有 EXIF」「有 XMP」两个标志
        body += chunk
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + bytes(body)


def orientation(src: pathlib.Path) -> int:
    from PIL import Image
    try:
        with Image.open(src) as im:
            return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def strip_image(src: pathlib.Path, out_dir: pathlib.Path, rnd: C.Rnd) -> tuple[pathlib.Path, str]:
    ext = src.suffix.lower()
    turned = orientation(src) != 1
    if ext in LOSSLESS_EXT and not turned:
        fn = strip_png if ext == ".png" else strip_webp if ext == ".webp" else strip_jpeg
        dst = C.next_path(out_dir, src.stem, TAG["strip"], src.suffix, [src])
        dst.write_bytes(fn(src.read_bytes()))
        return dst, "原样删，画面不动"
    import dedup_image as DI
    dst = C.next_path(out_dir, src.stem, TAG["strip"], ".JPG", [src])
    M.save_jpeg(DI.load(src), dst, rnd, 95)
    if turned:
        return dst, "原图靠方向标记转正，删了会躺倒，所以先转正再存 JPG（质量 95）"
    return dst, f"{ext[1:].upper()} 没法原样删，转成 JPG（质量 95）"


# ---------------- 核对 ----------------

def image_leftover(p: pathlib.Path) -> list[str]:
    """读出图片里还剩的拍摄信息：机型、时间、GPS、镜头。"""
    from PIL import Image
    left = []
    with Image.open(p) as im:
        ex = im.getexif()
        names = {0x010F: "厂商", 0x0110: "机型", 0x0132: "时间", 0xA434: "镜头"}
        left += [v for k, v in names.items() if k in ex]
        exif_ifd = ex.get_ifd(0x8769)
        if 0x9003 in exif_ifd:
            left.append("拍摄时间")
        if 0xA434 in exif_ifd:
            left.append("镜头")
        if ex.get_ifd(0x8825):
            left.append("GPS")
        if "xmp" in im.info or "XML:com.adobe.xmp" in im.info:
            left.append("XMP")
    return sorted(set(left))


# ---------------- strip ----------------

def cmd_strip(a) -> None:
    if sys.platform != "darwin":
        C.die("只能在 Mac 上运行")
    rnd = C.Rnd(a.seed)
    groups = collect(a.inputs, SUFFIX["strip"])
    man = C.Manifest("exif strip", {k: v for k, v in vars(a).items() if k != "func"}, rnd.seed)
    ok = fail = 0
    for files, default_out in groups:
        out_dir = C.out_dir_from(a.out, default_out)
        src = files[0]
        try:
            dst, how = strip_image(src, out_dir, rnd)
            left = image_leftover(dst)
            man.add(out_dir, {"原文件": str(src), "输出": dst.name, "做法": how, "剩下": left})
            print(f"  ✓ {dst.name}  {how}  剩下：{'、'.join(left) or '无'}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {src.name}：{e}")
            fail += 1
    for m in man.save():
        print(f"记录：{m}")
    print(f"完成：成功 {ok} 个，失败 {fail} 个。")
    sys.exit(0 if fail == 0 else 2)


# ---------------- write ----------------

def cmd_write(a) -> None:
    if sys.platform != "darwin":
        C.die("只能在 Mac 上运行")
    import dedup_image as DI
    rnd = C.Rnd(a.seed)
    groups = collect(a.inputs, SUFFIX["write"])
    M.model_range(a.devices)
    man = C.Manifest("exif write", {k: v for k, v in vars(a).items() if k != "func"}, rnd.seed)
    ok = fail = 0
    for files, default_out in groups:
        out_dir = C.out_dir_from(a.out, default_out)
        src = files[0]
        try:
            dev, t = M.pick_device(rnd, a.devices), M.shot_time(rnd, a.meta_days)
            dst = C.next_path(out_dir, src.stem, TAG["write"], ".JPG", [src])
            M.save_jpeg(DI.load(src), dst, rnd, 95, dev, t)
            written = M.describe(dev, t)
            man.add(out_dir, {"原文件": str(src), "输出": dst.name, "写入信息": written})
            print(f"  ✓ {dst.name}  {written.get('机型')}  {written.get('拍摄时间')}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {src.name}：{e}")
            fail += 1
    for m in man.save():
        print(f"记录：{m}")
    print(f"完成：成功 {ok} 个，失败 {fail} 个。")
    sys.exit(0 if fail == 0 else 2)


def main() -> None:
    ap = argparse.ArgumentParser(description="只改拍摄信息，不去重")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("strip", help="只删拍摄信息，不写新的")
    s.add_argument("inputs", nargs="+", help="图片或文件夹（只读第一层）")
    s.add_argument("--out", help="输出文件夹，默认 <输入>_删信息/")
    s.add_argument("--seed", type=int)
    s.set_defaults(func=cmd_strip)
    w = sub.add_parser("write", help="只写新的拍摄信息，画面不做去重")
    w.add_argument("inputs", nargs="+", help="图片或文件夹（只读第一层）")
    w.add_argument("--out", help="输出文件夹，默认 <输入>_写信息/")
    w.add_argument("--seed", type=int)
    w.add_argument("--devices", help="机型范围，逗号分隔；all 表示资料里全部机型。默认用 meta_pool.json 里的机型列表")
    w.add_argument("--meta-days", type=int, help="拍摄时间在最近几天里随机，默认用 meta_pool.json 里的天数")
    w.set_defaults(func=cmd_write)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
