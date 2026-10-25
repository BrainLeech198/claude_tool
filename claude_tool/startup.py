"""启动开关：不碰 tkinter，hook 和命令行那两条路也能安全 import。

「安全启动」= 忽略上次存的窗口位置，开在主屏中央。安装器把它挂到 exe 的右键
菜单和开始菜单上；源码跑的时候就是 `python -m claude_tool --reset-window`。
"""
import os

RESET_WINDOW_FLAG = "--reset-window"
RESET_WINDOW_ENV = "CLAUDE_TOOL_RESET_WINDOW"


def reset_window_requested():
    return bool(os.environ.get(RESET_WINDOW_ENV))
