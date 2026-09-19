# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本：onedir + 无控制台。

用 spec 文件而不是一长串命令行参数，是因为"无控制台 + 图标 + exe 名字"这几项
必须一起固定下来，手敲容易漏。入口直接指 claude_tool/__main__.py——源码模式怎么
跑，打包出来就怎么跑，两条路走同一个 main()。

onedir 不 onefile：启动快（不用每次解包到临时目录），杀软误报也少。反正 Inno
装的就是一整个目录，分发形态没差别。

    python -m PyInstaller build/claude_tool.spec --noconfirm --clean ^
        --workpath build/_work --distpath dist

产物：dist/claude_tool/claude_tool.exe（外加 _internal/ 一堆运行时）

同一个 spec 两边都用：Linux 上是 build/打包.sh 调它，产物是不带 .exe 后缀的
dist/claude_tool/claude_tool。差在图标——非 Windows 上 PyInstaller 根本不认
icon=，递过去只会换来一句警告，所以这里按平台分叉，不递。
"""
import os
import sys

ROOT = os.path.dirname(SPECPATH)
WINDOWS = sys.platform == "win32"

a = Analysis(
    [os.path.join(ROOT, "claude_tool", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    # 窗口图标要跟着一起打包：exe 的那个 icon= 只管资源管理器里显示的样子，
    # tkinter 的标题栏/任务栏图标得自己在运行时拿这张 png 设（见 ui/launcher.py）。
    # 摊在包的根目录下，paths.ICON_FILE 按 sys._MEIPASS 找它。
    datas=[(os.path.join(SPECPATH, "icon.png"), ".")],
    # ui 那两个模块是在 main() 里面才 import 的（为了让 --hook 那条路
    # 完全不碰 tkinter）。PyInstaller 扫得到，但这里再写死一份——漏了就是打包出来
    # 才炸，而且炸在"点开没反应"上，不值当省这一行。
    hiddenimports=[
        "claude_tool.ui.launcher",
        "claude_tool.ui.dialogs",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="claude_tool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # 无控制台。这正是 hook 那套 stdio 要自己从 fd 0/1 接回来的原因，
    # 见 claude_tool/handoff.py 的 _ensure_stdio()。
    console=False,
    # 崩了弹个框把 traceback 显示出来。程序是要转发给别人的，静默闪退最难查。
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 非 Windows 上当 None 递进去，别递 .ico——PyInstaller 不认，只会回一句警告。
    icon=os.path.join(SPECPATH, "icon.ico") if WINDOWS else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="claude_tool",
)
