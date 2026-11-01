# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "pillow-heif", "imagehash"]
# ///
"""算输出和原素材的相似度（感知哈希的汉明距离，0–64）。

- 0 表示几乎一样，数字越大差得越多。
- 图片：pHash 和 dHash 各一个数。

单独用：
    uv run similarity.py 原文件 输出文件
"""

from __future__ import annotations

import pathlib
import sys

import imagehash
from PIL import Image, ImageOps


def _load_image(path: pathlib.Path) -> Image.Image:
    try:
        from pillow_heif import register_heif_opener  # type: ignore
        register_heif_opener()
    except ImportError:
        pass
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def image_hashes(img: Image.Image) -> tuple:
    return imagehash.phash(img), imagehash.dhash(img)


def image_distance(src, out) -> dict:
    """src/out 可以是路径或 PIL 图。"""
    a = src if isinstance(src, Image.Image) else _load_image(pathlib.Path(src))
    b = out if isinstance(out, Image.Image) else _load_image(pathlib.Path(out))
    pa, da = image_hashes(a)
    pb, db = image_hashes(b)
    return {"pHash": int(pa - pb), "dHash": int(da - db)}


def fmt_image(d: dict) -> str:
    return f"pHash {d['pHash']} · dHash {d['dHash']}"


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    a, b = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    print(fmt_image(image_distance(a, b)))
    print("（0–64：0 表示几乎一样，数字越大差得越多）")


if __name__ == "__main__":
    main()
