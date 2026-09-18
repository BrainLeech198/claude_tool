"""入口：--handoff-hook 走 hook，其余打开窗口。

被 Claude Code 的 Stop hook 回头调用时不能碰 tkinter、也不该弹任何窗口——
所以那条分支要在 import 主界面之前就分出去。
"""
import os
import sys

# 允许两种跑法：`python claude_tool\__main__.py`（开发/源码）和
# `python -m claude_tool`。前者的 sys.path[0] 是包目录本身，得先把它的上级
# 塞进去，包内那些 `from claude_tool.xxx import` 才找得到。
# 冻结成 exe 后 PyInstaller 自己管着包的查找，这里插进去的路径反而指向解包出来
# 的临时目录，没意义，跳过。
if __package__ in (None, "") and not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_tool.handoff import HOOK_FLAG, run_handoff_hook


def main():
    if HOOK_FLAG in sys.argv:
        return run_handoff_hook()
    from claude_tool.ui.launcher import Launcher
    Launcher().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
