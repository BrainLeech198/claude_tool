"""跟 claude 这个外部程序打交道：找它、拼它要的参数。

"把它拉起来"那一步不在这层：得挑终端模拟器、得摆窗口，是平台的事，在 host
门面后面（winhost.spawn_terminal / nixhost.spawn_terminal）。这一层只管拼出
一个两个平台都能用的参数表。
"""
import os
import shutil
import sys

from claude_tool.permissions import DEFAULT_PERMISSION, PERMISSION_VALUES

# 新开一扇控制台窗口 / 别开控制台窗口，都是 Windows 的 creationflags。非 Windows
# 上没有这两个开关，取 0 就是"不加任何创建标志"——subprocess 认这个值，界面代码
# 和探针就不用各自写一遍平台分支。
CREATE_NEW_CONSOLE = 0x00000010 if sys.platform == "win32" else 0


def claude_args(cont=False, prompt=None, settings=None, permission=None,
                exe="claude"):
    """给 claude 的参数表，一项一个元素，第一项是程序名。

    cont       接着该目录里最近一次会话聊。
    prompt     开场白，claude 起来就先按这句干（不带就正常空会话）。
    settings   额外的 settings 文件路径，用来给它挂 hook；Claude Code 是把这份
               跟用户自己的 ~/.claude/settings.json 合并，不会覆盖掉。
    permission 本次会话的权限等级，取值见 PERMISSION_MODES；不传就用默认那档。
    exe        用哪个 claude。Linux/macOS 那边传绝对路径过去——那边的终端会再
               起一层 shell，PATH 未必跟我们这份一样。

    Linux/macOS 那条路要的是这种形态：一个参数一个元素，交给终端模拟器，谁也
    不用猜哪里该断开。
    """
    mode = permission if permission in PERMISSION_VALUES else DEFAULT_PERMISSION
    argv = [exe, "--permission-mode", mode]
    if cont:
        argv.append("--continue")
    if settings:
        argv += ["--settings", settings]
    if prompt:
        argv.append(prompt)
    return argv


def claude_command(cont=False, prompt=None, settings=None, permission=None):
    """拼给 cmd /k 的那条命令行（Windows 那条路用的）。

    参数的意思见 claude_args——两边是同一份参数表，这边只是把它渲染成一条
    字符串。

    prompt 和 settings 这两个值会拼进 cmd 命令行，所以里面不能出现双引号；
    别的几段都是自己写死的字面量，加新的记得别带引号。

    返回的这条命令行交给 Popen 时必须整条当字符串传，不能摆进列表：Python 会
    再包一层引号、把里面的引号转义成 \\"，而 cmd 和 C 运行时不认 \\ 转义，开场白
    一到空格就被劈成好几个参数，只有头一段到得了 claude 那儿。
    """
    argv = claude_args(cont, prompt, settings, permission)
    # 要包引号的只有这两个（值里有空格），别的一个都不用包——包了反而变字面量。
    quoted = {value for value in (settings, prompt) if value}
    return " ".join('"{}"'.format(item) if item in quoted else item
                    for item in argv)


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
CLAUDE_NPM_PACKAGE = "@anthropic-ai/claude-code"


CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
