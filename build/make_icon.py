"""把 build/icon.png 转成 build/icon.ico（一张图里塞五档尺寸）。

只在图换了之后手动跑一次。这里用 Pillow，但那是**构建期**的依赖——打包出来的
启动器本身仍旧零第三方依赖。生成的 icon.ico 直接提交进仓库，平时打包不重跑这个。

    python build/make_icon.py
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "icon.png")
DST = os.path.join(HERE, "icon.ico")

# Windows 会按场合自己挑：Alt+Tab 用大的，标题栏 16、任务栏 32、桌面 48。
SIZES = [256, 64, 48, 32, 16]


def main():
    img = Image.open(SRC).convert("RGBA")
    img.save(DST, format="ICO", sizes=[(s, s) for s in SIZES])
    print("wrote {} ({} bytes, sizes {})".format(
        DST, os.path.getsize(DST), SIZES))


if __name__ == "__main__":
    main()
