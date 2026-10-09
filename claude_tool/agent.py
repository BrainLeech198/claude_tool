"""跟外部的 agent 程序打交道：找它、拼它要的参数。

"哪个 agent"由 agents.py 那张档位表说了算，这儿只认它递进来的 profile——
claude 和 codebuddy 的命令行旗标是同一套，所以参数拼法只有一份。
"把它拉起来"那一步不在这层：得挑终端模拟器、得摆窗口，是平台的事，在 host
门面后面（winhost.spawn_terminal / nixhost.spawn_terminal）。这一层只管拼出
一个两个平台都能用的参数表。
"""
import os
import shutil
import sys

from claude_tool import agents
from claude_tool.permissions import DEFAULT_PERMISSION

# 新开一扇控制台窗口 / 别开控制台窗口，都是 Windows 的 creationflags。非 Windows
# 上没有这两个开关，取 0 就是"不加任何创建标志"——subprocess 认这个值，界面代码
# 和探针就不用各自写一遍平台分支。
CREATE_NEW_CONSOLE = 0x00000010 if sys.platform == "win32" else 0
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _profile(profile):
    """没传档位就用默认那档（claude）。

    界面层真正该传的是"这本工作区选的那个 agent"，那要等它接到工作区上才轮到；
    在那之前一律走默认，行为跟以前单跑 claude 时一模一样。
    """
    return profile or agents.get(agents.DEFAULT_AGENT)


def _split_prefix(prefix):
    """把一个命令行前缀凑成列表。

    前缀可以是列表（codebuddy 那种 `[node, 脚本]`），也可以是单个字符串
    （claude 那种一个可执行文件）——两种都收，省得每个调用点自己判。
    """
    if prefix is None:
        return []
    return [prefix] if isinstance(prefix, str) else list(prefix)


def agent_args(profile=None, cont=False, prompt=None, settings=None,
               permission=None, append_system_prompt=None, exe=None):
    """给 agent 的参数表，一项一个元素，头几项是程序名。

    cont        接着该目录里最近一次会话聊。
    prompt      开场白，起来就先按这句干（不带就正常空会话）。
    settings    额外的 settings 文件路径，用来给它挂 hook；这个 agent 是把这份
                跟用户自己的 settings.json 合并，不会覆盖掉。
    permission  本次会话的权限等级；不在这档认的取值里就退回默认那档。
    append_system_prompt  追加到系统提示后面的内容（记忆互通靠它注入）。
    exe         命令行前缀。不传就自己去找（agent_exe）；找不到退回裸名字。要
                塞假的（探针）就从这儿塞，可以是列表也可以是单个字符串。

    Linux/macOS 那条路要的是这种形态：一个参数一个元素，交给终端模拟器，谁也
    不用猜哪里该断开。
    """
    profile = _profile(profile)
    mode = permission if permission in profile.permission_modes else DEFAULT_PERMISSION
    argv = _split_prefix(exe if exe is not None else agent_exe(profile))
    argv += ["--permission-mode", mode]
    if cont:
        argv.append("--continue")
    if settings:
        argv += ["--settings", settings]
    if append_system_prompt:
        argv += ["--append-system-prompt", append_system_prompt]
    if prompt:
        argv.append(prompt)
    return argv


# 交给 cmd /k 时，这些字符在命令行里会被 cmd 自己解释掉，得用引号圈起来：空格最
# 常见（路径、开场白里都有），`&|<>^()` 不圈会被当成命令分隔符或重定向。
_CMD_SPECIALS = set(' \t&|<>^()')


def _quote_for_cmd(token):
    """一个 token 进 cmd 命令行要不要包引号。"""
    if not token or any(ch in _CMD_SPECIALS for ch in token):
        return '"{}"'.format(token)
    return token


def agent_command(profile=None, cont=False, prompt=None, settings=None,
                  permission=None, append_system_prompt=None, exe=None):
    """拼给 cmd /k 的那条命令行（Windows 那条路用的）。

    参数的意思见 agent_args——两边是同一份参数表，这边只是把它渲染成一条
    字符串。

    拼进来的值（开场白、注入的记忆前言、settings 路径）**不能带双引号**：这段是
    直接往 cmd 命令行里塞的，值里带引号就把引号配对搅乱了。旗标、取值那几段是
    自己写死的字面量，按 _quote_for_cmd 看不会无谓地多包引号。

    返回的这条命令行交给 Popen 时必须整条当字符串传，不能摆进列表：Python 会
    再包一层引号、把里面的引号转义成 \\"，而 cmd 和 C 运行时不认 \\ 转义，开场白
    一到空格就被劈成好几个参数，只有头一段到得了对面那儿。
    """
    argv = agent_args(profile, cont=cont, prompt=prompt, settings=settings,
                      permission=permission, append_system_prompt=append_system_prompt,
                      exe=exe)
    return " ".join(_quote_for_cmd(item) for item in argv)


def find_agent(profile=None, which=shutil.which):
    """找这个 agent 的命令行前缀，返回 token 列表；找不到返回 None。

    返回值是**列表**不是字符串：codebuddy 是个 node 脚本、没有 Windows shim，
    前缀得是 `[node, 脚本]` 两个 token；claude 落在 PATH 上是 `[claude.cmd]`
    一个。谁要拿它显示给人看、或按路径认装法，用 agent_path。
    """
    return _profile(profile).find(which)


def agent_path(profile=None, which=shutil.which):
    """这个 agent 落地的那个可执行文件/脚本路径；找不到返回 None。

    专门给"显示给用户看"和"按路径认装法"用：前缀可能是 `[node, 脚本]` 两个
    token，真正代表"这个 agent 装在哪"的是最后那个。
    """
    prefix = find_agent(profile, which)
    return prefix[-1] if prefix else None


def agent_exe(profile=None, which=shutil.which):
    """命令行前缀；找不到就把裸名字当兜底（对方会从 PATH 再碰一次运气）。

    跟 find_agent 的差别只在"找不到时"：find_agent 返回 None 让调用方判"有没有
    装"，这儿返回一个能塞进 argv 的东西，让拼命令的那条路不必先判空。
    """
    profile = _profile(profile)
    return find_agent(profile, which) or [profile.exe_names[0]]


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
