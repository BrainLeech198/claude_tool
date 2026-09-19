"""跟 claude 这个外部程序打交道：找它、拼命令行、把它拉起来。

改启动方式（想换成内嵌终端、加参数、启动前先跑脚本），只需要动 launch()。
"""
import os
import shutil
import subprocess
import sys

from claude_tool.permissions import DEFAULT_PERMISSION, PERMISSION_VALUES

# 新开一扇控制台窗口，给"在新窗口里跑"那条路用
CREATE_NEW_CONSOLE = 0x00000010


def claude_command(cont=False, prompt=None, settings=None, permission=None):
    """拼给 cmd /k 的那条命令行。

    cont       接着该目录里最近一次会话聊。
    prompt     开场白，claude 起来就先按这句干（不带就正常空会话）。
    settings   额外的 settings 文件路径，用来给它挂 hook；Claude Code 是把这份
               跟用户自己的 ~/.claude/settings.json 合并，不会覆盖掉。
    permission 本次会话的权限等级，取值见 PERMISSION_MODES；不传就用默认那档。

    这里的 prompt 是拼进 cmd 命令行的，所以里面不能出现双引号——下面几个
    常量都是自己写的，加新的记得别带引号。

    返回的是一条给 cmd 直接咬的完整命令行：交给 Popen 时必须整条当字符串传，
    不能摆进列表（理由见 launch()）。
    """
    mode = permission if permission in PERMISSION_VALUES else DEFAULT_PERMISSION
    command = "claude --permission-mode " + mode
    if cont:
        command += " --continue"
    if settings:
        command += ' --settings "{}"'.format(settings)
    if prompt:
        command += ' "{}"'.format(prompt)
    return command


def claude_exe():
    """claude 的完整路径。直接写 "claude" 在 Windows 上未必解析得到。"""
    return shutil.which("claude") or "claude"


def find_claude():
    """找得到 claude 就返回完整路径，找不到返回 None。"""
    return shutil.which("claude")


def python_exe():
    """要一个带控制台的 Python。

    启动器自己是 pythonw.exe 拉起来的，而 pythonw 没有 stdout——hook 的输出
    全靠 stdout 回给 Claude Code，用 pythonw 跑等于把答复丢进黑洞。所以这里
    把 pythonw 换回旁边的 python。
    """
    exe = sys.executable or "python"
    if os.path.basename(exe).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


CLAUDE_INSTALL_URL = "https://docs.claude.com/en/docs/claude-code/setup"
CLAUDE_WINGET_ID = "Anthropic.ClaudeCode"


CREATE_NO_WINDOW = 0x08000000


def launch(workdir, cont=False, prompt=None, settings=None, permission=None):
    """在新控制台窗口里，以 workdir 为工作目录启动 claude。

    返回那个进程对象——调用方留着它轮询 poll()，就知道这个会话还开没开着。
    """
    # 这条命令行得整条当字符串交给 Popen。塞进列表的话 Python 会再包一层引号、
    # 把里面的引号转义成 \"，而 cmd 和 C 运行时不认 \ 转义：开场白一到空格就被
    # 劈成好几个参数，只有头一段到得了 claude 那儿。
    return subprocess.Popen(
        "cmd /k " + claude_command(cont, prompt, settings, permission),
        cwd=workdir,
        creationflags=CREATE_NEW_CONSOLE,
    )
