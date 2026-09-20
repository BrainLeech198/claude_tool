"""按 claude_tool/__init__.py 里的 __version__ 生成 build/_version.iss。

    python build/write_version.py

Inno 的预处理器读不懂 Python，中间垫一层：产物就一行

    #define AppVersion "0.2.0"

claude_tool.iss 用 #include 把它吃进来。这样版本号还是只有 claude_tool/__init__.py
一处写死（这次改动之前那处直接写在 .iss 里，Python 那边读不到）。

不用手动跑，build/打包.bat 开头会跑一次。产物不进版本库（见 .gitignore），所以
单跑 ISCC 之前得先跑这个——或者干脆照 .iss 头上说的，走打包.bat。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from claude_tool import __version__        # noqa: E402

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_version.iss")


def main():
    # 纯 ASCII 内容，用不用 BOM 都行；不带 BOM 是为了让别的没装 BOM 阅读器的
    # 工具（diff、编辑器）看起来干净。
    with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
        f.write('#define AppVersion "{}"\n'.format(__version__))
    print("build/_version.iss  <-  {}".format(__version__))


if __name__ == "__main__":
    main()
