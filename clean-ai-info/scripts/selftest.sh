#!/usr/bin/env bash
# 清理 AI 信息 自测：造几份带 AI 标记的图，按 SKILL.md 的默认做法清理、再补一刀，逐项检查。
# 不联网（第一次没装 remove-ai-watermarks 时会先装）。用法：bash selftest.sh。通过时最后一行打印「自测通过」。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/clean-ai-selftest.XXXXXX")"
FAIL=0
ok()  { echo "  通过：$1"; }
bad() { echo "  失败：$1"; FAIL=$((FAIL + 1)); }
cleanup() { mv "$T" "$HOME/.Trash/$(basename "$T")-$(date +%H%M%S)" 2>/dev/null || true; }
trap cleanup EXIT
cd "$T" || exit 1

command -v uv >/dev/null || { echo "缺少 uv"; exit 1; }
command -v remove-ai-watermarks >/dev/null || uv tool install "remove-ai-watermarks[video]" >/dev/null

echo "1. 造素材"
uv run --quiet --no-project --with pillow python - <<'PY'
from PIL import Image, PngImagePlugin
im = Image.new("RGB", (512, 512), (120, 160, 200))
info = PngImagePlugin.PngInfo()
info.add_text("parameters", "a cat, Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Model: sd_xl_base_1.0")
im.save("sd.png", pnginfo=info)
xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
       '<rdf:Description xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
       'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"/>'
       '</rdf:RDF></x:xmpmeta>')
im.save("iptc.jpg", quality=92, xmp=xmp.encode())
ex = Image.Exif(); ex[0x0131] = "Midjourney v7"; ex[0x010E] = "prompt: a cat on a sofa"; ex[0x013B] = "ChatGPT image"
im.save("mj.jpg", exif=ex, quality=92)
PY
mkdir in && cp sd.png iptc.jpg mj.jpg in/
md5 -q in/* > before.md5
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
check "素材已生成" '[ -s sd.png ] && [ -s iptc.jpg ] && [ -s mj.jpg ]'

echo "2. 清理前能认出来"
check "生图参数（Stable Diffusion）" 'remove-ai-watermarks identify sd.png 2>&1 | grep -q "Stable Diffusion"'
check "IPTC 的 AI 来源" 'remove-ai-watermarks identify iptc.jpg 2>&1 | grep -q "digitalSourceType"'
check "生图软件名（Midjourney）" 'remove-ai-watermarks identify mj.jpg 2>&1 | grep -q "Midjourney"'

echo "3. 默认清理（文件夹 batch）+ 补一刀"
remove-ai-watermarks batch in --mode metadata >/dev/null 2>&1
check "输出在 in_clean/，3 个都在" '[ "$(ls in_clean | wc -l | tr -d " ")" -eq 3 ]'
uv run --quiet "$HERE/ai_text.py" check in_clean > before_fix.txt
check "补一刀前查得出工具漏掉的字样（提示词、ChatGPT）" 'grep -q "prompt" before_fix.txt && grep -q "ChatGPT" before_fix.txt'
uv run --quiet "$HERE/ai_text.py" fix in_clean > fix.txt
check "补一刀后再查：一处都没有" 'uv run --quiet "$HERE/ai_text.py" check in_clean >/dev/null'
for f in sd.png iptc.jpg mj.jpg; do
  check "$f 清完 identify 查不到标记" '! remove-ai-watermarks identify in_clean/'"$f"' 2>&1 | grep -qE "Stable Diffusion|digitalSourceType|Midjourney"'
done

echo "4. 画面没动、原文件没改"
check "三张图画面和原图一样" 'uv run --quiet --no-project --with pillow --with numpy python -c "
import sys, numpy as np
from PIL import Image
sys.exit(0 if all(np.array_equal(np.asarray(Image.open(\"in/\"+f)), np.asarray(Image.open(\"in_clean/\"+f))) for f in (\"sd.png\",\"iptc.jpg\",\"mj.jpg\")) else 1)"'
check "原文件 MD5 没变" '[ "$(md5 -q in/*)" = "$(cat before.md5)" ]'

echo
if [ "$FAIL" -eq 0 ]; then echo "自测通过"; exit 0; else echo "自测失败：$FAIL 项（临时文件夹已进废纸篓）"; exit 1; fi
