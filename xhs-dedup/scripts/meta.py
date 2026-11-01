# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""写入新的拍摄信息：机型、iOS 版本、拍摄时间、地点（GPS）、镜头和曝光参数。只能在 Mac 上运行。

机型、拍摄时间、地点的范围在 references/meta_pool.json 里，每份从里面随机挑。
iPhone：镜头和曝光参数、iOS 版本来自 references/iphone_profiles.json：从采集的小红书封面里挑出 iPhone 真片，
只留后置主摄。挑好机型后随机挑这个机型的一张真片，
镜头、焦距、光圈、快门、ISO、亮度值成套照搬，所以参数互相对得上。
安卓：焦距、光圈按 meta_pool.json 里写的规格；快门、ISO、亮度按拍摄时间推算，三者按曝光公式互相对得上。
图片按各品牌的格式写（字节序、Exif 版本、色彩空间、时区字段、Software、镜头名）。

给其它脚本 import：
    pick_device / shot_time / save_jpeg（图片）
单独运行只用来重建机型资料：
    uv run meta.py profiles 封面文件夹
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import mmap
import pathlib
import re
import struct
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402

SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
PROFILES = SKILL_DIR / "references" / "iphone_profiles.json"
POOL = SKILL_DIR / "references" / "meta_pool.json"
P3_ICC = pathlib.Path("/System/Library/ColorSync/Profiles/Display P3.icc")
TZ = _dt.timezone(_dt.timedelta(hours=8))
TZ_TXT = "+08:00"

# 从真片照搬的 EXIF 字段（Exif 子目录里的标签号）
SHOT_TAGS = {
    0x829A: "ExposureTime", 0x829D: "FNumber", 0x8822: "ExposureProgram", 0x8827: "ISOSpeedRatings",
    0x9201: "ShutterSpeedValue", 0x9202: "ApertureValue", 0x9203: "BrightnessValue",
    0x9204: "ExposureBiasValue", 0x9207: "MeteringMode", 0x9209: "Flash", 0x920A: "FocalLength",
    0x9214: "SubjectArea", 0xA217: "SensingMethod", 0xA301: "SceneType", 0xA402: "ExposureMode",
    0xA403: "WhiteBalance", 0xA404: "DigitalZoomRatio", 0xA405: "FocalLengthIn35mmFilm",
    0xA406: "SceneCaptureType", 0xA432: "LensSpecification", 0xA433: "LensMake", 0xA434: "LensModel",
    0xA460: "CompositeImage",
}
VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")


# ---------------- 命令行开关（三个脚本共用） ----------------

def add_meta_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("写入新信息")
    g.add_argument("--meta", action=argparse.BooleanOptionalAction, default=True,
                   help="写入 iPhone 机型、拍摄时间、地点、镜头和曝光参数，默认开；--no-meta 只去不写")
    g.add_argument("--devices", help="机型范围，逗号分隔，比如 \"iPhone 16 Pro,iPhone 17 Pro\"；"
                                     "all 表示资料里全部机型。默认用 meta_pool.json 里的机型列表")
    g.add_argument("--meta-days", type=int, help="拍摄时间在最近几天里随机，默认用 meta_pool.json 里的天数")


# ---------------- 挑机型、定时间 ----------------

_cache: dict | None = None
_pool: dict | None = None


def load_profiles() -> dict:
    global _cache
    if _cache is None:
        if not PROFILES.exists():
            C.die(f"找不到机型资料 {PROFILES}。先运行：uv run meta.py profiles 封面文件夹")
        _cache = json.loads(PROFILES.read_text(encoding="utf-8"))
    return _cache


def load_pool() -> dict:
    global _pool
    if _pool is None:
        if not POOL.exists():
            C.die(f"找不到写入范围 {POOL}")
        _pool = json.loads(POOL.read_text(encoding="utf-8"))
    return _pool


def android_specs() -> dict[str, dict]:
    return {m["机型"]: m for m in load_pool()["机型"]["列表"] if m.get("品牌", "Apple") != "Apple"}


def model_range(spec: str | None, apple_only: bool = False) -> dict[str, float]:
    """返回 {机型: 权重}。没指定就用 meta_pool.json 里「用」为 true 的机型。
    apple_only：只要 iPhone 时用，安卓机型去掉。"""
    models = load_profiles()["机型"]
    andr = android_specs()
    if spec and spec.strip().lower() == "all":
        names = {n: 1 for n in models}
    elif spec:
        names = {s.strip(): 1 for s in spec.split(",") if s.strip()}
    else:
        names = {m["机型"]: m.get("权重", 1) for m in load_pool()["机型"]["列表"] if m.get("用")}
    if apple_only:
        names = {n: w for n, w in names.items() if n not in andr}
    if not names:
        C.die(f"没有可用的机型（{POOL} 里「用」为 true 的{'iPhone ' if apple_only else ''}机型是空的）")
    bad = [n for n in names if n not in models and n not in andr]
    if bad:
        C.die(f"没有这些机型的资料：{bad}。iPhone 可选：{', '.join(models)}；安卓可选：{', '.join(andr)}")
    return names


def pick_device(rnd: C.Rnd, spec: str | None, apple_only: bool = False) -> dict:
    """按权重挑机型。iPhone 挑一张真片和一个 iOS 版本；安卓按规格推算曝光。再挑一个地点。"""
    names = model_range(spec, apple_only)
    name = rnd.r.choices(list(names), weights=list(names.values()))[0]
    andr = android_specs()
    if name in andr:
        spec_ = andr[name]
        # 曝光要看拍摄时间，时间这时还没定，先存一个种子，写入时再算（同一份图和记录算出来一样）
        return {"机型": name, "品牌": spec_["品牌"], "规格": spec_, "iOS": spec_.get("Software"),
                "曝光种子": rnd.r.randrange(2**31), "地点": pick_place(rnd)}
    m = load_profiles()["机型"][name]
    vers, cnts = zip(*m["iOS"].items())
    return {"机型": name, "品牌": "Apple", "iOS": rnd.r.choices(vers, weights=cnts)[0],
            "参数": rnd.choice(m["真片"]), "地点": pick_place(rnd)}


def is_android(dev: dict | None) -> bool:
    return bool(dev) and dev.get("品牌", "Apple") != "Apple"


def shot_params(dev: dict, t: _dt.datetime) -> dict:
    """拍摄参数 {标签号: 值}。iPhone 用挑好的真片；安卓按拍摄时间推算一次，存回 dev。"""
    if "参数" not in dev:
        import random
        dev["参数"] = synth_exposure(random.Random(dev["曝光种子"]), dev["规格"], t)
    return dev["参数"]


def synth_exposure(r, sp: dict, t: _dt.datetime) -> dict:
    """按 APEX 曝光公式推算：光圈值 + 快门值 = 亮度值 + 感光值，四个数互相对得上。
    亮度值按几点随机：白天室外亮、室内暗，傍晚暗一些，晚上多是室内或夜景。
    手机优先低 ISO，快门慢于 1/30 秒才提 ISO。返回和 iPhone 真片同样格式的 {标签号: 值}。"""
    fn = r.choices(sp["光圈"], weights=[4] + [1] * (len(sp["光圈"]) - 1))[0]
    h = t.hour
    if h <= 16:
        bv = r.uniform(5.0, 9.5) if r.random() < 0.5 else r.uniform(1.5, 5.0)
    elif h <= 18:
        bv = r.uniform(1.0, 6.0)
    else:
        bv = r.uniform(-1.0, 3.0)
    av = 2 * math.log2(fn)
    iso = r.choice([50, 50, 64, 80, 100])
    tv = bv + math.log2(iso / 3.125) - av
    while tv < 5 and iso < 3200:  # 慢于 1/30 秒，ISO 往上提
        iso = min(3200, round(iso * 1.26))
        tv = bv + math.log2(iso / 3.125) - av
    tv = max(tv, 3.3)  # 最慢 1/10 秒
    den = max(1, round(2 ** tv))
    tv = math.log2(den)
    bv = av + tv - math.log2(iso / 3.125)
    r = lambda x, d=1000000: [round(x * d), d]
    return {
        "0x829a": [1, den], "0x829d": r(fn, 100), "0x8822": 2, "0x8827": iso,
        "0x9201": r(tv), "0x9202": r(av), "0x9203": r(bv), "0x9204": [0, 1], "0x9207": 2, "0x9209": 16,
        "0x920a": r(sp["焦距"], 1000), "0xa217": 2, "0xa402": 0, "0xa403": 0, "0xa405": sp["等效焦距"], "0xa406": 0,
        **({"0xa434": sp["镜头名"]} if sp.get("镜头名") else {}),
    }


def pick_place(rnd: C.Rnd) -> dict:
    """按权重挑一个区域，在中心点周围半径内均匀取一个点。"""
    cfg = load_pool()["地点"]
    areas = cfg["列表"]
    a = rnd.r.choices(areas, weights=[x.get("权重", 1) for x in areas])[0]
    dist = a["半径公里"] * math.sqrt(rnd.r.random())
    ang = rnd.r.uniform(0, 2 * math.pi)
    lat = a["纬度"] + dist * math.cos(ang) / 111.32
    lon = a["经度"] + dist * math.sin(ang) / (111.32 * math.cos(math.radians(a["纬度"])))
    lo, hi = cfg.get("定位精度米", [3, 35])
    return {"城市": a["城市"], "区域": a["区域"], "纬度": round(lat, 6), "经度": round(lon, 6),
            "海拔": round(max(0.0, a["海拔米"] + rnd.r.uniform(-15, 15)), 3),
            "精度": round(rnd.r.uniform(lo, hi), 6), "朝向": round(rnd.r.uniform(0, 360), 4)}


def shot_time(rnd: C.Rnd, days: int | None = None) -> _dt.datetime:
    """最近 days 天里的白天（北京时间），至少在 1 小时以前。天数和几点到几点默认取 meta_pool.json。"""
    cfg = load_pool()["拍摄时间"]
    days = days or cfg["最近天数"]
    h0, h1 = cfg["从几点"], cfg["到几点"]
    now = _dt.datetime.now(TZ)
    while True:
        d = now.date() - _dt.timedelta(days=rnd.int(0, max(0, days - 1)))
        t = _dt.datetime(d.year, d.month, d.day, rnd.int(h0, h1), rnd.int(0, 59), rnd.int(0, 59),
                         rnd.int(0, 999) * 1000, tzinfo=TZ)
        if t <= now - _dt.timedelta(hours=1):
            return t


def describe(dev: dict, t: _dt.datetime) -> dict:
    p = shot_params(dev, t)
    g = dev["地点"]
    return {"机型": dev["机型"], "品牌": dev.get("品牌", "Apple"), "系统": dev["iOS"],
            "拍摄时间": t.isoformat(timespec="milliseconds"),
            "地点": f'{g["城市"]} {g["区域"]}', "坐标": [g["纬度"], g["经度"]], "海拔": g["海拔"],
            "镜头": p.get("0xa434"), "等效焦距": p.get("0xa405"),
            "光圈": _num(p.get("0x829d")), "快门": _shutter(p.get("0x829a")), "ISO": p.get("0x8827")}


def _num(v):
    return round(v[0] / v[1], 2) if isinstance(v, list) and len(v) == 2 and v[1] else v


def _shutter(v):
    if not (isinstance(v, list) and len(v) == 2 and v[0] and v[1]):
        return v
    return f"1/{round(v[1] / v[0])}" if v[1] > v[0] else f"{v[0] / v[1]:g}"


# ---------------- 图片：Display P3 + EXIF ----------------

# EXIF 2.32 规定的类型。Pillow 会把带符号分数写成不带符号的，所以这里自己按表写。
BYTE, ASCII, SHORT, LONG, RATIONAL, UNDEFINED, SRATIONAL = 1, 2, 3, 4, 5, 7, 10
TAG_TYPE = {
    0x010F: ASCII, 0x0110: ASCII, 0x0112: SHORT, 0x011A: RATIONAL, 0x011B: RATIONAL, 0x0128: SHORT,
    0x0131: ASCII, 0x0132: ASCII, 0x8769: LONG, 0x8825: LONG,
    0x829A: RATIONAL, 0x829D: RATIONAL, 0x8822: SHORT, 0x8827: SHORT, 0x9000: UNDEFINED,
    0x9003: ASCII, 0x9004: ASCII, 0x9010: ASCII, 0x9011: ASCII, 0x9012: ASCII, 0x9101: UNDEFINED,
    0x9201: SRATIONAL, 0x9202: RATIONAL, 0x9203: SRATIONAL, 0x9204: SRATIONAL, 0x9207: SHORT,
    0x9209: SHORT, 0x920A: RATIONAL, 0x9214: SHORT, 0x9291: ASCII, 0x9292: ASCII, 0xA000: UNDEFINED,
    0xA001: SHORT, 0xA002: LONG, 0xA003: LONG, 0xA217: SHORT, 0xA301: UNDEFINED, 0xA402: SHORT,
    0xA403: SHORT, 0xA404: RATIONAL, 0xA405: SHORT, 0xA406: SHORT, 0xA432: RATIONAL, 0xA433: ASCII,
    0xA434: ASCII, 0xA460: SHORT,
}
# GPS 子目录的标签号和上面不重，单独一张表
GPS_TYPE = {
    0x0001: ASCII, 0x0002: RATIONAL, 0x0003: ASCII, 0x0004: RATIONAL, 0x0005: BYTE, 0x0006: RATIONAL,
    0x0007: RATIONAL, 0x000C: ASCII, 0x000D: RATIONAL, 0x0010: ASCII, 0x0011: RATIONAL, 0x0017: ASCII,
    0x0018: RATIONAL, 0x001D: ASCII, 0x001F: RATIONAL,
}


def _encode(typ: int, v, bo: str = ">") -> tuple[int, bytes]:
    """返回（个数，字节）。bo 是字节序：iPhone 是大端（MM，">"），有的安卓是小端（II，"<"）。
    文字按 UTF-8 写（华为典藏版的 Model 里有中文）。"""
    if typ == ASCII:
        b = str(v).encode("utf-8") + b"\x00"
        return len(b), b
    if typ == UNDEFINED:
        b = v if isinstance(v, bytes) else bytes.fromhex(v)
        return len(b), b
    vals = v if isinstance(v, list) and (typ not in (RATIONAL, SRATIONAL) or isinstance(v[0], list)) else [v]
    if typ == BYTE:
        return len(vals), bytes(int(x) for x in vals)
    if typ == SHORT:
        return len(vals), b"".join(struct.pack(bo + "H", int(x)) for x in vals)
    if typ == LONG:
        return len(vals), b"".join(struct.pack(bo + "I", int(x)) for x in vals)
    fmt = bo + ("ii" if typ == SRATIONAL else "II")
    return len(vals), b"".join(struct.pack(fmt, int(x[0]), int(x[1])) for x in vals)


def _tiff(ifd0: dict, exif: dict, gps: dict | None = None, bo: str = ">") -> bytes:
    """拼一个 TIFF 头 + IFD0 + Exif 子目录 (+ GPS 子目录)。数据放在各目录后面，按 2 字节对齐。"""
    def layout(tags: dict, start: int, types: dict = TAG_TYPE) -> tuple[bytes, bytes]:
        n = len(tags)
        data_off = start + 2 + 12 * n + 4
        entries, data = b"", b""
        for tag in sorted(tags):
            typ = types[tag]
            cnt, raw = _encode(typ, tags[tag], bo)
            if len(raw) <= 4:
                val = raw.ljust(4, b"\x00")
            else:
                val = struct.pack(bo + "I", data_off + len(data))
                data += raw + (b"\x00" if len(raw) % 2 else b"")
            entries += struct.pack(bo + "HHI", tag, typ, cnt) + val
        return struct.pack(bo + "H", n) + entries + b"\x00\x00\x00\x00", data

    ifd0 = {**ifd0, 0x8769: 0}
    if gps:
        ifd0[0x8825] = 0
    head0, data0 = layout(ifd0, 8)
    exif_off = 8 + len(head0) + len(data0)
    head1, data1 = layout(exif, exif_off)
    gps_off = exif_off + len(head1) + len(data1)
    ifd0[0x8769] = exif_off
    if gps:
        ifd0[0x8825] = gps_off
    head0, data0 = layout(ifd0, 8)  # 指针长度固定，重排一次只改这几个值
    head = b"MM\x00\x2a" if bo == ">" else b"II\x2a\x00"
    out = head + struct.pack(bo + "I", 8) + head0 + data0 + head1 + data1
    if gps:
        head2, data2 = layout(gps, gps_off, GPS_TYPE)
        out += head2 + data2
    return out


def _dms(x: float) -> list:
    """十进制度 → [[度,1],[分,1],[秒×100,100]]，iPhone 就这么存。"""
    x = abs(x)
    d = int(x)
    m = int((x - d) * 60)
    s = round(((x - d) * 60 - m) * 60 * 100)
    if s >= 6000:
        m, s = m + 1, s - 6000
    return [[d, 1], [m, 1], [s, 100]]


def build_gps(g: dict, t: _dt.datetime) -> dict:
    u = t.astimezone(_dt.timezone.utc)
    head = round(g["朝向"] * 10000)
    return {
        0x0001: "N" if g["纬度"] >= 0 else "S", 0x0002: _dms(g["纬度"]),
        0x0003: "E" if g["经度"] >= 0 else "W", 0x0004: _dms(g["经度"]),
        0x0005: 0, 0x0006: [round(g["海拔"] * 1000), 1000],
        0x0007: [[u.hour, 1], [u.minute, 1], [u.second * 100 + u.microsecond // 10000, 100]],
        0x000C: "K", 0x000D: [0, 1],
        0x0010: "T", 0x0011: [head, 10000], 0x0017: "T", 0x0018: [head, 10000],
        0x001D: u.strftime("%Y:%m:%d"), 0x001F: [round(g["精度"] * 1000), 1000],
    }


def build_exif(dev: dict, t: _dt.datetime, w: int, h: int) -> bytes:
    stamp = t.strftime("%Y:%m:%d %H:%M:%S")
    sub = f"{t.microsecond // 1000:03d}"
    ifd0 = {0x010F: "Apple", 0x0110: dev["机型"], 0x0112: 1, 0x011A: [72, 1], 0x011B: [72, 1],
            0x0128: 2, 0x0131: dev["iOS"], 0x0132: stamp}
    exif = {0x9000: b"0232", 0x9003: stamp, 0x9004: stamp, 0x9010: TZ_TXT, 0x9011: TZ_TXT, 0x9012: TZ_TXT,
            0x9101: b"\x01\x02\x03\x00", 0x9291: sub, 0x9292: sub, 0xA000: b"0100",
            0xA001: 65535,  # 带 Display P3 时，iPhone 写「未校准」
            0xA002: w, 0xA003: h}
    for k, v in shot_params(dev, t).items():
        exif[int(k, 16)] = v
    gps = build_gps(dev["地点"], t) if dev.get("地点") else None
    if not is_android(dev):
        return b"Exif\x00\x00" + _tiff(ifd0, exif, gps)
    # 安卓：按 meta_pool.json 里这个机型的格式改
    sp = dev["规格"]
    ifd0[0x010F], ifd0[0x0110] = sp["Make"], sp["Model"]
    if sp.get("Software"):
        ifd0[0x0131] = sp["Software"]
    else:
        ifd0.pop(0x0131)
    exif[0x9000] = {"HUAWEI": b"0210", "Xiaomi": b"0230", "HONOR": b"0230"}.get(sp["品牌"], b"0220")
    exif[0xA001] = sp.get("ColorSpace", 1)
    tz = sp.get("时区字段", "两个")
    if tz == "无":
        for k in (0x9010, 0x9011, 0x9012):
            exif.pop(k)
    elif tz == "只拍摄时间":
        exif.pop(0x9010)
        exif.pop(0x9012)
    else:
        exif.pop(0x9012)
    return b"Exif\x00\x00" + _tiff(ifd0, exif, gps, "<" if sp.get("字节序") == "II" else ">")


def to_display_p3(img):
    """像素从 sRGB 换算到 Display P3，再带上 P3 配置文件。看起来颜色不变。"""
    from PIL import ImageCms
    if not P3_ICC.exists():
        C.die(f"找不到 {P3_ICC}")
    srgb = ImageCms.createProfile("sRGB")
    p3 = ImageCms.getOpenProfile(str(P3_ICC))
    out = ImageCms.profileToProfile(img.convert("RGB"), srgb, p3, renderingIntent=ImageCms.Intent.PERCEPTUAL,
                                    outputMode="RGB")
    return out, P3_ICC.read_bytes()


def save_jpeg(img, path: pathlib.Path, rnd: C.Rnd, quality: int | None,
              dev: dict | None = None, t: _dt.datetime | None = None) -> int:
    """dev 为空：只存像素，不带任何附加信息。dev 有值：转 P3，写 EXIF。"""
    q = quality or rnd.int(90, 95)
    img = img.convert("RGB")
    img.info = {}
    kw = dict(quality=q, optimize=True, progressive=rnd.chance(0.5), subsampling=2)
    if dev:
        img, icc = to_display_p3(img)
        img.info = {}
        kw.update(exif=build_exif(dev, t, img.width, img.height), icc_profile=icc)
    img.save(path, "JPEG", **kw)
    return q


# ---------------- 重建机型资料 ----------------

def _raw(v):
    """读出来的 EXIF 值原样存：分数存成 [分子, 分母]。"""
    from PIL.TiffImagePlugin import IFDRational
    if isinstance(v, IFDRational):
        return [int(v.numerator), int(v.denominator)]
    if isinstance(v, tuple):
        return [_raw(x) for x in v]
    if isinstance(v, bytes):
        return v.hex()
    return v


def cmd_profiles(a) -> None:
    from PIL import Image
    root = pathlib.Path(a.folder).expanduser().resolve()
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in C.IMAGE_EXT)
    seen: set[str] = set()
    models: dict[str, dict] = {}
    n_apple = 0
    for p in files:
        try:
            h = hashlib.md5(p.read_bytes()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            ex = Image.open(p).getexif()
        except Exception:
            continue
        if ex.get(0x010F) != "Apple" or not ex.get(0x0110):
            continue
        n_apple += 1
        name = str(ex[0x0110]).strip()
        e = ex.get_ifd(0x8769)
        m = models.setdefault(name, {"iOS": {}, "真片": []})
        sw = str(ex.get(0x0131) or "").strip()
        if VERSION_RE.match(sw):
            m["iOS"][sw] = m["iOS"].get(sw, 0) + 1
        lens = str(e.get(0xA434) or "")
        spec = e.get(0xA432)
        fn = e.get(0x829D)
        main = spec and fn and abs(float(fn) - float(spec[2])) < 0.01  # 光圈最大的那颗就是主摄
        if not (lens.startswith(f"{name} back") and main and all(k in e for k in (0x829A, 0x8827, 0x9203))):
            continue
        m["真片"].append({f"0x{k:04x}": _raw(e[k]) for k in SHOT_TAGS if k in e})
    out = {}
    for name, m in sorted(models.items()):
        if not m["真片"] or not m["iOS"]:
            continue
        vers = {v: c for v, c in m["iOS"].items() if int(v.split(".")[0]) >= 26} or m["iOS"]
        out[name] = {"iOS": dict(sorted(vers.items(), key=lambda kv: -kv[1])), "真片": m["真片"]}
    data = {"说明": "iPhone 真片的拍摄参数，meta.py 写入新信息时从这里取值。只留后置主摄；iOS 只留 26 及以上（没有就全留）",
            "来源": str(root), "生成时间": _dt.datetime.now().isoformat(timespec="seconds"),
            "去重后图片": len(seen), "iPhone 真片": n_apple, "机型": out}
    PROFILES.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"图片 {len(seen)} 张（去重后），iPhone 真片 {n_apple} 张，可用机型 {len(out)} 个")
    for name in (m["机型"] for m in load_pool()["机型"]["列表"]):
        if name in out:
            print(f"  {name}：真片 {len(out[name]['真片'])} 张，iOS {', '.join(list(out[name]['iOS'])[:4])}")
        else:
            print(f"  {name}：资料里没有")
    print(f"写到：{PROFILES}")


def main() -> None:
    ap = argparse.ArgumentParser(description="重建 iPhone 机型资料")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profiles", help="从封面文件夹里挑 iPhone 真片，生成 references/iphone_profiles.json")
    p.add_argument("folder")
    p.set_defaults(func=cmd_profiles)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
