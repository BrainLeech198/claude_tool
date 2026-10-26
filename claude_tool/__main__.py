"""入口：--hook 走 hook 那条路，子命令走命令行那条，其余打开窗口。

被 Claude Code 的 hook 回头调用时不能碰 tkinter、也不该弹任何窗口——所以那条
分支要在 import 主界面之前就分出去。开关（推着往下走 / 摁住危险动作 / 谁来答那
个选项框）按启动时写进 settings 的那份从命令行传进来，读法在 handoff.hook_options。

命令行那条同理：一个 tkinter 都不 import，没界面的机器上也能跑。它认得出的
东西全在 claude_tool/cli.py；返回 None 表示"这不是子命令"，那就照旧开窗口。
"""
import os
import sys
import time

# 允许两种跑法：`python claude_tool\__main__.py`（开发/源码）和
# `python -m claude_tool`。前者的 sys.path[0] 是包目录本身，得先把它的上级
# 塞进去，包内那些 `from claude_tool.xxx import` 才找得到。
# 冻结成 exe 后 PyInstaller 自己管着包的查找，这里插进去的路径反而指向解包出来
# 的临时目录，没意义，跳过。
if __package__ in (None, "") and not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_tool.handoff import HOOK_FLAG, hook_options, run_hook
from claude_tool.startup import RESET_WINDOW_ENV, RESET_WINDOW_FLAG


def _hand_off_to_running():
    """把已经在跑的那扇窗口叫到眼前：挪到鼠标所在的那块屏、再提到前台。

    第一次启动可能还在建窗（标题都还没挂上），按标题找不着就等一等，最多两秒；
    还找不到就认了——总比自己再开一扇强。
    """
    from claude_tool import host
    from claude_tool.startup import LAUNCHER_TITLE

    at = host.cursor_position()
    for _ in range(20):
        target = host.find_window(LAUNCHER_TITLE)
        if target is not None:
            if at is not None:
                host.focus_window_at(target, *at)
            else:
                host.focus_window(target)
            return
        time.sleep(0.1)


def main():
    if HOOK_FLAG in sys.argv:
        return run_hook(**hook_options(sys.argv))
    if RESET_WINDOW_FLAG in sys.argv:
        sys.argv.remove(RESET_WINDOW_FLAG)
        os.environ[RESET_WINDOW_ENV] = "1"
    from claude_tool import cli
    code = cli.main()
    if code is not None:
        return code
    # 只允许开一扇：第二扇不新建窗口，把已有那扇挪到鼠标所在的屏。hook 和命令行
    # 那两条不进这里——它们本来就不开窗，不该被这个挡掉。
    from claude_tool import host
    if not host.claim_singleton():
        _hand_off_to_running()
        return 0
    from claude_tool.ui.launcher import Launcher
    Launcher().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
