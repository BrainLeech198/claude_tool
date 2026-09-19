"""交接文档，和挂在会话上的那个 Stop hook 本体。

交接文档这边只有一条路：**手动点**那个「整理交接文档」按钮，在目标目录里 fork
一份会话让它写 handoff.md。写出来的东西谁读？下一段会话起来时那句"先读
handoff.md"（见 READ_HANDOFF_PROMPT），以及换模型重开时同样那句。

本来还有第二条路——挂在会话上的 Stop hook 定时自动刷一份。砍掉了：claude 自己
的 `--continue` 已经能把上次的上下文整个接过来，那份文档只在"新模型从头读一遍旧
上下文"这种场合才值（见 _offer_migration）；而自动刷每次都要真烧一轮 token、还
往会话自己的上下文里再塞一坨，加速它撞上 compact。要文档就手点一下。

做不到"用户喊停时往正在跑的会话里塞提示词"——Windows 上没法从外部给一个开着
的交互式会话投喂输入。能用的只有 Stop hook：claude 每把控制权交还给用户之前
会跑它，回一句 {"decision":"block","reason":...} 就能逼它再干一轮。现在这个杠杆
上只剩一件事：停下时接着往下推，别等用户（见 CONTINUE_PROMPT）。
"""
import json
import os
import sys
import time

from claude_tool.paths import TOOL_DIR
from claude_tool.claude import python_exe

# 源码模式下 hook 要回头调的那个文件。不能写 handoff.py 自己——那样会被当成
# 脚本直接跑，包内的相对导入就全崩了；__main__.py 认得这个入口标记。
# 打包成 exe 之后这份源码不在盘上了，那时调的就是 exe 自己。
ENTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "__main__.py")

# 入口标记 + 开关。启动时按当时勾了哪些，把对应的开关拼进命令行；勾选状态就此
# 固化进那一份 settings，改勾不影响已经跑着的会话。
HOOK_FLAG = "--hook"
CONTINUE_FLAG = "--auto-continue"


def hook_command(auto_continue=False):
    """拼给 Claude Code 的那条命令行。"""
    flags = [HOOK_FLAG]
    if auto_continue:
        flags.append(CONTINUE_FLAG)
    tail = " ".join(flags)
    if getattr(sys, "frozen", False):
        return '"{}" {}'.format(sys.executable, tail)
    return '"{}" "{}" {}'.format(python_exe(), ENTRY_PATH, tail)


# ── 交接文档 ──────────────────────────────────────────────────────────────
# 手动按钮的做法：在目标目录里 fork 一份那个会话，让"分身"去写文档。fork 出来的
# 是新 session id，所以不会往用户原来的对话里塞进这么一轮。
# 不给 --dangerously-skip-permissions，只放这四个工具——写文档用不着跑命令。



HANDOFF_FILE = "handoff.md"
# Edit 在这儿是为了 .gitignore：追加一行比让 Write 整份重写它安全得多。
HANDOFF_TOOLS = "Read,Write,Edit,Glob,Grep"
HANDOFF_PROMPT = (
    "把这次会话的工作状态整理成一份交接文档，写到当前目录下的 "
    + HANDOFF_FILE + "（整份覆盖，不要追加）。"
    "读者是下一个接手的人或下一个 AI 会话，读完应该能直接接着干。写清楚："
    "1) 这个项目/任务的目标是什么；"
    "2) 已经做完并确认可用的部分，涉及哪些文件；"
    "3) 正在做但没做完的部分，卡在哪；"
    "4) 下一步做什么，按优先级排；"
    "5) 关键文件/目录的路径和各自作用；"
    "6) 踩过的坑、试过但不通的路子、以及任何没写进代码的约定。"
    "只写你确实知道的事，不确定的明确标注「不确定」。不要客套话。"
)

# 工作区是 git 仓库、且用户选了"不进版本管理"时，接在上面那段后面。
GITIGNORE_PROMPT = (
    "另外：这个目录是个 git 仓库。把 " + HANDOFF_FILE
    + " 加进 .gitignore（没有就新建一个），确认它不会被提交——它是工具生成的"
    "工作状态，不该进版本历史。除了 .gitignore 这一处，别动仓库里别的东西，"
    "也不要提交任何东西。"
)


def handoff_prompt(ignore_git=False):
    """写文档那句话，按"要不要进版本管理"拼上对应的尾巴。"""
    return HANDOFF_PROMPT + (GITIGNORE_PROMPT if ignore_git else "")


def is_git_repo(path):
    """这个目录是不是 git 仓库。

    拿 exists 而不是 isdir：worktree 和 submodule 的 .git 是个文件，不是目录，
    按 isdir 判会把这两样漏掉。
    """
    return os.path.exists(os.path.join(path, ".git"))


# 点工作区弹框里那个"先读交接文档"勾上时用的开场白；换模型重开时目录里要是有
# 这份文档，也用它
READ_HANDOFF_PROMPT = (
    "先读当前目录下的 " + HANDOFF_FILE + "，那是上次的交接文档。"
    "读完接着上面的进度继续干，已经做完的不要重做。"
)

# ── 自动继续 ──────────────────────────────────────────────────────────────
# 开了「什么都不问」也只是免掉工具确认，"要不要用 A 方案""这个删不删"这种它还是会
# 停下来等人。这个开关就是替用户接住这一下：停下时回一句"接着干，自己定"。
#
# 这一层故意不接第二个模型，就回一句固定的话——先量出来这条路能吃掉多少停顿，
# 值不值得再上一个"指挥模型"（见仓库根 设计说明.md 的「自主接手」）。
# 模型说这句就是收工了，别再去催它——不然一个做完的会话会被一直拖着往下编。
DONE_MARK = "已完成"
CONTINUE_PROMPT = (
    "接着干，别停下来等我。能自己定的就自己定（选哪个方案、先做哪块、要不要重构"
    "这种，按你的判断走，别回来问）；真拿不准的、或者要我拍板的，把问题写清楚"
    "再停下。整件事做完了，最后明确说一句「" + DONE_MARK + "」然后停。"
)

# 连着续这么多次就放行，让它真的停下来等人。平台自己也有个上限（文档说一次
# 停顿连着拦 8 轮就会被强制收尾），所以这个数不用比它大。
AUTO_CONTINUE_MAX = 8
# 距上次续超过这么久，就把连计数清零：中间隔了十分钟，要么是用户回来敲过字、
# 要么是模型自己在干长活，两种情况下都该重新开始帮它往前推。
# 这也顺带兜住了总量——不在这儿清的话，一个卡住的会话会被无限期地催下去。
AUTO_CONTINUE_RESET = 10 * 60

# ── hook 的配置文件和节流状态 ─────────────────────────────────────────────
HOOK_DIR = os.path.join(TOOL_DIR, "hooks")
HOOK_SETTINGS = os.path.join(HOOK_DIR, "hook.json")
# 老版本那份配置：只干刷文档这一件事，命令行上写的是 --handoff-hook。刷文档那条
# 路已经砍了，写新配置时顺手把它删掉——留着只会让人以为它还管用。
LEGACY_HOOK_SETTINGS = os.path.join(HOOK_DIR, "auto_handoff.json")
# 自动继续那个连计数按目录分开记在这儿。名字里带 handoff、文件名也没改，都是
# 历史遗留（它原来还装着刷文档的轮数和时刻）——改了就得去动用户盘上那份文件，
# 不值当。
HANDOFF_STATE = os.path.join(TOOL_DIR, "handoff_state.json")


def hook_state_key(cwd):
    """节流状态按目录分开记——一个会话算一份文档，不是全局一份。"""
    return os.path.normcase(os.path.normpath(cwd))


def read_hook_state():
    try:
        with open(HANDOFF_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_hook_state(state):
    try:
        os.makedirs(TOOL_DIR, exist_ok=True)
        with open(HANDOFF_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _ensure_stdio():
    """sys.stdin / sys.stdout 是 None 的话，从原始 fd 接回来。

    实测过：--noconsole 的 exe 被管道拉起时（Claude Code 跑 hook 就是这么拉的），
    三个流 Python 都给赋了值，走不到这里。留着是防另一种情形——句柄没传进来时
    sys.stdout 会是 None，而 **print 到 None 是静默无操作**（CPython 认这个分支），
    于是 block 答复凭空消失、自动继续永远不触发，还一声不吭。这种漏法不报错，
    查起来得不偿失。
    """
    if sys.stdin is None:
        sys.stdin = open(0, "rb", closefd=False)
    if sys.stdout is None:
        sys.stdout = open(1, "w", encoding="utf-8", errors="replace",
                          closefd=False)


def _stdin_bytes():
    """把 stdin 读成原始字节。两种流都认：文本流取 .buffer，二进制流直接读。"""
    stream = sys.stdin
    if stream is None:
        return b""
    return getattr(stream, "buffer", stream).read()


def _block(reason):
    """回一句"拦下来、带上这个理由"，让 claude 再干一轮。

    ensure_ascii 别改成 False：冻结后这个进程没有控制台，stdout 是按系统 locale
    （这台机器 cp936）开的文本流，纯 ASCII 的 \\uXXXX 转义才不挑编码，写成原文
    中文就有可能在那一头变成乱码。
    """
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=True))


def run_hook(auto_continue=False):
    """Stop hook 的本体。返回进程退出码。

    Claude Code 每轮把控制权交还给用户之前会跑一遍这个：stdin 给一段 JSON
    （含 cwd、stop_hook_active、last_assistant_message），stdout 的 JSON 就是
    答复。什么都不打印 = 放行。开关按启动时勾的从命令行传进来。

    这里**故意不认 stop_hook_active**——认了就等于一次对话最多只能续一轮，那这个
    功能等于没有（护栏改由 AUTO_CONTINUE_MAX 自己数）。

    stdin 必须按字节读、自己解 UTF-8：这台机器 locale 是 cp936，直接
    json.load(sys.stdin) 会拿 GBK 去解 UTF-8 字节，只要路径里有中文
    （工作区叫「冉」「小说」「2026数学建模」这种）就抛 UnicodeDecodeError，
    被下面的 except 吞掉，自动继续就永远不触发。
    """
    _ensure_stdio()
    try:
        payload = json.loads(_stdin_bytes().decode("utf-8", "replace"))
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    cwd = payload.get("cwd") or os.getcwd()
    key = hook_state_key(cwd)
    state = read_hook_state()
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    now = time.time()

    if auto_continue:
        said_done = DONE_MARK in str(payload.get("last_assistant_message") or "")
        consec = int(entry.get("consec") or 0)
        if now - float(entry.get("consec_at") or 0) >= AUTO_CONTINUE_RESET:
            # 中间隔了十分钟，上一串已经断了，重新开始数
            consec = 0
        if said_done:
            # 它自己说干完了。清零放行，别去催一个做完的会话往下编。
            consec = 0
        elif consec < AUTO_CONTINUE_MAX:
            consec += 1
            entry.update({"consec": consec, "consec_at": now})
            state[key] = entry
            write_hook_state(state)
            _block(CONTINUE_PROMPT)
            return 0

    # 能走到这儿 = 放行，让它真的停下来等用户。两种情形：它自己说了「已完成」，
    # 或者连着续到 AUTO_CONTINUE_MAX 了。前者把连计数清零再记下时刻；后者原样
    # 存回去——不存的话下一轮又从旧值往上数，上限永远到不了。
    if auto_continue:
        entry["consec"] = consec
        if said_done:
            entry["consec_at"] = now
    if entry:
        state[key] = entry
        write_hook_state(state)
    return 0


def ensure_hook_settings(auto_continue=False):
    """写出 hook 用的 settings 文件，返回它的路径。

    不往 Claude Code 自己的 settings.json 里写东西——这份是启动时用 --settings
    传真上去的，Claude Code 会把它跟用户那份合并，用完即弃。开关按启动时勾了哪些
    拼进命令行；勾选状态就此固化，之后改勾不影响已经跑着的会话。

    顺手删掉老版本那份配置：它只干刷文档这一件事、命令行上写的是 --handoff-hook。
    刷文档那条路已经砍了，留着只会让人以为它还管用。
    """
    os.makedirs(HOOK_DIR, exist_ok=True)
    try:
        os.remove(LEGACY_HOOK_SETTINGS)
    except OSError:
        pass
    command = hook_command(auto_continue=auto_continue)
    payload = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": command}]}]}}
    with open(HOOK_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return HOOK_SETTINGS
