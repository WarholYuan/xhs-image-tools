# 常用命令（直接复制）

先设一个变量，后面的命令都用它：

```bash
S=<skill目录>/scripts # 本 skill 的 scripts 目录，按实际安装位置改
```

PATH 里要有 `~/.local/bin`（uv 在那里）：`export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"`。

- 只处理图片，不处理视频和 Live 图。
- 路径可以是文件，也可以是文件夹（只读第一层）。可以一次给多个。
- 从访达把文件拖进终端，路径会自动填好。
- 加 `--seed 数字`，同一个数字每次出一样的结果。
- 加 `--out 文件夹`，输出放到指定位置。默认是 `<输入名>_去重/`。
- 默认写入新的手机拍摄信息（iPhone 或安卓），机型、时间、地点从 `references/meta_pool.json` 里挑。加 `--no-meta` 只去不写；加 `--devices "iPhone 16 Pro,iPhone 17 Pro"` 临时指定机型范围。
- 不写 `--level` 就是中度。

## 图片

```bash
# 默认中度，每张出 1 份
uv run $S/dedup_image.py run 图片文件夹

# 轻度，每张出 5 份
uv run $S/dedup_image.py run 图片文件夹 --level light --copies 5

# 中度 + 封面痛点字（| 换行）
uv run $S/dedup_image.py run 封面.jpg --level medium --text "黄皮必看|显白3件套" --text-pos top

# 模糊拓边成 3:4
uv run $S/dedup_image.py run 图.jpg --blur-extend 3:4

# 加 3% 垫图
uv run $S/dedup_image.py run 图片文件夹 --overlay 垫图文件夹 --overlay-opacity 3

# 只做调色，别的都关
uv run $S/dedup_image.py run 图.jpg --crop-pct 0 --no-rotate --no-resize
```

## 拼图

```bash
# 四宫格（4 张图）
uv run $S/dedup_image.py collage 1.jpg 2.jpg 3.jpg 4.jpg

# 三拼：左大右二 / 三竖条 / 三横条
uv run $S/dedup_image.py collage 1.jpg 2.jpg 3.jpg --layout 3
uv run $S/dedup_image.py collage 1.jpg 2.jpg 3.jpg --layout 3h

# 双拼：上下 / 左右；出 3 份，每份换格子顺序；加字
uv run $S/dedup_image.py collage 1.jpg 2.jpg --layout v2 --copies 3 --shuffle --text "对比一下"

# 糊底、间距 16
uv run $S/dedup_image.py collage 1.jpg 2.jpg 3.jpg 4.jpg --bg blur --gap 16
```

## 静态图加花字

```bash
# huazi.py 给静态图（实拍图、AI 图）加小红书花字，--out 必填
# 样式照小红书真实的字做，不是粗描边大字报；「|」换行，里的字用强调色，emoji 照常写（彩色）；英文自动换英文字体
# 没给 --pos 时自动避让：默认位置压到产品（画面中间）、边缘多、白字落在浅色上，就上下挪、左右换边到最近的空处
# 浅色背景：白字自动加深灰细描边、投影加深；深色字（ink、serif、film 自动变深时）加一圈淡白晕（白底字没描边看不清）
uv run $S/huazi.py 图.jpg --text "夯爆了～|好吃到跺脚🥹" --out 成图.jpg # soft 随手白字（默认）
uv run $S/huazi.py 图.jpg --style note --text "今天也要好好喝水|summer🍑" --out 成图.jpg # 手写便签
uv run $S/huazi.py 图.jpg --style big --text "夏日续命水|气泡感拉满" --out 成图.jpg # 黄字黑边大字报
uv run $S/huazi.py 图.jpg --style ink --text "这个酸甜度刚刚好|冰一下更好喝" --out 成图.jpg # 手写墨水字，浅底黑字深底白字
uv run $S/huazi.py 图.jpg --style film --text "夏天的风是苹果味的" --out 成图.jpg # 电影字幕
uv run $S/huazi.py 图.jpg --style serif --text "a slow afternoon|with apple kombucha" --out 成图.jpg # 杂志英文小字
uv run $S/huazi.py 图.jpg --style script --text "Sweet days|慢慢喝" --out 成图.jpg # 英文花体
uv run $S/huazi.py 图.jpg --style bubble --text "下午三点的续命水|0糖0脂" --out 成图.jpg # 消息卡片
uv run $S/huazi.py 图.jpg --style arc --text "Kombucha" --out 成图.jpg # 弧形英文
uv run $S/huazi.py 图.jpg --style shade --text "适合食补的印度姜黄粉|满满的姜黄素对女生很友好" --out 成图.jpg # 半透明黑底，整块
uv run $S/huazi.py 图.jpg --style label --text "⚠️一定要选未碱化可可粉|碱化处理过的可可含糖没营养❌" --out 成图.jpg # 每行白底条黑字
uv run $S/huazi.py 图.jpg --style song --text "取姜黄粉、肉桂粉各一小勺|燕麦奶+黑胡椒+油脂 叮热" --out 成图.jpg # 每行白纱底宋体
uv run $S/huazi.py 图.jpg --style ochre --text "品牌名+产品名" --out 成图.jpg # 每行棕黄底白字（某类产品可指定一半的图用它）
# 全部样式：soft、note、big、ink、film、serif、script、arrow、bubble、tape、arc、stamp，加 4 种底色字 shade、label、song、ochre
# 底色字的参考图：references/底色字参考/；产品笔记可在 soft / shade / label / song / ochre 5 种里轮换，某类产品可指定一半用 ochre
# 位置：--pos top / center / bottom / 0–1 小数（字块中心高度，给了就不自动避让）；--align left/center/right
# --size 字号（画面宽比例）；--color 样式主色
# 字体：优先用苹方；没有苹方的电脑自动改用冬青黑体
```

导入「照片」App：用 `imac-photo-folders` 的「把这些图导进照片」。去重输出文件夹可以整个给它，`manifest.json` 会被跳过。

## 写入新信息

```bash
# 默认：机型、拍摄时间、地点都从 references/meta_pool.json 里随机挑
uv run $S/dedup_image.py run 图片文件夹

# 只用这几个机型；拍摄时间在最近 7 天里
uv run $S/dedup_image.py run 图片文件夹 --devices "iPhone 16 Pro,iPhone 17 Pro" --meta-days 7

# 只用安卓（写列表里的「机型」名）
uv run $S/dedup_image.py run 图片文件夹 --devices "华为 Mate 90 Pro,小米 17 Pro Max"

# 只去不写
uv run $S/dedup_image.py run 图片文件夹 --no-meta

# 采集了新封面以后，重建机型资料
uv run $S/meta.py profiles <封面文件夹>
```

## 相似度、自测

```bash
# 单独比两张图
uv run $S/similarity.py 原图.jpg 新图.jpg

# 自测（测完测试文件夹移到废纸篓；加 --keep 保留）
bash $S/selftest.sh
```

## 只改拍摄信息（不去重）

```bash
# 只删：机型、时间、GPS、镜头参数都去掉，不写新的。JPEG / PNG / WebP 画面不动
uv run $S/exif.py strip 图片或文件夹

# 只写：画面不去重，写入新的手机拍摄信息
uv run $S/exif.py write 图片或文件夹

# 只写，指定机型
uv run $S/exif.py write 图片.jpg --devices "iPhone 17 Pro"
```
