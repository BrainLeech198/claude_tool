#!/bin/sh
# 一把梭打包（Linux）：PyInstaller -> dist/claude_tool/，再压成 tar.gz，最后记进
# 官网那一页。跟 Windows 那边的 build/打包.bat 是一条流水线，只是这边没有 Inno
# 那一步——Linux 上不发安装包，发一个解开放着就能跑的目录。
#
# 产物：dist/claude_tool/                            解开放着就能跑
#       Output/ClaudeLauncher-<ver>-linux-<arch>.tar.gz   发出去的那个
#
# 要装的东西只有 PyInstaller 一样（跟 .bat 一样，纯打包工具，打出来的程序自己
# 还是只用标准库）。这台机器上装法是 build/_venv：
#
#     python3 -m venv build/_venv
#     build/_venv/bin/pip install pyinstaller
#
# _venv 存在就用它，不存在就用 PATH 上的 python3——都没有 PyInstaller 就当场报错
# 退出，不往下走。
#
# 这个文件名和里面的中文都无所谓：只有 cmd.exe 解析的 .bat 才必须 ASCII。
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PY=build/_venv/bin/python
[ -x "$PY" ] || PY=python3
if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
    echo "没找到 PyInstaller（试过 build/_venv/bin/python 和 python3）。" >&2
    echo "装一下：" >&2
    echo "    python3 -m venv build/_venv && build/_venv/bin/pip install pyinstaller" >&2
    exit 1
fi

# 版本号只写死在 claude_tool.iss 里一处（跟 Windows 共用那一份），这儿读出来拼
# 文件名。update_releases.py 自己也会再读一遍——同一处来源，各读各的。
VER=$(sed -n 's/^#define[[:space:]]*AppVersion[[:space:]]*"\(.*\)"/\1/p' \
          build/claude_tool.iss | head -1)
if [ -z "$VER" ]; then
    echo "build/claude_tool.iss 里没找到 AppVersion" >&2
    exit 1
fi
ARCH=$(uname -m)

echo "=== 1/3  PyInstaller ==="
"$PY" -m PyInstaller build/claude_tool.spec --noconfirm --clean \
      --workpath build/_work --distpath dist

echo
echo "=== 2/3  压成 tar.gz ==="
# -C dist 之后再点 claude_tool：解开是 claude_tool/ 一整个目录，跟 Windows 那边
# onedir 的形状一致，不至于一解开就把文件撒在当前目录里。
mkdir -p Output
TARBALL="ClaudeLauncher-${VER}-linux-${ARCH}.tar.gz"
rm -f "Output/$TARBALL"
tar -czf "Output/$TARBALL" -C dist claude_tool
echo "Output/$TARBALL  ($(du -h "Output/$TARBALL" | cut -f1))"

echo
echo "=== 3/3  docs release list ==="
# 往官网上记这一版。真正传到 Gitee 的发行版还是手动的，见那个脚本的开头。
"$PY" build/update_releases.py linux

echo
echo "完事。产物在 $ROOT/Output"
