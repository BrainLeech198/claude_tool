"""交接文档：手动那个按钮，和挂在会话上的那个 Stop hook 本体。

做不到"用户喊停时往正在跑的会话里塞提示词"——Windows 上没法从外部给一个开着
的交互式会话投喂输入。能用的只有 Stop hook：claude 每把控制权交还给用户之前
会跑它，回一句 {"decision":"block","reason":...} 就能逼它再干一轮。两个功能都
架在这个杠杆上，各自一个开关：停下时顺手刷一份交接文档；停下时接着往下推，
别等用户（见 CONTINUE_PROMPT）。

所以这个 hook 是"每次它停下来时我们唯一能插手的地方"，要加新行为就往这儿加。
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

# 入口标记 + 每个行为一个开关。启动时按当时勾了哪些，把对应的开关拼进命令行；
# 勾选状态就此固化进那一份 settings，改勾不影响已经跑着的会话。
HOOK_FLAG = "--hook"
HANDOFF_FLAG = "--handoff"
CONTINUE_FLAG = "--auto-continue"


def hook_command(handoff=False, auto_continue=False):
    """拼给 Claude Code 的那条命令行。"""
    flags = [HOOK_FLAG]
    if handoff:
        flags.append(HANDOFF_FLAG)
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
HANDOFF_TOOLS = "Read,Write,Glob,Grep"
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

# 点工作区弹框里那个"先读交接文档"勾上时用的开场白
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

# ── 自动刷交接文档 ────────────────────────────────────────────────────────
# 做不到"用户喊停时往正在跑的会话里塞提示词"——Windows 上没法从外部给一个开着
# 的交互式会话投喂输入。能用的只有 Stop hook：claude 每把控制权交还给用户之前
# 会跑它，回一句 {"decision":"block","reason":...} 就能逼它再干一轮。
# 于是改成"每次它停下来就顺手刷一份"，用户随时关窗口，文档都是新的。
#
# 节流：距上次写够久、且又聊够了轮数才动手，否则每问一句都要写一遍文档。
HOOK_DIR = os.path.join(TOOL_DIR, "hooks")
HOOK_SETTINGS = os.path.join(HOOK_DIR, "hook.json")
# 老版本只干刷文档这一件事，配置叫这个名字、命令行上写的是 --handoff-hook。
# 现在两个行为共用一个入口，写新配置时顺手把它删掉——留着只会让人以为它还管用。
LEGACY_HOOK_SETTINGS = os.path.join(HOOK_DIR, "auto_handoff.json")
HANDOFF_COOLDOWN = 20 * 60
HANDOFF_MIN_TURNS = 3
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


def hook_fired_at(state, cwd):
    """这个目录上次"决定要刷交接文档"的时刻；没刷过是 0.0。

    只有真决定要刷的那一次才写 last（普通轮次只动 turns），所以这个值一变新，
    就说明那个会话的 hook 刚被逼着去写文档了。
    """
    entry = state.get(hook_state_key(cwd))
    if not isinstance(entry, dict):
        return 0.0
    return float(entry.get("last") or 0)


def note_handoff_written(cwd):
    """启动器自己写完了这个目录的交接文档，顺手把节流时间戳顶掉。

    不顶的话会撞成一个来回：用户点了「整理交接文档」，启动器 fork 的那个 claude
    正在写，这时候那个会话自己的 Stop hook 又够条件了，被逼出来的一轮往同一份
    handoff.md 上再写一遍，两份搅在一起。顶掉之后 20 分钟内 hook 不会再动。
    语义上也是对的——这份文档刚刷新过，本来就不该马上再刷。
    """
    state = read_hook_state()
    key = hook_state_key(cwd)
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    # 往条目里改而不是整个换掉：这个条目里还躺着自动继续那边的连计数，
    # 换掉就把它清了，一个刚被整理过文档的会话会因此又被催着往下干。
    entry.update({"turns": 0, "last": time.time()})
    state[key] = entry
    write_hook_state(state)


def _ensure_stdio():
    """sys.stdin / sys.stdout 是 None 的话，从原始 fd 接回来。

    实测过：--noconsole 的 exe 被管道拉起时（Claude Code 跑 hook 就是这么拉的），
    三个流 Python 都给赋了值，走不到这里。留着是防另一种情形——句柄没传进来时
    sys.stdout 会是 None，而 **print 到 None 是静默无操作**（CPython 认这个分支），
    于是 block 答复凭空消失、自动交接文档永远不触发，还一声不吭。这种漏法不报错，
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


def run_hook(handoff=False, auto_continue=False):
    """Stop hook 的本体。返回进程退出码。

    Claude Code 每轮把控制权交还给用户之前会跑一遍这个：stdin 给一段 JSON
    （含 cwd、stop_hook_active、last_assistant_message），stdout 的 JSON 就是
    答复。什么都不打印 = 放行。两个开关按启动时勾的从命令行传进来。

    stdin 必须按字节读、自己解 UTF-8：这台机器 locale 是 cp936，直接
    json.load(sys.stdin) 会拿 GBK 去解 UTF-8 字节，只要路径里有中文
    （工作区叫「冉」「小说」「2026数学建模」这种）就抛 UnicodeDecodeError，
    被下面的 except 吞掉，自动交接文档就永远不触发。
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
    # 已经在"被 hook 逼出来的那一轮"里了。刷文档那条得认这个标记，再拦就成了
    # 来回写同一份文件；自动继续那条**故意不认**——认了就等于一次对话最多只
    # 能续一轮，那这个功能等于没有（护栏改由 AUTO_CONTINUE_MAX 自己数）。
    forced = bool(payload.get("stop_hook_active"))

    if handoff and not forced:
        entry["turns"] = int(entry.get("turns") or 0) + 1
        last = float(entry.get("last") or 0)
        if (entry["turns"] >= HANDOFF_MIN_TURNS
                and now - last >= HANDOFF_COOLDOWN):
            # 该写了：把轮数清零并记下时间，免得紧接着的那一轮又被拦一次
            entry.update({"turns": 0, "last": now})
            state[key] = entry
            write_hook_state(state)
            _block(HANDOFF_PROMPT)
            return 0

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
    #
    # 刷文档那条路也靠这儿落盘：上面给 turns 加了 1 但够不到阈值就先不写，
    # 攒到下一次进来（entry 非空）才落盘。
    if auto_continue:
        entry["consec"] = consec
        if said_done:
            entry["consec_at"] = now
    if entry:
        state[key] = entry
        write_hook_state(state)
    return 0


def ensure_hook_settings(handoff=False, auto_continue=False):
    """写出 hook 用的 settings 文件，返回它的路径。

    不往 Claude Code 自己的 settings.json 里写东西——这份是启动时用 --settings
    传真上去的，Claude Code 会把它跟用户那份合并，用完即弃。两个开关按启动时勾了
    哪些拼进命令行；勾选状态就此固化，之后改勾不影响已经跑着的会话。

    顺手删掉老版本那份配置：它只干刷文档这一件事、命令行上写的是 --handoff-hook。
    现在两个行为共用一个入口，留着只会让人以为它还管用。
    """
    os.makedirs(HOOK_DIR, exist_ok=True)
    try:
        os.remove(LEGACY_HOOK_SETTINGS)
    except OSError:
        pass
    command = hook_command(handoff=handoff, auto_continue=auto_continue)
    payload = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": command}]}]}}
    with open(HOOK_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return HOOK_SETTINGS
