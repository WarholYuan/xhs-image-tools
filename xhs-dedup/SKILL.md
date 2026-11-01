---
name: xhs-dedup
description: 小红书图片去重，只处理图片，不处理视频和 Live 图。按课里的轻度、中度做法，默认中度、每个出 1 份，要几份出几份。做法包括裁边放大、调色、镜像、模糊拓边、拼图、封面加痛点字、静态图加小红书花字（16 种样式）。去重时默认去掉原来的信息，写入新的手机拍摄信息（iPhone 或安卓旗舰的机型、系统版本、拍摄时间、地点 GPS、镜头和曝光参数），机型、时间、地点从写入范围文件里挑；iPhone 的镜头和曝光参数照搬真片，安卓按官方规格推算。每份都记下参数和它跟原素材的相似度。用户说「去重」「过 MD5」「做几份不一样的」「拼四宫格」「封面加字」「图片加字」「给图片加花字」「写入机型」「换拍摄信息」「写 GPS」，或者给出图片、文件夹要求去重时使用。也管只改拍摄信息、不去重：「只删拍摄信息」「去掉 EXIF / GPS」「删机型和地点」「只写拍摄信息，不用去重」。导入 Apple 照片、组相册不归这里，归 imac-photo-folders。
---

# 图片去重（只处理图片）

## 什么时候用
- 用户给图片或文件夹，要做去重、出多份、拼图、加痛点字。给到视频或 Live 图时告诉用户不在处理范围内。
- 静态图加小红书花字（不去重）：`huazi.py`，命令见 `references/usage.md`。默认 `soft`（随手白字）。16 种样式（soft、note、big、ink、film、serif、script、arrow、bubble、tape、arc、stamp，加 4 种底色字 shade 半透明黑底、label 白底条黑字、song 白纱底宋体、ochre 棕黄底白字）照小红书真实的字做，底色字参考图在 `references/底色字参考/`。给产品笔记的实拍图、AI 图加字时，可在 soft、shade、label、song、ochre 这 5 种里轮换；**某类产品可指定一半图用 ochre**（一篇里先挑一半页用 ochre，其余页在另外 4 种里换）。红边大字报、色块标签、斜标题这类太生硬的样式已删。没给 `--pos` 时自动避让产品（上下挪、左中右换边，字蹭到产品一角也算压到）；浅色背景上的白字自动加深灰细描边，避免看不清。
- 只改拍摄信息、不去重：见下面「只改拍摄信息」。
- 不归这里的事：
  - 导入「照片」App、组相册、整理本地实拍素材：用 `imac-photo-folders`。去重好的图要拿去组稿，用它的「放进素材池」（`import-files <输出文件夹> --template <品名月.日>`），输出文件夹直接给它，记录文件会自动跳过。
  - 文案违禁词：用 `xhs-compliance`。

## 依赖（运行环境）
- **系统：必须是 macOS。** 转色用系统自带的 Display P3 配置文件（`/System/Library/ColorSync/Profiles/Display P3.icc`），加字用系统字体（苹方、冬青黑体、黑体、Apple Color Emoji），这些在别的系统上没有。命令前先 `export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"`。
- **外部命令：只有 `uv`。** 所有脚本都用 `uv run 脚本.py` 运行。不需要 ffmpeg / ffprobe（那是处理视频才用的，本技能只处理图片）。
- **Python 依赖：不用手动装。** Python ≥ 3.11；每个脚本开头 `# /// script` 里写明了自己的依赖，`uv run` 第一次运行时自动下载建环境（需联网，要等 1 分钟左右）。各脚本的依赖：
  - `dedup_image.py`：pillow、pillow-heif（开 HEIC）、numpy、imagehash
  - `exif.py`：pillow、pillow-heif、numpy、imagehash（经 `dedup_image.py` 用到后两项）
  - `meta.py`：pillow
  - `similarity.py`：pillow、pillow-heif、imagehash
  - `huazi.py`：pillow、pillow-heif
  - `common.py`：只用标准库，依赖由调用它的脚本声明。
- **兄弟技能：`clean-ai-info` 必须与本技能同级安装**（`<父目录>/clean-ai-info/scripts/ai_text.py`，脚本按相对位置找它）。`dedup_image.py run` 启动时强制检查输入过没过 clean-ai-info，没装会直接报错退出；确实要跳过才加 `--skip-ai-clean`。拼图 `collage` 和「只改拍摄信息」不走这道检查。
- **自测额外依赖**：`scripts/selftest.sh` 还用 macOS 自带的 `md5 -q`，校验环节临时用 `pyobjc-framework-Quartz`（经 `uv run --with` 临时装，用苹果 ImageIO 读回校验）。
- **数据文件：全在本 skill 文件夹里自带**（`references/meta_pool.json` 写入范围、`references/iphone_profiles.json` 机型资料），按脚本自己的位置找。不依赖外部服务、账号或 API key。

## 流程
1. 先去 AI 信息：调用 `clean-ai-info` skill（跟本技能同级的 `clean-ai-info/`），按它的默认做法处理素材。下面各步用它输出的 `_clean` 文件。输出名会自动去掉 `_clean`。
   - 脚本已强制这一步：`dedup_image.py run` 开始前检查输入是 `_clean` 输出（文件名或所在文件夹名带 `_clean`），并用 `clean-ai-info/scripts/ai_text.py check` 查 AI 字样，不符合就报错退出。确实要跳过才加 `--skip-ai-clean`。拼图（`collage`）没有这道检查。
2. 档位默认中度，每张图出 1 份，直接跑，不问。用户点名「轻度」「出 N 份」才改。档位见下表，细节见 `references/levels.md`。图片没有重度档（课里重度封面是 1:1 实拍，脚本做不了）。
3. 运行命令。命令照抄 `references/usage.md`。默认写入新信息，见下面「写入新信息」。
4. 清理中间文件：输出核对过以后，把第 1 步生成的 `_clean` 文件（或 `_clean/` 文件夹）移进废纸篓。原文件和输出不动。
5. 交付：输出文件夹路径、每份写入的机型和时间、每份的相似度、`manifest.json` 路径、清理了哪些中间文件。

| 档位 | 图片做什么 |
| --- | --- |
| light 轻度 | 裁 2–4%、微转、放大缩小、微调色 |
| medium 中度（默认） | 裁 4–8%、微转、放大缩小、调色加重；给了 `--text` 就加字。RGB 错位、颗粒 2026-10-20 起默认关（会出彩边光晕，对相似度没帮助），要用加 `--rgb-shift` / `--grain` |
| heavy 重度 | 无此档。课里重度封面是 1:1 实拍，脚本做不了；用中度或拼图 `collage` |

- 课里原话分到档的（封面部分）：
  - 轻度：封面过 MD5、放大、镜像、滤镜。
  - 中度：封面增减痛点字、AI 换背景（AI 换背景脚本没做）。
  - 重度：封面 1:1 实拍。
  - 其余（调色、微转、RGB 错位、颗粒）课里没分档，是技能自己分的。逐条见 `references/levels.md`。
- 课里怎么选档：轻度是「没有过1000单链接，自己刚开始测」；中度是「1000单以上链接，且3-5人打」、轻度流量明显不好；重度是「万单以上链接，打的人多」。
- 封面加字算哪档，课和教程说法不一样，写在 `references/levels.md` 末尾，没替你挑。
- `dedup_image.py --text` 用的苹方、冬青黑体都没有表情符号，画出来是方框（2026-10-16 实测「🥳」）。脚本会自动去掉表情，并打一行「已去掉：…」；要表情就用 `huazi.py` 的花字（支持彩色 emoji），或者用户自己在小红书里加。

## 输出
- 输出放在 `<输入名>_去重/`。输入已经在某个 `_去重/` 文件夹里时，输出放回同一个文件夹。
- 原文件不动。已有的输出不覆盖，编号往后排（`_去重01`、`_去重02`……）。
- 每次运行在输出文件夹的 `manifest.json` 里追加一条，写明：
  - 每份做了哪些操作、用了什么随机参数、随机种子。
  - 输出的 MD5 和原文件的 MD5。
  - 相似度：感知哈希的汉明距离，0–64。0 表示几乎一样，越大差得越多。图片是 pHash 和 dHash。
- 原文件的元数据全部去掉，再写入新信息。加 `--no-meta` 就只去不写。
- 图片输出是 JPEG。写新信息时颜色存成 Display P3，带 EXIF；`--no-meta` 时不带 EXIF、ICC 和注释。

## 只改拍摄信息（不去重）
用户说只删拍摄信息、去掉 EXIF / GPS，或只写拍摄信息、不用去重时走这条，不跑去重流程，也不先调 clean-ai-info（要清 AI 信息用户会说）。命令见 `references/usage.md`「只改拍摄信息」。
- `uv run scripts/exif.py strip <文件或文件夹>`：只删，不写新的。输出 `<输入名>_删信息/`。
  - JPEG、PNG、WebP 不重新编码，画面一个像素不动；色彩配置留着，iPhone JPEG 末尾的 HDR 增益图去掉。
  - 原图靠方向标记转正的（手机竖拍常见），删了会躺倒，所以先转正再存 JPG。HEIC、TIFF、AVIF、BMP 也转成 JPG（质量 95）。
- `uv run scripts/exif.py write <文件或文件夹>`：只写，画面不做任何去重操作。输出 `<输入名>_写信息/`。
  - 机型、时间、地点、镜头和曝光参数按下面「写入新信息」挑，`--devices`、`--meta-days` 照用。
  - 图片存成 JPG（质量 95，转 Display P3）。
- 每次在输出文件夹的 `manifest.json` 里记一条：删的记「剩下」还有什么（应该是「无」），写的记写入的机型、时间、地点。
- 交付：输出文件夹路径；删的每个文件「剩下」什么、哪些转成了 JPG；写的每份机型和时间。

## 写入新信息
- 机型、拍摄时间、地点都从 `references/meta_pool.json`（写入范围）里挑。要改范围就改这个文件，不改脚本。
- 机型：列表里「用」为 true 的按权重随机。现在是近一年左右上市、国行起售价 5000 元以上的手机：iPhone 17、Air、17 Pro、17 Pro Max，加 26 款安卓旗舰（华为 Mate 80 / 90、Pura 90，小米 17 / 18，vivo X300 / X500，OPPO Find X9 / X10，荣耀 Magic8 / 9，三星 S26 Ultra）。iPhone 18 Pro、18 Pro Max 在列表里，但还没有真片参数，先不用。`--devices` 临时改范围（写列表里的「机型」名，比如 `"小米 17 Pro Max"`），`--devices all` 用全部 iPhone 资料机型。
- iPhone：从 `references/iphone_profiles.json` 里随机挑这个机型的一张真片（从采集的小红书封面里挑出的 iPhone 真片，只留后置主摄）。镜头、焦距、等效焦距、光圈、快门、ISO、亮度值、测光、闪光灯成套照搬，所以参数互相对得上。iOS 版本从这个机型真片里出现过的版本里挑。
- 安卓：焦距、等效焦距、光圈（华为是可变光圈，几档里挑）按 `meta_pool.json` 里的官方规格。快门、ISO、亮度按拍摄时间推算：白天室外亮、室内暗，傍晚和晚上更暗；优先低 ISO，快门慢于 1/30 秒才提 ISO；四个数按曝光公式（光圈值 + 快门值 = 亮度值 + 感光值）互相对得上。格式按各品牌写：Make、Model（华为、小米、vivo、OPPO、三星写营销名，荣耀写型号代码）、Software（华为写「型号代码 版本号」，联发科芯片的 vivo / OPPO 写 MediaTek Camera Application，三星写固件号，小米、荣耀不写）、字节序（MM / II）、Exif 版本、色彩空间、时区字段、OPPO 的镜头名。备注里写了哪些是推断。
- 拍摄时间：最近 30 天（`--meta-days` 临时改）里的 9:00–21:59，北京时间 +08:00。
- 地点：按权重挑一个国内城市区域（北京、上海、广州、深圳、杭州、成都、重庆、武汉、南京、西安、长沙、苏州、郑州、厦门的市区），在中心点周围几公里内随机取一个点。写纬度、经度、海拔、定位精度、朝向、GPS 时间，坐标用 GPS 原始坐标（WGS-84）。
- 图片写：Make、Model、Software、DateTime、DateTimeOriginal、OffsetTime、SubsecTime、镜头和曝光参数、像素尺寸，大端字节序（MM），类型按 EXIF 2.32。颜色从 sRGB 换算到 Display P3，带 P3 配置文件。
- 没写的：Apple MakerNote、HostComputer。
- 更新机型资料：采集了新封面以后运行 `uv run scripts/meta.py profiles 封面文件夹`。

## 文件
- `scripts/dedup_image.py`：图片去重（`run`）和拼图（`collage`）。
- `scripts/exif.py`：只改拍摄信息、不去重：`strip` 只删，`write` 只写。
- `scripts/meta.py`：写入新信息（挑机型、推算安卓曝光、写 EXIF），也负责重建机型资料。
- `scripts/similarity.py`：算图片相似度。上面的脚本都调用它，也能单独用。
- `scripts/huazi.py`：静态图加小红书花字（16 种样式）。
- `scripts/common.py`：公用函数。
- `scripts/selftest.sh`：自测。改了脚本后运行 `bash scripts/selftest.sh`。
- `references/levels.md`：知识库做法 → 档位 → 开关 → 参数 → 出处。
- `references/usage.md`：可以直接复制的命令。
- `references/meta_pool.json`：写入范围，机型（含安卓规格和格式）、拍摄时间、地点从这里挑。
- `references/iphone_profiles.json`：iPhone 真片的拍摄参数，写入新信息时从这里取值。

## 出处
- 下面的出处路径都相对本地课程知识库目录。
- 知识库把两份去重教程列在附录索引的「去水印、过 MD5」类（`01_小红书/04_附录/附录_原文位置索引.md`）。附录 README 说，附录收的是违反平台规则或处于灰色地带的内容。`references/levels.md` 里这些出处标了〔附录〕。
- 两份教程都是一页导图，文字版顺序是乱的，哪句属于哪档要对照 `01_小红书/00_课程资料/06_内容制作/` 里的原件 PDF。
- 知识库里查不到的做法（开源做法、旧版自制工具），`references/levels.md` 里都标了「不是知识库来的」。
