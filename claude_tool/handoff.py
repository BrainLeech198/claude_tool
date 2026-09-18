"""交接文档：手动那个按钮，和自动刷文档的 Stop hook。

做不到"用户喊停时往正在跑的会话里塞提示词"——Windows 上没法从外部给一个开着
的交互式会话投喂输入。能用的只有 Stop hook：claude 每把控制权交还给用户之前
会跑它，回一句 {"decision":"block","reason":...} 就能逼它再干一轮。于是改成
"每次它停下来就顺手刷一份"，用户随时关窗口，文档都是新的。
"""
import json
import os
import sys
import time

from claude_tool.paths import TOOL_DIR
from claude_tool.claude import python_exe

# 源码模式下 hook 要回头调的那个文件。不能写 handoff.py 自己——那样会被当成
# 脚本直接跑，包内的相对导入就全崩了；__main__.py 认得 --handoff-hook。
ENTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "__main__.py")


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

# ── 自动刷交接文档 ────────────────────────────────────────────────────────
# 做不到"用户喊停时往正在跑的会话里塞提示词"——Windows 上没法从外部给一个开着
# 的交互式会话投喂输入。能用的只有 Stop hook：claude 每把控制权交还给用户之前
# 会跑它，回一句 {"decision":"block","reason":...} 就能逼它再干一轮。
# 于是改成"每次它停下来就顺手刷一份"，用户随时关窗口，文档都是新的。
#
# 节流：距上次写够久、且又聊够了轮数才动手，否则每问一句都要写一遍文档。
HOOK_DIR = os.path.join(TOOL_DIR, "hooks")
HOOK_SETTINGS = os.path.join(HOOK_DIR, "auto_handoff.json")
HOOK_FLAG = "--handoff-hook"
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


def run_handoff_hook():
    """Stop hook 的本体。返回进程退出码。

    Claude Code 每轮把控制权交还给用户之前会跑一遍这个：stdin 给一段 JSON，
    stdout 的 JSON 就是答复。什么都不打印 = 放行。

    stdin 必须按字节读、自己解 UTF-8：这台机器 locale 是 cp936，直接
    json.load(sys.stdin) 会拿 GBK 去解 UTF-8 字节，只要路径里有中文
    （工作区叫「冉」「小说」「2026数学建模」这种）就抛 UnicodeDecodeError，
    被下面的 except 吞掉，自动交接文档就永远不触发。
    """
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    # 已经在"被 hook 逼出来的那一轮"里了，再拦就成了死循环，放行
    if payload.get("stop_hook_active"):
        return 0

    cwd = payload.get("cwd") or os.getcwd()
    key = hook_state_key(cwd)
    state = read_hook_state()
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    turns = int(entry.get("turns") or 0) + 1
    last = float(entry.get("last") or 0)

    if turns < HANDOFF_MIN_TURNS or time.time() - last < HANDOFF_COOLDOWN:
        entry["turns"] = turns
        state[key] = entry
        write_hook_state(state)
        return 0

    # 该写了：把计数清零并记下时间，免得紧接着的那一轮又被拦一次
    state[key] = {"turns": 0, "last": time.time()}
    write_hook_state(state)
    print(json.dumps({"decision": "block", "reason": HANDOFF_PROMPT}))
    return 0


def ensure_handoff_hook_settings():
    """写出 hook 用的 settings 文件，返回它的路径。

    不往 Claude Code 自己的 settings.json 里写东西——这份是启动时用 --settings
    传真上去的，Claude Code 会把它跟用户那份合并，用完即弃。
    """
    os.makedirs(HOOK_DIR, exist_ok=True)
    command = '"{}" "{}" {}'.format(python_exe(), ENTRY_PATH, HOOK_FLAG)
    payload = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": command}]}]}}
    with open(HOOK_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return HOOK_SETTINGS
