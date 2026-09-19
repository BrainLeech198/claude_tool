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
import re
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
# 第 3 档多挂这个：认得出的危险动作（改 git 历史、删东西）先摁住，停下来等人。
HOLD_FLAG = "--hold"
# 第 2 档多挂这个，后面跟指挥模型那个预设的名字。
COMMANDER_FLAG = "--command-model"


def tier_flags(tier, commander=""):
    """档位 -> 一组开关。

    三个档共用"别停下来"这件事，差别只在多挂什么。这样每个开关只干字面那一件事
    （CONTINUE_FLAG 推着往下走、HOLD_FLAG 摁住危险动作、COMMANDER_FLAG 管谁来答），
    档位只是它们的组合——不另设一个"档"参数传进 hook，省得同一件事有两处说法。
    """
    return {"auto_continue": True,
            # 第 3 档是"第 1 档 + 摁住危险动作"，它不要指挥模型——写 tier >= 2
            # 会把一个用不上的名字挂上去，命令行里多一个看不懂的参数。
            "hold": tier >= 3,
            "commander": commander if tier == 2 else ""}


def autonomy_caption(flags):
    """托管那次实际挂的是哪一档，给界面上那行提示用。

    从开关反推档位，而不是把档号另传一份——档号和开关是同一件事的两种说法，
    传两份早晚会对不上。flags 就是 tier_flags() 的返回值。
    """
    if not flags:
        return "自动继续"
    if flags.get("hold"):
        return "第 3 档 · 半指挥"
    if flags.get("commander"):
        return "第 2 档 · 接指挥模型"
    return "第 1 档 · 让它自己定"


def safe_commander(name):
    """预设名要拼进命令行，带引号或换行的直接当没有。

    名字是用户在表单里敲的，表单那头挡过非法字符；但这是唯一一处把外部字符串拼进
    命令行的地方，多判一道——真拼进去一句 `" & del ...` 就是命令注入。
    """
    text = str(name or "")
    if any(bad in text for bad in "\"'\\&|<>^%\n\r"):
        return ""
    return text


def hook_options(argv):
    """从命令行读开关。__main__ 那条 hook 分支只干这一件事，读法摆在这儿跟开关定义挨着。"""
    options = {"auto_continue": CONTINUE_FLAG in argv, "hold": HOLD_FLAG in argv,
               "commander": ""}
    if COMMANDER_FLAG in argv:
        index = argv.index(COMMANDER_FLAG)
        if index + 1 < len(argv):
            options["commander"] = argv[index + 1]
    return options


def hook_command(auto_continue=False, hold=False, commander=""):
    """拼给 Claude Code 的那条命令行。"""
    flags = [HOOK_FLAG]
    if auto_continue:
        flags.append(CONTINUE_FLAG)
    if hold:
        flags.append(HOLD_FLAG)
    name = safe_commander(commander)
    if name:
        # 预设名里可能有空格（"智谱 GLM" 这种），带上引号
        flags.extend([COMMANDER_FLAG, '"{}"'.format(name)])
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

# ── 拦选项框（第 1 档的另一半） ────────────────────────────────────────────
# 它摆个选项框问人时，控制权根本没交回来——还停在一个回合里等工具返回，所以
# Stop 不触发，上面那套接不住。这半只能挂 PreToolUse：把那次调用拦下来，把
# 「你自己定」当理由还回去，模型读到理由就在同一个回合里接着干。
#
# 拦下来会不会让它原地再摆一遍？撞过了（_probe_askdeny.py）：不会。理由是
# 读得进去的，它自己定完接着往下写，一次都没重摆。倒是**放行**（什么都不回）
# 时更糟——平台回它一句 "Answer questions?"，它改用纯文字问人，白拦。
ASK_TOOL = "AskUserQuestion"
# 第 3 档要看的就是它的命令原文。
BASH_TOOL = "Bash"
ASK_DENY_PROMPT = (
    "用户现在不在电脑前，没人能点这个框。别摆选项了：按你自己的判断把该定的定下来，"
    "挑一个最像样的先做下去，直接接着干，也别再摆一次选项。"
)
# 连着挡这么多次就放行，退回今天这种"停在框上等人"。纯粹是保险——测出来的
# 行为是一次都不重摆，这个上限够不到；留着是防以后哪版模型改了脾气，别让一个
# 死循环把 token 烧干。判"连着"跟自动继续一个口径：隔久了就重新数。
ASK_DENY_MAX = 3

# ── 第 2 档：让另一个模型替用户拍板 ────────────────────────────────────────
# 副手本体在 commander.py，这儿只管把它答出来的东西包成一句理由。答不出来（返回
# None）就原样退回第 1 档那句「你自己定」——会话不能因为一个副手失灵就停下。
def commander_reason(picked):
    """picked 是 [(标题, 选择)]，来自 commander.ask()。"""
    lines = "；".join("「{}」选 {}".format(header, choice)
                      for header, choice in picked)
    return ("用户现在不在电脑前。另一个模型读过这段会话，替用户拍了板：{}。"
            "按这个往下做，别停下来等，也别再摆一次选项。".format(lines))


# ── 第 3 档：这几类先摁住，停下来等人 ──────────────────────────────────────
# 判据两条一起上（设计说明里定的）：认得出的具体命令在这儿拦，认不出的语义级大
# 动作（"把整个认证模块推倒重写"、"这几十个文件一起动一遍"）写进提示词让模型自己
# 判（见 HOLD_PARA）。前者准、后者广，谁也替代不了谁。
#
# 正则在命令原文上跑，别指望解析出「这条命令到底干了什么」——`git push --force` 写在
# 反引号里、写在 && 后面、写在 script 里，形态太多了。宁可误拦几条，也好过漏掉一条
# 真把历史改写掉的。误拦的代价只是问一句。
HOLD_PATTERNS = [
    (r"git\s+reset\s+[^&|;]*--hard", "改 git 历史：reset --hard"),
    (r"git\s+push\b[^&|;]*(--force\b|--force-with-lease\b|\s-f\b)", "强推：push --force"),
    # rebase 本身要拦，但 --abort / --continue / --skip 是"从 rebase 里出来"的
    # 那三个逃生口，拦它等于把人锁在里面。
    (r"git\s+rebase\b(?!\s+(--abort|--continue|--skip))", "改 git 历史：rebase"),
    (r"git\s+commit\b[^&|;]*--amend", "改已提交的历史：commit --amend"),
    (r"git\s+filter-branch\b", "改 git 历史：filter-branch"),
    (r"git\s+branch\b[^&|;]*\s-[a-zA-Z]*D", "强删分支：branch -D"),
    (r"git\s+tag\b[^&|;]*\s-[a-zA-Z]*d", "删标签"),
    # clean 的 -n / --dry-run 是空跑，得放过：要求短选项前面紧跟着空白，
    # 这样 "--dry-run" 里的 "d" 蹭不进来。
    (r"git\s+clean\b[^&|;]*\s-[a-zA-Z]*[fd]\b", "清理未跟踪的文件：git clean"),
    (r"git\s+(checkout|restore)\s+(--\s+)?\.", "丢弃工作区改动"),
    (r"\brm\s+(-[a-zA-Z]+\s+)*-[a-zA-Z]*(r|f)", "删东西：rm"),
    (r"\brm\s+[^&|;]*--(recursive|force)", "删东西：rm"),
    (r"\brmdir\s+/[a-zA-Z]*s", "递归删目录：rmdir /s"),
    (r"\bdel\s+/[a-zA-Z]*[sq]", "强删：del /s /q"),
    (r"remove-item\b[^&|;]*-[a-zA-Z]*(recurse|force)", "PowerShell 递归强删"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bdrop\s+(table|database)\b", "删库删表"),
]

# 摁住之后跟它说的话。**必须叫它别再换个说法把同一件事干了**——不然拦下来只是个
# 姿势，它绕一下照样做。
HOLD_PROMPT = (
    "这一步得让主人拍板，先停下。把你要做什么、为什么、会动到什么，写成一小段说"
    "清楚，然后停下等他回来。别自己换个说法绕过去，也别改用一个没被挡住的命令去"
    "做同一件事。"
)

# 第 3 档的提示词里比第 1 档多这一段：正则在命令上看不出来的那些大动作，只能靠
# 它自己按这条线判。
HOLD_PARA = (
    "另外这几类事必须先停下来等主人拍板，别自己决定：改 git 历史（rebase、"
    "reset --hard、push --force、动已经提交过的东西）、大范围重做（几十个文件一起"
    "改，或者推倒重来的那种重构）、删东西。碰上就把你要做什么写清楚，然后停下。"
)

# 摁住过的那几条命令。用户回来点头之后它会再发一次同一条，第二次得放行——不然
# "停下来等人"就变成"永远做不了"。
HELD_MAX = 5

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


def _deny(reason):
    """回一句"这次工具调用不许，理由在这儿"，让模型照理由自己往下走。

    新写法（permissionDecision）和老写法（decision:block）在这台机器的 2.1.150
    上撞出来一个样（_probe_askdeny.py 两种都跑了）。选新写法是因为它把决定藏在
    hookSpecificOutput 里，跟 Stop 那条用的 decision 字段不重名——同一个进程要
    认两种事件，字段各归各的，省得哪天读串了。

    ensure_ascii 的理由同 _block：冻结后这个进程没有控制台，stdout 是按 cp936
    开的，纯 ASCII 的 \\uXXXX 转义才不挑编码。
    """
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}, ensure_ascii=True))


def run_hook(auto_continue=False, hold=False, commander=""):
    """hook 的本体：Stop 那条和 PreToolUse 那条都从这儿进。返回进程退出码。

    三个开关都从命令行来（见 hook_options）。**别在这儿给参数加默认值以外的花样**
    ——hook 是被外部程序拉起来的子进程，签名对不上就是 TypeError、进程非零退出，
    而 Claude Code 那头只当这个 hook 没说话，静默退回它自己的行为。一路不报错，
    查起来得不偿失（这个坑探针当场抓到过一次）。

    Claude Code 每轮把控制权交还给用户之前会跑一遍这个：stdin 给一段 JSON
    （含 cwd、stop_hook_active、last_assistant_message），stdout 的 JSON 就是
    答复。什么都不打印 = 放行。开关按启动时勾的从命令行传进来。

    两个事件共用这一个入口、按 hook_event_name 分派（加行为别另开入口）。
    认不出来的事件一律按 Stop 走：万一哪版 Claude Code 不给这个字段，自动继续
    照旧能用，不会因为多认一个字段就静默失效。

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

    if payload.get("hook_event_name") == "PreToolUse":
        return run_pre_hook(payload, auto_continue, hold, commander)
    return run_stop_hook(payload, auto_continue, hold)


def run_pre_hook(payload, auto_continue=False, hold=False, commander=""):
    """PreToolUse 那条路，管两件事：它摆选项框问人，和第 3 档摁住危险动作。

    两件事挤在一个事件上，是因为 Claude Code 一次只给一个 payload——得先看这是
    哪个工具，再决定归哪件事管。
    """
    tool = payload.get("tool_name")
    if hold and tool == BASH_TOOL:
        return run_hold_hook(payload)
    if auto_continue and tool == ASK_TOOL:
        return run_ask_hook(payload, commander)
    return 0


def run_ask_hook(payload, commander=""):
    """它摆选项框问人的那半：拦下来，把「你自己定」当理由还回去。

    只认 AskUserQuestion 一个工具。matcher 那头已经卡在工具名上了，这儿再判
    一道不重复——matcher 认的是"哪些工具调用拉起这个 hook"，认不到"这次该答
    什么"，两层各管一件事。

    commander 非空时是第 2 档：让另一个模型读一遍会话替用户答，答出来了就用它的
    答案当理由。答不出来（超时、报错、选项对不上）原样退回第 1 档那句"你自己定"
    ——副手失灵不能把用户的目标卡死在半路。
    """
    if payload.get("tool_name") != ASK_TOOL:
        return 0

    reason = ASK_DENY_PROMPT
    if commander:
        try:
            # 迟到这儿才 import：这条路只有第 2 档走得到，别让第 3 档每次 Bash
            # 调用都跟着加载 urllib 和 presets。
            from claude_tool import commander as deputy
            picked = deputy.ask(commander,
                                (payload.get("tool_input") or {}).get("questions") or [],
                                payload.get("transcript_path") or "")
        except Exception:
            picked = None
        if picked:
            reason = commander_reason(picked)

    key = hook_state_key(payload.get("cwd") or os.getcwd())
    state = read_hook_state()
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    now = time.time()

    consec = int(entry.get("ask_consec") or 0)
    if now - float(entry.get("ask_at") or 0) >= AUTO_CONTINUE_RESET:
        consec = 0
    if consec >= ASK_DENY_MAX:
        # 挡够了，放行——让它照常摆框等人，别一直挡着。计数不清：这个窗口里
        # 接着问也一路放行，等隔久了再从头数。
        return 0

    entry.update({"ask_consec": consec + 1, "ask_at": now})
    state[key] = entry
    write_hook_state(state)
    _deny(reason)
    return 0


def hold_reason(command):
    """第 3 档：这条命令踩上哪一类了？没踩返回空串。

    在命令原文上跑正则，别指望解析出"这条命令到底干了什么"——它可能写在反引号里、
    写在 && 后面、写在脚本里，形态太多。宁可误拦几条，也好过漏掉一条真把历史改写
    掉的；误拦的代价只是问一句。
    """
    for pattern, why in HOLD_PATTERNS:
        # 不挑大小写：PowerShell 的命令名和参数本来就不分（remove-item -recurse），
        # SQL 关键字又习惯大写（DROP TABLE）。
        if re.search(pattern, command, re.IGNORECASE):
            return "这一步踩线了（{}）。".format(why)
    return ""


def _flatten(command):
    """同一条命令的两种写法（多一个空格、多一个换行）得认成同一条。"""
    return " ".join(str(command).split())


def run_hold_hook(payload):
    """第 3 档：认得出的危险命令先摁住，停下来等主人点头。

    "停下来"分两半：这儿把工具调用挡了（deny），Stop 那头还得别趁势再把它推起来
    （见 run_stop_hook 里那个 hold_stop）。缺一半就成了"拦下来又催它继续"，比不拦
    还乱。

    摁住过的那条命令记在状态里：用户回来点头之后它会再发一次同一条，第二次放行。
    不记的话"停下来等人"就变成"这件事永远做不成"。
    """
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command:
        return 0
    reason = hold_reason(command)
    if not reason:
        return 0

    key = hook_state_key(payload.get("cwd") or os.getcwd())
    state = read_hook_state()
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    flat = _flatten(command)
    held = [one for one in (entry.get("held") or []) if isinstance(one, str)]

    if flat in held:
        held.remove(flat)
        entry["held"] = held
        state[key] = entry
        write_hook_state(state)
        return 0

    entry["held"] = (held + [flat])[-HELD_MAX:]
    entry["hold_stop"] = True
    state[key] = entry
    write_hook_state(state)
    _deny(reason + HOLD_PROMPT)
    return 0


def run_stop_hook(payload, auto_continue=False, hold=False):
    """Stop 那条路：它干完一轮要把控制权交回来时，接一句"接着干"。

    返回进程退出码。节流状态按 cwd 分开记（见 hook_state_key）。

    hold 那次会多带一段提示词（HOLD_PARA），并且认一个额外的放行信号：刚才摁住过
    危险动作的话，这一轮**不能**再推它——这正是第 3 档和第 1、2 档反方向的地方。
    """
    cwd = payload.get("cwd") or os.getcwd()
    key = hook_state_key(cwd)
    state = read_hook_state()
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    now = time.time()

    if auto_continue:
        said_done = DONE_MARK in str(payload.get("last_assistant_message") or "")
        # 刚才摁住过危险动作（第 3 档）。这笔在这儿销掉：它已经停下来了，正是我们
        # 要的，别再推。存回去的那份 entry 里也就没这个键了。
        waiting = bool(entry.pop("hold_stop", False))
        consec = int(entry.get("consec") or 0)
        if now - float(entry.get("consec_at") or 0) >= AUTO_CONTINUE_RESET:
            # 中间隔了十分钟，上一串已经断了，重新开始数
            consec = 0
        if said_done or waiting:
            # 一个是它自己说干完了，一个是正等着主人对那件危险的事拍板。两种都得
            # 放行——尤其后者：拦下来又催它继续，等于白拦。
            consec = 0
        elif consec < AUTO_CONTINUE_MAX:
            consec += 1
            entry.update({"consec": consec, "consec_at": now})
            state[key] = entry
            write_hook_state(state)
            _block(CONTINUE_PROMPT + (HOLD_PARA if hold else ""))
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


def hook_settings_payload(auto_continue=False, hold=False, commander=""):
    """hook 配置的内容。

    单独抽出来是给探针用的：那边要拿**真配置**去撞行为，照着抄一份迟早会跟
    这儿对不上（少一个 matcher，诊断出来的结论就是假的）。
    """
    command = hook_command(auto_continue, hold, commander)
    entry = {"hooks": [{"type": "command", "command": command}]}
    # matcher 卡在工具名上，别用 "*"：那样每调一次工具都要起一个 python 进程，
    # 一个会话能白起几百个。Bash 那条只有第 3 档才加——第 1、2 档不看命令原文，
    # 挂上去就是白起进程（Bash 是调得最勤的工具）。
    matcher = ASK_TOOL + ("|" + BASH_TOOL if hold else "")
    return {"hooks": {
        "Stop": [entry],
        "PreToolUse": [{"matcher": matcher, "hooks": entry["hooks"]}],
    }}


def ensure_hook_settings(auto_continue=False, hold=False, commander=""):
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
    with open(HOOK_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(hook_settings_payload(auto_continue, hold, commander), f,
                  ensure_ascii=False, indent=2)
    return HOOK_SETTINGS
