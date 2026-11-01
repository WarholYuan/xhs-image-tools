---
name: clean-ai-info
description: 清理图片里的 AI 信息：删 AI 元信息（C2PA、EXIF、XMP、IPTC、PNG 生成参数、TC260 AIGC 标签），按需删画面上的 AI 标记（Gemini 星星、豆包、即梦、可灵、Sora、Veo 等）。只处理图片，不处理视频。用户说「清理/去掉 AI 信息」「去 AI 元信息」「删 AI 标签」「去 AI 水印标记」「查这张图是不是 AI 做的」，或给出图片/文件夹要求去 AI 痕迹时使用。不处理 SynthID 等看不见的像素水印。跟小红书去重一起做时，先跑这个、再去重（去重会写入新的手机拍摄信息，反过来会被这里删掉）。只删普通拍摄信息（机型、时间、GPS），不涉及 AI 的，用 xhs-dedup 的「只改拍摄信息」。
---

# 清理图片的 AI 信息（只处理图片）

底层工具：`remove-ai-watermarks`（github.com/wiltodelta/remove-ai-watermarks），用 uv 装。
没装 torch，所以不做 SynthID 等隐形水印。永远不要用 `invisible` 命令、`--invisible` 参数、`all` 或 `batch --mode all/invisible`。
本技能只处理图片；给到视频时告诉用户不在处理范围内，不要用这个技能硬处理。

## 0. 依赖（先检查环境）

在 Mac 上跑，`<skill目录>` 指本 skill 所在目录。命令前先 `export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"`。

**必需的外部工具**
- `uv`：装主工具、跑补一刀脚本都要它（一般在 `~/.local/bin`）。
- `remove-ai-watermarks`（主工具）：`uv tool install remove-ai-watermarks`。它自己的传递依赖（去画面标记用的 OpenCV 等）由 uv tool 一起装好，不用另外管。
- 不需要 `ffmpeg` / `ffprobe`（那是处理视频才用的，本技能不处理视频）。

**Python 依赖（不用手动装）**
- Python ≥ 3.11。补一刀脚本 `scripts/ai_text.py` 只依赖 `pillow`，写在脚本开头的 `# /// script` 声明里，`uv run` 第一次运行时自动下载（需联网）。

**明确不装、不需要**
- `torch`：不装。所以不做 SynthID 等隐形水印（见开头禁令）。

**自测额外依赖**：`scripts/selftest.sh` 还用 macOS 自带的 `md5 -q`，并用 `uv run --with pillow --with numpy` 临时环境比画面。

**跟其它技能的关系**
- 本技能可单独用，不依赖别的技能。反过来 `xhs-dedup` 依赖本技能与它同级安装（去重前强制调用本技能的 `ai_text.py` 检查），两边要装在一起时保持同级目录。

```bash
command -v uv
command -v remove-ai-watermarks || uv tool install remove-ai-watermarks
```

## 1. 分清要做什么

| 用户说的 | 做法 |
| --- | --- |
| 清理 AI 信息 / 元信息 / 标签（默认） | 只删元信息，画面一个像素不动 |
| 去水印 / 去标记 / 去星星 / 全部清掉 | 删画面上的 AI 标记 + 元信息（会重画那一小块） |
| 查一下是不是 AI 做的 | 只运行 identify，不改文件 |

拿不准就按默认：只删元信息。

**跟去重一起用**：用户既要去 AI 信息又要去重（`xhs-dedup`）时，顺序固定是先清 AI 信息、再去重。去重会写入新的手机拍摄信息；先去重再清，这些信息会被删掉。已经去重过的文件，用户再要清 AI 信息时，先告诉用户这一点再动。去重技能自己会先调这里，走去重流程时不用另外跑。

## 2. 输出规则

- 不改原文件。每次都输出新文件：`原名_clean.扩展名`，放在原文件旁边。
- 文件夹：输出到 `原文件夹_clean/`。
- 图片的 `metadata` 命令不加 `-o` 会覆盖原图，所以一定要加 `-o`。

## 3. 命令

先 identify，再处理，最后对新文件再 identify 一次验证。

**图片**（PNG/JPEG/WebP/AVIF/HEIF/JXL）

```bash
remove-ai-watermarks identify 图.png
remove-ai-watermarks metadata 图.png --remove -o 图_clean.png          # 默认
remove-ai-watermarks visible 图.png -o 图_clean.png                    # 去画面标记，元信息一起删
```

**文件夹**

```bash
remove-ai-watermarks batch 文件夹 --mode metadata                     # 默认
remove-ai-watermarks batch 文件夹 --mode visible                      # 去画面标记
```

文件夹里混有视频时只处理图片，视频跳过并在汇报里说明。

**补一刀（每次都做）**：这个工具只删它认得的标记（C2PA、生成参数、IPTC 的 AI 来源、生图软件名）。写在普通字段里的 AI 字样它不管，比如描述里的提示词、作者写 ChatGPT（2026-10-06 实测）。所以对输出再跑：

```bash
uv run <skill目录>/scripts/ai_text.py fix 图_clean.png 原文件夹_clean/
```

- 只删含 AI 字样的那几个字段：图片不重新编码，画面不动。
- 只对 `_clean` 输出跑，不碰原文件。只想看不想改用 `check`（有 AI 字样退出码 1）。

## 4. 汇报

用中文、短条目说清：
- 处理了几个文件，新文件在哪
- 删之前查到了什么（来源平台、哪些标记）
- 删之后再查的结果，补一刀删了哪些 AI 字样
- 提醒一句：工具查不到 SynthID 这类隐形水印，「没查到」不等于「完全干净」

## 已知情况

- 去画面标记默认用 cv2 补画，没有下载大模型，补的地方可能有点糊。
- 截图、转发、社交平台上传常会自己抹掉元信息，这时 identify 会显示 unknown。
- 自测：`bash <skill目录>/scripts/selftest.sh`。造几份带 AI 标记的图，跑清理和补一刀，查标记删没删、画面动没动、原文件改没改。
