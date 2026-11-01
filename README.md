# xhs-image-tools

小红书图片工具，两个 skill，只处理图片（不处理视频和 Live 图）：

- **clean-ai-info** — 清理图片里的 AI 信息：删 AI 元信息（C2PA、EXIF、XMP、IPTC、PNG 生成参数、TC260 AIGC 标签），按需删画面上的 AI 标记（Gemini 星星、豆包、即梦、可灵、Sora、Veo 等），附补漏脚本 `ai_text.py`（清普通字段里的 AI 字样）与自测。
- **xhs-dedup** — 小红书图片去重：按轻度 / 中度两档做裁边放大、调色、镜像、模糊拓边、拼图、封面加痛点字、静态图加小红书花字（16 种样式）；默认去掉原拍摄信息，写入新的手机拍摄信息（iPhone 或安卓旗舰的机型、系统版本、拍摄时间、地点 GPS、镜头和曝光参数）。附档位出处 `references/levels.md`、命令集 `references/usage.md`、机型与地点池。也含「只改拍摄信息」模式（只删或只写）。

## 用法

两个文件夹并排放在一起（同级目录），保持这个相对位置：`xhs-dedup` 去重前会调用同级的 `clean-ai-info/scripts/ai_text.py` 做 AI 字样检查。各自的说明、依赖和命令都在自己的 `SKILL.md` 里。

## 环境

- clean-ai-info：需要 `uv`，主工具 `remove-ai-watermarks` 用 `uv tool install remove-ai-watermarks` 装。
- xhs-dedup：必须在 macOS 上跑（用系统 Display P3 配置文件与系统字体），只需要 `uv`，Python 依赖由各脚本头声明、`uv run` 自动装。
