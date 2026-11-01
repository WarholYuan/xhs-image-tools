#!/usr/bin/env bash
# 小红书图片去重 自测：用 Pillow 生成测试图片，把图片去重、拼图、花字、只改拍摄信息都跑一遍，逐项检查。
# 用法：bash selftest.sh [--keep]
#   --keep  测完保留测试文件夹（默认测完移到废纸篓）
# 前提：clean-ai-info 与本技能同级安装（去重前要过它的检查）；测试图命名带 _clean 装作已清过。

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/xhs-dedup-test.XXXXXX")"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  ✓ $*"; }
bad()  { FAIL=$((FAIL + 1)); echo "  ✗ $*"; }
step() { echo; echo "== $* =="; }

# 检查：文件存在、MD5 和原文件不同
exists_diff() { # 输出 原文件
  if [ ! -s "$1" ]; then bad "没有输出：$1"; return 1; fi
  if [ "$(md5 -q "$1")" = "$(md5 -q "$2")" ]; then bad "MD5 和原文件一样：$(basename "$1")"; else ok "$(basename "$1") 已生成，MD5 和原文件不同"; fi
}

# 检查图片：第 2 个参数 meta 表示要有新写入的 iPhone 信息（用苹果自己的 ImageIO 读），否则要什么都没有
check_image() { # 文件 [meta]
  uv run --quiet --no-project --with pillow --with pyobjc-framework-Quartz python - "$1" "${2:-}" "$HERE/../references/iphone_profiles.json" "$HERE/../references/meta_pool.json" <<'PY'
import json, sys
from PIL import Image
f, mode, prof, pool = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
im = Image.open(f)
errs = []
if im.format != "JPEG":
    errs.append(f"格式是 {im.format}")
for k in ("xmp", "photoshop", "comment"):
    if im.info.get(k):
        errs.append(f"还有 {k}")
name = f.rsplit("/", 1)[-1]
if mode != "meta":
    if len(im.getexif()):
        errs.append("还有 EXIF")
    if im.info.get("icc_profile"):
        errs.append("还有 ICC")
    if errs:
        print(f"  ✗ {name}：" + "；".join(errs)); sys.exit(1)
    print(f"  ✓ {name}：{im.width}x{im.height}，没有 EXIF/ICC"); sys.exit(0)
import Quartz
from Foundation import NSURL
p = Quartz.CGImageSourceCopyPropertiesAtIndex(Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(f), None), 0, None)
tiff, exif, gps = dict(p.get("{TIFF}") or {}), dict(p.get("{Exif}") or {}), dict(p.get("{GPS}") or {})
import math
models = json.load(open(prof))["机型"]
andr = {m["Model"]: m for m in json.load(open(pool))["机型"]["列表"] if m.get("品牌", "Apple") != "Apple"}
sp = andr.get(tiff.get("Model")) if tiff.get("Make") != "Apple" else None
if sp:
    if tiff.get("Make") != sp["Make"] or tiff.get("Software") != sp.get("Software"):
        errs.append(f"品牌或 Software 不对：{tiff.get('Make')} {tiff.get('Software')}")
    need = ["DateTimeOriginal", "FNumber", "ExposureTime", "ISOSpeedRatings", "FocalLength", "FocalLenIn35mmFilm",
            "BrightnessValue", "ShutterSpeedValue", "ApertureValue", "PixelXDimension"]
    if sp.get("镜头名"):
        need.append("LensModel")
    if sp.get("时区字段", "两个") != "无":
        need.append("OffsetTimeOriginal")
    if exif.get("FNumber") not in sp["光圈"] or abs(exif.get("FocalLength", 0) - sp["焦距"]) > 0.01:
        errs.append(f"光圈 {exif.get('FNumber')} 或焦距 {exif.get('FocalLength')} 和规格对不上")
    # 曝光公式：光圈值 + 快门值 = 亮度值 + 感光值
    if all(k in exif for k in ("ApertureValue", "ShutterSpeedValue", "BrightnessValue", "ISOSpeedRatings")):
        sv = math.log2(exif["ISOSpeedRatings"][0] / 3.125)
        if abs(exif["ApertureValue"] + exif["ShutterSpeedValue"] - exif["BrightnessValue"] - sv) > 0.01:
            errs.append("光圈、快门、亮度、ISO 对不上曝光公式")
elif tiff.get("Make") != "Apple" or tiff.get("Model") not in models:
    errs.append(f"机型不对：{tiff.get('Make')} {tiff.get('Model')}")
elif tiff.get("Software") not in models[tiff["Model"]]["iOS"]:
    errs.append(f"iOS 版本 {tiff.get('Software')} 不在这个机型的资料里")
if not sp:
    need = ["DateTimeOriginal", "OffsetTimeOriginal", "LensModel", "FNumber", "ExposureTime", "ISOSpeedRatings",
            "FocalLength", "FocalLenIn35mmFilm", "BrightnessValue", "ShutterSpeedValue", "ApertureValue",
            "ExposureBiasValue", "LensSpecification", "SceneType", "PixelXDimension"]
miss = [k for k in need if k not in exif]
if miss:
    errs.append(f"苹果读不出：{miss}")
if not sp and not str(exif.get("LensModel", "")).startswith(f"{tiff.get('Model')} back"):
    errs.append(f"镜头 {exif.get('LensModel')} 和机型对不上")
if exif.get("PixelXDimension") != im.width or exif.get("PixelYDimension") != im.height:
    errs.append("EXIF 里的尺寸和图片不一样")
if p.get("ProfileName") != "Display P3":
    errs.append(f"颜色配置是 {p.get('ProfileName')}")
miss = [k for k in ("Latitude", "LatitudeRef", "Longitude", "LongitudeRef", "Altitude", "TimeStamp", "DateStamp",
                    "HPositioningError", "ImgDirection") if k not in gps]
if miss:
    errs.append(f"苹果读不出 GPS：{miss}")
elif not (gps["LatitudeRef"] == "N" and gps["LongitudeRef"] == "E" and 18 < gps["Latitude"] < 54 and 73 < gps["Longitude"] < 135):
    errs.append(f"GPS 不在国内：{gps.get('Latitude')} {gps.get('Longitude')}")
if errs:
    print(f"  ✗ {name}：" + "；".join(errs)); sys.exit(1)
bo = open(f, "rb").read(64).split(b"Exif\x00\x00", 1)[-1][:2].decode("latin1")
print(f"  ✓ {name}：{tiff['Make']} {tiff['Model']}（{bo}）系统 {tiff.get('Software', '无')}，{exif['DateTimeOriginal']}，"
      f"f/{exif['FNumber']} 1/{round(1 / exif['ExposureTime'])}s ISO {exif['ISOSpeedRatings'][0]}，"
      f"GPS {gps['Latitude']:.4f},{gps['Longitude']:.4f}，Display P3，苹果能读出全部字段")
PY
  if [ $? -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi
}

# 运行一个脚本，失败就记一笔
runpy() { # 描述 命令...
  local what="$1"; shift
  if "$@" > "$T/last.log" 2>&1; then ok "$what 运行成功"; else bad "$what 运行失败："; tail -5 "$T/last.log"; fi
}

command -v uv >/dev/null || { echo "✗ 缺少 uv"; exit 1; }
cd "$T" || exit 1

step "生成测试素材"
uv run --quiet --no-project --with pillow python - <<'PY'
from PIL import Image, ImageDraw
def make(w, h, c1, c2, path, fmt=None):
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        k = y / max(h - 1, 1)
        d.line([(0, y), (w, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * k) for i in range(3)))
    d.ellipse([w * 0.2, h * 0.2, w * 0.8, h * 0.6], fill=(240, 230, 200))
    d.rectangle([w * 0.3, h * 0.65, w * 0.7, h * 0.85], fill=(60, 90, 140))
    im.save(path, format=fmt) if fmt else im.save(path)
make(1080, 1440, (200, 120, 80), (80, 140, 200), "img_clean.jpg")
make(1080, 1080, (90, 170, 120), (230, 200, 90), "img2_clean.jpg")
make(1280, 720, (150, 150, 160), (60, 60, 90), "img3_clean.png", "PNG")
print("素材已生成")
PY
ls img_clean.jpg img2_clean.jpg img3_clean.png >/dev/null && ok "素材已生成（1080×1440 图等，命名带 _clean）"

I="$T/img_去重"
step "图片 轻度 ×2"
runpy "image light" uv run "$HERE/dedup_image.py" run img_clean.jpg --level light --copies 2 --seed 21
for n in 01 02; do exists_diff "$I/img_去重$n.jpg" img_clean.jpg && check_image "$I/img_去重$n.jpg" meta; done
if uv run --quiet --no-project --with pillow python - "$HERE/../references/meta_pool.json" "$I/img_去重01.jpg" "$I/img_去重02.jpg" <<'PY'
import json, sys
from PIL import Image
pool = {m.get("Model", m["机型"]) for m in json.load(open(sys.argv[1]))["机型"]["列表"] if m.get("用")}
# EXIF 文字按 UTF-8 写（华为典藏版型号有中文），Pillow 按 latin-1 读，要转回来
got = [Image.open(f).getexif().get(0x0110).encode("latin-1").decode("utf-8") for f in sys.argv[2:]]
print(f"  默认机型：{got}")
sys.exit(not all(g in pool for g in got))
PY
then ok "默认机型都在写入范围的列表里"; else bad "默认机型不在写入范围的列表里"; fi

step "图片 中度（加字 + 模糊拓边 + 垫图）"
runpy "image medium" uv run "$HERE/dedup_image.py" run img_clean.jpg --level medium --seed 22 \
  --text "痛点字|第二行" --blur-extend 3:4 --overlay img2_clean.jpg
exists_diff "$I/img_去重03.jpg" img_clean.jpg && check_image "$I/img_去重03.jpg" meta

step "图片 只去不写（--no-meta）+ 指定机型"
runpy "image no-meta" uv run "$HERE/dedup_image.py" run img_clean.jpg --seed 26 --no-meta
exists_diff "$I/img_去重04.jpg" img_clean.jpg && check_image "$I/img_去重04.jpg"
runpy "image 指定机型" uv run "$HERE/dedup_image.py" run img_clean.jpg --seed 27 --devices "iPhone 16 Pro"
exists_diff "$I/img_去重05.jpg" img_clean.jpg && check_image "$I/img_去重05.jpg" meta
if uv run --quiet --no-project --with pillow python -c "import sys;from PIL import Image;sys.exit(Image.open(sys.argv[1]).getexif().get(0x0110)!='iPhone 16 Pro')" "$I/img_去重05.jpg"; then ok "指定机型生效：iPhone 16 Pro"; else bad "指定机型没生效"; fi

step "安卓机型（图片：华为大端带中文型号、OPPO 小端带镜头名、荣耀写型号代码）"
n=6
for d in "华为 Mate 90 Pro Max 典藏版" "OPPO Find X9 Pro" "荣耀 Magic8 Pro" "三星 Galaxy S26 Ultra"; do
  runpy "image $d" uv run "$HERE/dedup_image.py" run img_clean.jpg --seed $((30 + n)) --devices "$d"
  exists_diff "$I/img_去重0$n.jpg" img_clean.jpg && check_image "$I/img_去重0$n.jpg" meta
  n=$((n + 1))
done

step "拼图（三拼、四宫格、双拼）"
runpy "collage 3" uv run "$HERE/dedup_image.py" collage img_clean.jpg img2_clean.jpg img3_clean.png --seed 23 --text 三拼
runpy "collage 4" uv run "$HERE/dedup_image.py" collage img_clean.jpg img2_clean.jpg img3_clean.png img_clean.jpg --bg blur --seed 24
runpy "collage h2" uv run "$HERE/dedup_image.py" collage img_clean.jpg img2_clean.jpg --layout h2 --seed 25
for f in img_拼图3_01.jpg img_拼图4_01.jpg img_拼图h2_01.jpg; do exists_diff "$I/$f" img_clean.jpg && check_image "$I/$f" meta; done

step "静态图加花字"
runpy "huazi ochre" uv run "$HERE/huazi.py" img_clean.jpg --style ochre --text "测试花字|第二行" --out "$T/huazi_out.jpg"
exists_diff "$T/huazi_out.jpg" img_clean.jpg

step "只改拍摄信息：只删（strip）"
E="$T/exif"; mkdir -p "$E"
cp "$I/img_去重01.jpg" "$E/phone.jpg"   # 带新写入的机型、时间、GPS
uv run --quiet --no-project --with pillow python - "$E/phone.jpg" "$E/rotated.jpg" <<'PY'
import sys
from PIL import Image
im = Image.open(sys.argv[1]); ex = im.getexif(); ex[0x0112] = 6
im.save(sys.argv[2], exif=ex, quality=92)
PY
runpy "exif strip" uv run "$HERE/exif.py" strip "$E/phone.jpg" "$E/rotated.jpg" --out "$E/删" --seed 41
uv run --quiet --no-project --with pillow --with numpy python - "$E/phone.jpg" "$E/删/phone_删信息01.jpg" "$E/删/rotated_删信息01.JPG" <<'PY'
import sys
import numpy as np
from PIL import Image
src, out, rot = sys.argv[1:4]
ok = True
if not np.array_equal(np.asarray(Image.open(src)), np.asarray(Image.open(out))):
    print("  ✗ 只删后 JPEG 画面变了"); ok = False
for f in (out, rot):
    ex = Image.open(f).getexif()
    if 0x0110 in ex or ex.get_ifd(0x8825) or 0x9003 in ex.get_ifd(0x8769):
        print(f"  ✗ {f} 还有机型 / GPS / 拍摄时间"); ok = False
if Image.open(rot).size != Image.open(src).size[::-1]:
    print("  ✗ 方向标记的图没转正"); ok = False
if ok:
    print("  ✓ JPEG 画面不动、机型 GPS 时间都删了；靠方向标记的图已转正")
sys.exit(0 if ok else 1)
PY
if [ $? -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi

step "只改拍摄信息：只写（write）"
runpy "exif write" uv run "$HERE/exif.py" write img_clean.jpg --out "$E/写" --seed 42
if uv run --quiet --no-project --with pillow python -c "import sys;from PIL import Image;ex=Image.open(sys.argv[1]).getexif();sys.exit(not(ex.get(0x0110) and ex.get_ifd(0x8825)))" "$E/写/img_写信息01.JPG"; then
  ok "图片只写：有机型和 GPS"; else bad "图片只写：没写进机型或 GPS"; fi

step "原文件没被改"
if [ -s img_clean.jpg ] && [ -s img2_clean.jpg ] && [ -s img3_clean.png ]; then ok "原文件都还在"; else bad "原文件不见了"; fi

step "相似度（0–64，0 表示几乎一样，越大差得越多）"
python3 - "$I/manifest.json" <<'PY'
import json, sys
for run in json.load(open(sys.argv[1]))["runs"]:
    for o in run["输出"]:
        s = o.get("相似度")
        name = o["输出"] if isinstance(o["输出"], str) else o["输出"][0]
        if not s:
            continue
        if "pHash" in s:
            txt = f"pHash {s['pHash']} / dHash {s['dHash']}"
        else:
            txt = "；".join(f"{k}: pHash {v['pHash']}" for k, v in s.items())
        print(f"  {run['工具']:<20} {name:<26} {txt}")
PY

echo
echo "结果：通过 $PASS 项，失败 $FAIL 项"
if [ $FAIL -eq 0 ] && [ $KEEP -eq 0 ]; then
  mv "$T" ~/.Trash/"xhs-dedup-test-$(date +%Y%m%d-%H%M%S)" && echo "测试文件夹已移到废纸篓"
elif [ $FAIL -ne 0 ]; then
  echo "测试文件夹保留在 ${T}，方便查看"
fi
[ $FAIL -eq 0 ]
