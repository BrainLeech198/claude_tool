"""Claude Code 启动器：模型切换 + 工作区切换，单文件。

上半部分：扫描 ~/.claude_tool/claude_settings/*.json 作为模型预设，点击即替换
~/.claude/settings.json。下半部分：工作区列表，点一下就在那个目录开窗口跑 claude。
两块互不干扰，启动前随时换模型。

自己的东西全放在 ~/.claude_tool/ 下面，跟 Claude Code 的配置分开：
    launcher.json     工作区列表、窗口位置、默认工作区目录
    claude_settings/  模型预设，一个模型一个 json
    workplace/        默认工作区目录，"＋ 新建文件夹"就在这儿建文件夹

顶栏：当前模型（预设认得出来才是强调色的胶囊）、claude 的版本号、以及
      "打开配置目录"。版本号是后台问一次 claude --version，不占启动时间。
加模型：点界面上的"＋ 添加"，从"常用供应商"下拉里挑一家（国内四家 + 国外六家），
地址和模型名自动填好，你只要贴自己的 key。挑不到就选"（自己填）"手填。
加工作区：两种来源，所以是两个按钮——"＋ 新建文件夹"在默认工作区目录里建一个新的；
         "＋ 选已有目录"把已经存在的目录挂进来。两者都只是往列表里加一条。
换默认工作区目录：工作区列表顶上那行的"更改目录"。
工作区每行右侧：打开（资源管理器）、↑ ↓（在列表里挪位置，顺序就是 Ctrl+1~9 的顺序）、
               改名、移除（只从列表拿掉，不动硬盘上的目录）。右边那格会写
               "有没有交接文档"和"上次聊是多久以前"，后者来自
               ~/.claude/projects 里这个目录的会话记录。
点工作区先问一句：开新会话还是接着上次聊，权限等级选哪档——权限按工作区记住。
交接文档：从这个启动器开出去的窗口，只要还开着，顶上"正在跑"那块就会列出来，
         每条带一个"整理交接文档"——它会 fork 一份那份会话，让它写 handoff.md。
         窗口一关，那条自己就没了；一个都没开的时候整块不显示。
没装 claude 时顶上会挂一条提示，给"一键安装（winget）/ 官网说明 / 重新检测"三个去处。

改启动方式（想换成内嵌终端、加参数、启动前先跑脚本），只需要动 launch()。
"""
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

# ── 常量 ──────────────────────────────────────────────────────────────────

CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
# 这个文件是 Claude Code 自己的，永远不动它——换模型就是往里覆盖一份新内容。
SETTINGS_FILE = os.path.join(CLAUDE_DIR, "settings.json")

# 启动器自己的东西全在这一个目录下，不跟 Claude Code 的配置混在一起。
#   ~/.claude_tool/
#     launcher.json     工作区列表、窗口位置、默认目录
#     claude_settings/  模型预设，一个模型一个 json，由启动器接管
#     workplace/        默认工作区目录，新建的文件夹都建在这儿
TOOL_DIR = os.path.join(os.path.expanduser("~"), ".claude_tool")
CONFIG_FILE = os.path.join(TOOL_DIR, "launcher.json")
PRESET_DIR = os.path.join(TOOL_DIR, "claude_settings")
WORKPLACE_DIR = os.path.join(TOOL_DIR, "workplace")
# 老版本的遗留位置，只用来各迁移一次，迁完就不再看它们
LEGACY_CONFIG = os.path.join(CLAUDE_DIR, "launcher.json")
LEGACY_PREFIX = "settings_"
LEGACY_PRESET_DIR = os.path.join(TOOL_DIR, "models")

PRESET_SUFFIX = ".json"
ILLEGAL_CHARS = r'[<>:"/\\|?*\s]'

CREATE_NEW_CONSOLE = 0x00000010

# 本次会话的权限等级，对应 claude 的 --permission-mode。以前这里写死
# --dangerously-skip-permissions，等于永远挑最松的那档；现在每次启动自己选，
# 选过的按工作区记下来。元组是 (给 claude 的值, 界面上显示的字, 一句解释)。
PERMISSION_MODES = (
    ("default", "每次都问", "用任何工具之前都先问你一句，最稳当。"),
    ("acceptEdits", "改文件不问，跑命令才问", "改、写、移动文件直接做，跑命令还是要你点头。"),
    ("plan", "只规划不动手", "只读代码、给方案，一个文件都不会改。"),
    ("auto", "让模型替你批", "另开一个模型判断该不该放行；官方还标着研究预览，偶尔判错。"),
    ("bypassPermissions", "什么都不问", "权限检查整个跳过。只在你百分之百信得过的目录里用。"),
)
DEFAULT_PERMISSION = "acceptEdits"
PERMISSION_VALUES = tuple(mode for mode, _, _ in PERMISSION_MODES)


def permission_label(value):
    """给 claude 的值 -> 界面上显示的字。认不出来就原样返回。"""
    for mode, label, _ in PERMISSION_MODES:
        if mode == value:
            return label
    return value


def permission_option(value):
    """下拉里那一行：中文解释 + 括号里的真名。

    光写中文解释，熟练用户反而找不着——他脑子里记的是 acceptEdits、
    bypassPermissions 这些字符串，是照着文档和命令行来的。两个都给，各取所需。
    """
    return "{}（{}）".format(permission_label(value), value)


def permission_hint(value):
    """给 claude 的值 -> 那句解释。

    只写大白话：真名和命令行参数摆在别处（下拉里带括号的真名 + 下面那行
    --permission-mode），不用挤在这一句里，挤进来换档时还会撑高对话框。
    """
    for mode, _, hint in PERMISSION_MODES:
        if mode == value:
            return hint
    return ""


def workspace_permission(item):
    """这个工作区上次挑的权限等级。

    没挑过、或者存的值是旧的/手改坏了的，一律回落到默认那档——工作区条目在
    好几个地方现造（新建文件夹、选已有目录、重新扫描），不是每条都带这个字段。
    """
    mode = item.get("permission")
    return mode if mode in PERMISSION_VALUES else DEFAULT_PERMISSION


def claude_command(cont=False, prompt=None, settings=None, permission=None):
    """拼给 cmd /k 的那条命令行。

    cont       接着该目录里最近一次会话聊。
    prompt     开场白，claude 起来就先按这句干（不带就正常空会话）。
    settings   额外的 settings 文件路径，用来给它挂 hook；Claude Code 是把这份
               跟用户自己的 ~/.claude/settings.json 合并，不会覆盖掉。
    permission 本次会话的权限等级，取值见 PERMISSION_MODES；不传就用默认那档。

    这里的 prompt 是拼进 cmd 命令行的，所以里面不能出现双引号——下面几个
    常量都是自己写的，加新的记得别带引号。
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


# ── 常用供应商 ────────────────────────────────────────────────────────────
# "添加模型"表单里那个下拉栏用的：选一个就把地址和模型名填好，用户只剩贴 key。
# (下拉里的名字, 预设名, Base URL, 模型名)
#
# Base URL 全部实测过：拿假 key 打 <base>/v1/messages，回 401/403 说明端点存在，
# 同时往同域名的假路径打一次确认它回 404——否则一个"什么都回 401"的网关会把
# 不存在的路径也伪装成存在。下面这些是过了双重检查的。
# （OpenAI 格式的 /v1/chat/completions 端点不算数，Claude Code 只认 Messages API。）
#
# 模型名是照各家公开文档填的，会过时；聚合平台的模型名还得照它们的目录填。
# 填错了在表单里改一下，或者点「测试」看通不通——那个是真打请求。

PROVIDERS = [
    # 国内
    ("DeepSeek", "DeepSeek", "https://api.deepseek.com/anthropic", "deepseek-chat"),
    ("智谱 GLM", "智谱 GLM", "https://open.bigmodel.cn/api/anthropic", "glm-4.6"),
    ("Kimi", "月之暗面 Kimi", "https://api.moonshot.cn/anthropic", "kimi-k2-turbo-preview"),
    ("硅基流动", "硅基流动", "https://api.siliconflow.cn", "deepseek-ai/DeepSeek-V3"),
    # 国外的聚合平台：一个 key 能用很多家的模型
    ("OpenRouter", "OpenRouter", "https://openrouter.ai/api",
     "anthropic/claude-sonnet-4.6"),
    ("Vercel AI 网关", "Vercel AI 网关", "https://ai-gateway.vercel.sh",
     "anthropic/claude-sonnet-4.6"),
    ("Requesty", "Requesty", "https://router.requesty.ai",
     "anthropic/claude-sonnet-4.6"),
    ("Nano-GPT", "Nano-GPT", "https://nano-gpt.com/api", "claude-sonnet-4.6"),
    # 国内厂商的海外站
    ("MiniMax 国际", "MiniMax 国际", "https://api.minimax.io/anthropic", "MiniMax-M2"),
    ("Moonshot 国际", "Moonshot 国际", "https://api.moonshot.ai/anthropic",
     "kimi-k2-turbo-preview"),
    # 官方
    ("Anthropic 官方", "Anthropic 官方", "https://api.anthropic.com",
     "claude-sonnet-4-6"),
]


def host_of(base):
    """从 Base URL 里抠出域名，给提示语用。"""
    return base.split("//")[-1].split("/")[0]



# ── 交接文档 ──────────────────────────────────────────────────────────────
# 手动按钮的做法：在目标目录里 fork 一份那个会话，让"分身"去写文档。fork 出来的
# 是新 session id，所以不会往用户原来的对话里塞进这么一轮。
# 不给 --dangerously-skip-permissions，只放这四个工具——写文档用不着跑命令。

CREATE_NO_WINDOW = 0x08000000
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
    command = '"{}" "{}" {}'.format(python_exe(), os.path.abspath(__file__),
                                    HOOK_FLAG)
    payload = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": command}]}]}}
    with open(HOOK_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return HOOK_SETTINGS


# ── 外观 ──────────────────────────────────────────────────────────────────

PAGE_BG = "#f4f5f7"
PANEL_BG = "#ffffff"
HOVER_BG = "#eef0f4"
BORDER = "#e2e5ea"
TEXT = "#1f2328"
MUTED = "#8b939e"
ACCENT = "#4a6cf7"
ACCENT_HOVER = "#3b5ce0"
ACCENT_SOFT = "#eaefff"
WARN = "#d05a56"
OK = "#2f9e6b"
ALERT_BG = "#fdf3f2"

FONT_FAMILY = "Microsoft YaHei UI"


def font(size=11, bold=False):
    return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)


# 量文字用的 Font 对象建起来不便宜，按字号缓存一份。
# 换了 Tk 根（比如测试里反复开关窗口）会让缓存的字体失效，那时重建即可。
_measured = {}


def measure(text, size=11, bold=False):
    key = (size, bold)
    try:
        return _measured[key].measure(text)
    except KeyError:
        _measured[key] = tkfont.Font(family=FONT_FAMILY, size=size,
                                     weight="bold" if bold else "normal")
        return _measured[key].measure(text)
    except tk.TclError:
        _measured.clear()
        return measure(text, size, bold)


def ellipsize(text, max_width, size=11, bold=False):
    if measure(text, size, bold) <= max_width:
        return text
    while text and measure(text + "…", size, bold) > max_width:
        text = text[:-1]
    return text + "…"


# ── 模型预设 ──────────────────────────────────────────────────────────────


def preset_path(name):
    """预设名 -> 文件路径。名称里的非法字符在表单那关已经挡掉了。"""
    return os.path.join(PRESET_DIR, name + PRESET_SUFFIX)


def migrate_presets():
    """把预设从两个老位置搬进 claude_settings/。

    来源一：早期版本放在 ~/.claude/settings_<名称>.json 的那些。
    来源二：中间版本放在 ~/.claude_tool/models/<名称>.json 的那些。
    只搬不删用户文件；目标已存在就不动，免得覆盖掉后来改过的。返回搬来的名称。
    """
    moved = []

    def take(source, target, name):
        if os.path.exists(target):
            return
        try:
            shutil.copyfile(source, target)
            moved.append(name)
        except OSError:
            pass

    if os.path.isdir(LEGACY_PRESET_DIR):
        for filename in os.listdir(LEGACY_PRESET_DIR):
            if filename.endswith(PRESET_SUFFIX):
                name = filename[:-len(PRESET_SUFFIX)]
                if name:
                    take(os.path.join(LEGACY_PRESET_DIR, filename),
                         preset_path(name), name)

    if os.path.isdir(CLAUDE_DIR):
        for filename in os.listdir(CLAUDE_DIR):
            if not (filename.startswith(LEGACY_PREFIX)
                    and filename.endswith(PRESET_SUFFIX)):
                continue
            # settings.json 撞不上这个前缀（它是 settings. 不是 settings_），
            # 所以这里不会误搬到 Claude Code 自己的配置
            name = filename[len(LEGACY_PREFIX):-len(PRESET_SUFFIX)]
            if not name:
                continue
            take(os.path.join(CLAUDE_DIR, filename), preset_path(name), name)

    consume_legacy_preset_dir()
    return moved


def consume_legacy_preset_dir():
    """收掉中间版本留下的 .claude_tool/models/。

    只在里面每一个 json 都能在 claude_settings/ 里找到逐字节一样的一份时才删。
    有一条对不上、或者读不了，就整个留着——宁可多一个目录，也不删用户改过的东西。
    """
    if not os.path.isdir(LEGACY_PRESET_DIR):
        return False
    for filename in os.listdir(LEGACY_PRESET_DIR):
        try:
            with open(os.path.join(LEGACY_PRESET_DIR, filename), "rb") as old, \
                    open(os.path.join(PRESET_DIR, filename), "rb") as new:
                if old.read() != new.read():
                    return False
        except OSError:
            return False
    try:
        shutil.rmtree(LEGACY_PRESET_DIR)
    except OSError:
        return False
    return True


def discover_presets():
    """扫描 claude_settings/ 下所有 *.json，返回 {名称: 完整路径}。"""
    presets = {}
    if not os.path.isdir(PRESET_DIR):
        return presets
    for filename in os.listdir(PRESET_DIR):
        if not filename.endswith(PRESET_SUFFIX):
            continue
        name = filename[:-len(PRESET_SUFFIX)]
        if name:
            presets[name] = os.path.join(PRESET_DIR, filename)
    return presets


def read_env(path):
    """读取预设文件里的 env 段，读不到就返回空字典。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("env", {})
    except Exception:
        return {}


def read_model(path):
    """从预设文件读取 ANTHROPIC_MODEL，读不到则返回占位。"""
    return read_env(path).get("ANTHROPIC_MODEL") or "未知模型"


def describe_preset(path):
    """卡片副标题：模型名 · 供应商域名，域名读不到就只显示模型名。"""
    env = read_env(path)
    host = env.get("ANTHROPIC_BASE_URL", "").split("//")[-1].split("/")[0]
    model = read_model(path)
    return "{}  ·  {}".format(model, host) if host else model


def active_preset(presets):
    """返回当前 settings.json 内容所对应的预设名称；若为自定义配置则返回 None。"""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            current = json.load(f)
    except Exception:
        return None
    for name, path in presets.items():
        try:
            with open(path, "r", encoding="utf-8") as f:
                if json.load(f) == current:
                    return name
        except Exception:
            continue
    return None


def test_preset(path, timeout=15):
    """照着预设打一次最小请求，看这个 endpoint 通不通。

    返回 (是否可用, 说明)。说明要么是耗时，要么是状态码/错误名，直接摆卡片上。
    """
    env = read_env(path)
    base = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    model = env.get("ANTHROPIC_MODEL") or ""
    if not (base and token and model):
        return False, "配置不全"

    payload = json.dumps({
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/v1/messages", data=payload,
        headers={
            "content-type": "application/json",
            "authorization": "Bearer " + token,
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        })
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)
        return True, "可用 {:.1f}s".format(time.time() - started)
    except urllib.error.HTTPError as e:
        return False, "失败 {}".format(e.code)
    except urllib.error.URLError as e:
        # 带上底层原因：超时、DNS 解析不了、连接被拒是三种完全不同的毛病，
        # 光写"连不上"熟练用户也没法判断该改哪儿。
        reason = str(getattr(e, "reason", "") or "").strip()
        if not reason:
            return False, "连不上"
        return False, "连不上 · " + reason
    except Exception as e:
        return False, type(e).__name__


# ── 工作区 ────────────────────────────────────────────────────────────────


def path_key(path):
    """比较路径用：大小写和正反斜杠的差异都不算差异。"""
    return os.path.normcase(os.path.normpath(path))


def default_config():
    """全新安装时的配置：默认工作区目录建在工具目录下，它本身也算第一个工作区。"""
    return {
        "workplace": WORKPLACE_DIR,
        "roots": [WORKPLACE_DIR],
        "workspaces": [{"name": "默认", "path": WORKPLACE_DIR}],
        "window": None,
        # 自动刷交接文档会多花 token，所以首次装上一定是关的
        "auto_handoff": False,
    }


def load_config():
    """读工具目录下的配置；那儿没有就翻一次旧位置，把老配置原样接过来。

    都没有就返回 None，调用方据此判断这是不是全新安装。
    """
    data = None
    for path in (CONFIG_FILE, LEGACY_CONFIG):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            break
        except Exception:
            continue
    if not isinstance(data, dict):
        return None

    config = default_config()
    workplace = data.get("workplace")
    if isinstance(workplace, str) and workplace.strip():
        config["workplace"] = os.path.normpath(workplace.strip())
    # 没写 roots 就跟着 workplace 走；写了就以写的为准
    config["roots"] = [config["workplace"]]
    window = data.get("window")
    if isinstance(window, dict) and all(
            isinstance(window.get(k), int) and abs(window[k]) < 32768
            for k in ("x", "y", "w", "h")) and window["w"] >= 300 and window["h"] >= 300:
        config["window"] = {k: window[k] for k in ("x", "y", "w", "h")}
    if isinstance(data.get("roots"), list) and data["roots"]:
        config["roots"] = [r for r in data["roots"] if isinstance(r, str)]
    # 默认工作区目录永远在扫描范围内，"重新扫描"才扫得到它下面手建的文件夹
    if all(path_key(r) != path_key(config["workplace"]) for r in config["roots"]):
        config["roots"].insert(0, config["workplace"])
    if isinstance(data.get("workspaces"), list):
        workspaces = []
        for item in data["workspaces"]:
            if isinstance(item, dict) and item.get("path"):
                path = item["path"]
                workspaces.append({
                    "name": str(item.get("name") or os.path.basename(path)),
                    "path": path,
                    "permission": workspace_permission(item),
                })
        config["workspaces"] = workspaces
    config["auto_handoff"] = bool(data.get("auto_handoff"))
    return config


def save_config(config):
    os.makedirs(TOOL_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")


def project_dir(path):
    """工作目录 -> ~/.claude/projects 下面存这个项目会话记录的那个目录。

    Claude Code 的命名规则是把路径里所有非字母数字的字符换成短横：D:\\Desktop
    变成 D--Desktop，D:\\File\\claude\\kk 变成 D--File-claude-kk。猜错了也只是
    显示成"还没聊过"，不会出错。
    """
    return os.path.join(PROJECTS_DIR, re.sub(r"[^A-Za-z0-9]", "-", path))


def last_chat_time(path):
    """这个工作区最近一次会话是什么时候。找不着就返回 None。

    会话记录是目录里一个个 .jsonl，最大 mtime 就是最后一次动过的时间。只算
    .jsonl：那目录里还可能有 memory 之类的子目录，不能一并算进去。
    """
    folder = project_dir(path)
    try:
        stamps = [os.path.getmtime(os.path.join(folder, name))
                  for name in os.listdir(folder) if name.endswith(".jsonl")]
    except OSError:
        return None
    return max(stamps) if stamps else None


def humanize_ago(stamp):
    """时间戳 -> 「刚刚」「3 小时前」「5 天前」这种说法。"""
    delta = max(time.time() - stamp, 0)
    if delta < 90:
        return "刚刚"
    if delta < 3600:
        return "{} 分钟前".format(int(delta // 60))
    if delta < 86400:
        return "{} 小时前".format(int(delta // 3600))
    if delta < 86400 * 30:
        return "{} 天前".format(int(delta // 86400))
    if delta < 86400 * 365:
        return "{} 个月前".format(int(delta // (86400 * 30)))
    return "{} 年前".format(int(delta // (86400 * 365)))


def scan_roots(roots):
    """扫 roots 下的一级子文件夹，返回 [{name, path}]。"""
    found = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root), key=str.lower):
            path = os.path.join(root, entry)
            if not os.path.isdir(path):
                continue
            key = os.path.normcase(os.path.normpath(path))
            if key in seen:
                continue
            seen.add(key)
            found.append({"name": entry, "path": path})
    return found


def merge_scanned(config):
    """把 roots 下新出现的目录并进工作区列表；已存在的按路径去重，名称不动。"""
    known = {os.path.normcase(os.path.normpath(w["path"])) for w in config["workspaces"]}
    added = []
    for item in scan_roots(config["roots"]):
        key = os.path.normcase(os.path.normpath(item["path"]))
        if key not in known:
            known.add(key)
            config["workspaces"].append(item)
            added.append(item["name"])
    return added


def launch(workdir, cont=False, prompt=None, settings=None, permission=None):
    """在新控制台窗口里，以 workdir 为工作目录启动 claude。

    返回那个进程对象——调用方留着它轮询 poll()，就知道这个会话还开没开着。
    """
    return subprocess.Popen(
        ["cmd", "/k", claude_command(cont, prompt, settings, permission)],
        cwd=workdir,
        creationflags=CREATE_NEW_CONSOLE,
    )


# ── 内嵌终端 ──────────────────────────────────────────────────────────────
# Windows Terminal 是 WinUI3 应用，窗口没法当子窗口塞进别的窗口，所以内嵌这
# 条路只能拉老 conhost.exe。代价：conhost 没有字体回退（它默认用新宋体），
# claude 界面里少数符号会显示成方框，而 WT 里不会。

GWL_STYLE = -16
WS_CHILD, WS_VISIBLE, WS_POPUP = 0x40000000, 0x10000000, 0x80000000
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
CONSOLE_CLASS = "ConsoleWindowClass"
TITLE_TAG = "claude-embed"

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

# 句柄是 64 位的指针，不声明 argtypes 的话 ctypes 会按 32 位 int 截断。
_user32 = ctypes.windll.user32
_user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def console_windows():
    """当前桌面上所有老式控制台窗口的 (句柄, 标题)。"""
    found = []

    def visit(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(hwnd, name, 64)
        if name.value == CONSOLE_CLASS:
            title = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
            found.append((hwnd, title.value))
        return True

    ctypes.windll.user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def spawn_console(workdir, cont=False, prompt=None, settings=None, permission=None):
    """开一个老式 conhost 跑 claude。

    返回 (进程, 启动前就存在的窗口句柄集合)——窗口是控制台那边异步建的，
    调用方得拿这份旧名单去比对，才知道哪个是新冒出来的。
    """
    known = {hwnd for hwnd, _ in console_windows()}
    command = "title {} & {}".format(
        TITLE_TAG, claude_command(cont, prompt, settings, permission))
    process = subprocess.Popen(
        ["conhost.exe", "cmd", "/k", command],
        cwd=workdir,
        creationflags=CREATE_NEW_CONSOLE,
    )
    return process, known


def fresh_console(known):
    """在 known 之外找新冒出来的控制台窗口；优先认标题打着自己标记的那个。"""
    new = [item for item in console_windows() if item[0] not in known]
    if not new:
        return None
    tagged = [hwnd for hwnd, title in new if TITLE_TAG in title]
    return tagged[0] if tagged else new[0][0]


class EmbeddedConsole:
    """一个被塞进启动器窗口里的 conhost。"""

    def __init__(self, process, hwnd, holder):
        self.process = process
        self.hwnd = hwnd
        self.holder = holder
        self._attach()

    def _attach(self):
        u = ctypes.windll.user32
        root = self.holder.winfo_toplevel()
        u.SetParent(self.hwnd, root.winfo_id())
        style = u.GetWindowLongW(self.hwnd, GWL_STYLE)
        style &= ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU |
                   WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        u.SetWindowLongW(self.hwnd, GWL_STYLE, style | WS_CHILD | WS_VISIBLE)
        self.place()

    def place(self):
        """子窗口的坐标是相对父窗口客户区的，所以拿 Tk 的屏幕坐标做差。"""
        self.holder.update_idletasks()
        root = self.holder.winfo_toplevel()
        ctypes.windll.user32.SetWindowPos(
            self.hwnd, 0,
            self.holder.winfo_rootx() - root.winfo_rootx(),
            self.holder.winfo_rooty() - root.winfo_rooty(),
            self.holder.winfo_width(), self.holder.winfo_height(),
            SWP_NOZORDER | SWP_NOACTIVATE)

    def detach(self):
        """放出去，变回一个普通的独立窗口；进程一律不动，不关 claude。"""
        u = ctypes.windll.user32
        style = u.GetWindowLongW(self.hwnd, GWL_STYLE)
        u.SetWindowLongW(self.hwnd, GWL_STYLE,
                         (style & ~WS_CHILD) | WS_CAPTION | WS_THICKFRAME |
                         WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        u.SetParent(self.hwnd, 0)
        root = self.holder.winfo_toplevel()
        x, y = window_position(root)
        u.SetWindowPos(self.hwnd, 0, x + 48, y + 48, 900, 600,
                       SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED |
                       SWP_SHOWWINDOW)


# ── 窗口位置 ──────────────────────────────────────────────────────────────
# Tk 的 geometry 把 "-N" 读成"距屏幕右边缘 N 像素"，副屏的负坐标会落到主屏上，
# 而且每存读一轮还会漂十几像素，所以位置这块直接问 Win32。

SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _frame_handle(window):
    """winfo_id 给的是客户区句柄，带边框的顶层窗口是它的父窗口。"""
    return ctypes.windll.user32.GetParent(window.winfo_id())


def window_position(window):
    """顶层窗口左上角在桌面上的坐标，副屏上是负的。"""
    rect = _RECT()
    ctypes.windll.user32.GetWindowRect(_frame_handle(window), ctypes.byref(rect))
    return rect.left, rect.top


def place_window(window, x, y):
    ctypes.windll.user32.SetWindowPos(
        _frame_handle(window), 0, x, y, 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


# ── 绘制零件 ──────────────────────────────────────────────────────────────


def rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """在 canvas 上画一个圆角矩形（用平滑多边形近似）。"""
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class PillButton(tk.Canvas):
    """圆角胶囊按钮。"""

    def __init__(self, parent, text, command, primary=False, bg=PAGE_BG, height=28):
        width = measure(text, 10, primary) + 26
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, borderwidth=0)
        self._text = text
        self._command = command
        self._primary = primary
        self._hover = False
        self._cw, self._ch = width, height

        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._command())
        self._draw()

    def _set_hover(self, value):
        self._hover = value
        self.configure(cursor="hand2" if value else "arrow")
        self._draw()

    def _draw(self):
        self.delete("all")
        if self._primary:
            fill = ACCENT_HOVER if self._hover else ACCENT
            outline, fg = "", "#ffffff"
        else:
            fill = HOVER_BG if self._hover else PANEL_BG
            outline, fg = BORDER, TEXT
        rounded_rect(self, 0, 0, self._cw - 1, self._ch - 1, self._ch / 2,
                     fill=fill, outline=outline or fill)
        self.create_text(self._cw / 2, self._ch / 2 + 1, text=self._text,
                         fill=fg, font=font(10, self._primary))


class Row(tk.Canvas):
    """列表里的一张卡片。可带副标题、右侧文字动作、选中标记。"""

    # 动作格子的最小宽度。实际宽度按标签量出来（见 _action_spans），这只是个下限，
    # 免得单字动作挤成一条缝。
    ICON_W = 32

    def __init__(self, parent, title, subtitle="", on_click=None, actions=(),
                 active=False, marker=False, warn="", warn_color=WARN,
                 bg=PAGE_BG, height=52):
        super().__init__(parent, height=height, bg=bg,
                         highlightthickness=0, borderwidth=0)
        self.title = title
        self.subtitle = subtitle
        self.on_click = on_click
        self.actions = list(actions)
        self.active = active
        self.marker = marker
        self.warn = warn
        self.warn_color = warn_color
        self._hover = None

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)

    # 命中判定：返回 None / "row" / 动作下标
    def _hit(self, x):
        for index, (x1, x2) in enumerate(self._action_spans()):
            if x1 <= x <= x2:
                return index
        return "row" if self.on_click else None

    def _action_spans(self):
        """每个动作占的横向格子，从右往左排。

        宽度按标签实际量出来算，不写死——动作现在都是「改名」「打开」这样的词，
        不是固定宽度的单个字形了。量的是当前字体下的真实宽度，所以中文和其他
        字符混着也不会挤在一起。
        """
        spans = []
        right = self.winfo_width() - 8
        for glyph, _callback in self.actions:
            width = max(self.ICON_W, measure(glyph, 11) + 18)
            spans.append((right - width, right))
            right -= width
        return spans

    def _on_motion(self, event):
        target = self._hit(event.x)
        if target != self._hover:
            self._hover = target
            self.configure(cursor="hand2" if target is not None else "arrow")
            self._draw()

    def _on_leave(self, _event):
        if self._hover is not None:
            self._hover = None
            self.configure(cursor="arrow")
            self._draw()

    def _on_press(self, event):
        target = self._hit(event.x)
        if target == "row":
            self.on_click()
        elif isinstance(target, int):
            self.actions[target][1]()

    def set_warn(self, text, color=WARN):
        """换掉右侧那行小字；测试结果就是靠它落在卡片上的。"""
        self.warn, self.warn_color = text, color
        self._draw()

    def _draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1:
            return

        if self.active:
            fill = ACCENT_SOFT
        elif self._hover == "row":
            fill = HOVER_BG
        else:
            fill = PANEL_BG
        rounded_rect(self, 0, 0, width - 1, height - 1, 10,
                     fill=fill, outline=ACCENT_SOFT if self.active else BORDER)

        x = 16
        if self.marker:
            cy = height / 2
            if self.active:
                self.create_oval(x, cy - 5, x + 10, cy + 5,
                                 fill=ACCENT, outline="")
            else:
                self.create_oval(x + 1, cy - 4, x + 9, cy + 4,
                                 fill=PANEL_BG, outline=MUTED, width=2)
            x += 22

        spans = self._action_spans()
        action_edge = spans[-1][0] - 10 if spans else width - 16
        title_room = max(action_edge - x, 40)
        subtitle_room = title_room

        # 备注靠右对齐、跟标题同一行。放下面那行会跟路径抢地方，把「D:\File\claude\kk」
        # 截成「D:\File\...」——正好是最活跃的几行最难认。标题这边空得很（名字普遍
        # 四五个字），拿标题那行的余量换路径的完整，划算得多。
        if self.warn:
            # 备注最长只占到"给标题留 90px"为止。它的内容是外部来的（连不上的
            # 底层原因、异常类名），长度不可控；不封顶的话一条长错误就能把标题
            # 挤成一片省略号，那一行就认不出来了。
            warn = ellipsize(self.warn, max(action_edge - x - 102, 60), 9)
            warn_width = measure(warn, 9)
            note_y = height / 2 - 9 if self.subtitle else height / 2
            title_room = max(title_room - warn_width - 12, 40)
            self.create_text(action_edge, note_y, text=warn,
                             anchor="e", fill=self.warn_color, font=font(9))

        title_font = font(11, self.active)
        if self.subtitle:
            self.create_text(x, height / 2 - 9,
                             text=ellipsize(self.title, title_room, 11, self.active),
                             anchor="w", fill=ACCENT if self.active else TEXT,
                             font=title_font)
            self.create_text(x, height / 2 + 11,
                             text=ellipsize(self.subtitle, subtitle_room), anchor="w",
                             fill=MUTED, font=font(9))
        else:
            self.create_text(x, height / 2, text=ellipsize(self.title, title_room, 11, self.active),
                             anchor="w", fill=ACCENT if self.active else TEXT,
                             font=title_font)

        for index, (glyph, _callback) in enumerate(self.actions):
            x1, x2 = spans[index]
            cx, cy = (x1 + x2) / 2, height / 2
            if self._hover == index:
                self.create_oval(x1 + 2, cy - 11, x2 - 2, cy + 11,
                                 fill="#e6e9ef", outline="")
            # 悬停的时候字色压深一点，跟底下的浅灰圆一起把"这个能点"说清楚。
            self.create_text(cx, cy, text=glyph, font=font(11),
                             fill=TEXT if self._hover == index else MUTED)


class ScrollArea(tk.Frame):
    """带细滚动条的容器，内容都塞进 .inner。"""

    def __init__(self, parent, max_height, bg=PAGE_BG):
        super().__init__(parent, bg=bg)
        self.max_height = max_height
        self.canvas = tk.Canvas(self, height=max_height, bg=bg,
                                highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", style="Slim.Vertical.TScrollbar",
                            command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    def _on_inner_resize(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()

    def fit(self):
        """把高度收到内容那么多，超过 max_height 才出现滚动条。"""
        self.update_idletasks()
        self.canvas.configure(height=min(self.inner.winfo_reqheight(), self.max_height))

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def scroll(self, steps):
        self.canvas.yview_scroll(steps, "units")


# ── 通用表单控件 ──────────────────────────────────────────────────────────


def make_entry(parent, var, width=40, secret=False):
    return tk.Entry(parent, textvariable=var, width=width, show="*" if secret else "",
                    bg=PANEL_BG, fg=TEXT, relief="flat", font=font(11),
                    insertbackground=TEXT, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=ACCENT)


def make_form(dialog, title):
    """建一个统一样式的对话框，返回可往里放控件的 body 框架。"""
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.configure(bg=PAGE_BG)
    dialog.transient(dialog.master)

    tk.Label(dialog, text=title, bg=PAGE_BG, fg=TEXT,
             font=font(13, True), anchor="w").pack(fill="x", padx=20, pady=(16, 10))
    body = tk.Frame(dialog, bg=PAGE_BG)
    body.pack(fill="x", padx=20)
    return body


def finish_form(dialog, buttons):
    """在对话框底部摆按钮，buttons 是 [(文字, 回调, 是否主按钮), ...]。"""
    holder = tk.Frame(dialog, bg=PAGE_BG)
    holder.pack(fill="x", padx=20, pady=(16, 16))
    for text, command, primary in buttons:
        PillButton(holder, text, command, primary=primary,
                   bg=PAGE_BG).pack(side="right", padx=(8, 0))


# ── 主窗口 ────────────────────────────────────────────────────────────────

# 两个列表的高度上限
MODEL_MAX, WORKSPACE_MAX = 200, 320
# 内嵌终端时：左列固定这么宽，终端从右边长出来，窗口不够宽就往右撑
SIDE_WIDTH = 360
TERMINAL_MIN_WIDTH = 900


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude 启动器")
        self.geometry("640x760")
        self.minsize(560, 520)
        self.configure(bg=PAGE_BG)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.layout("Slim.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {
                "children": [("Vertical.Scrollbar.thumb",
                              {"expand": "1", "sticky": "nswe"})],
                "sticky": "ns"})])
        style.configure("Slim.Vertical.TScrollbar", background="#cbd1da",
                        troughcolor=PAGE_BG, bordercolor=PAGE_BG,
                        darkcolor="#cbd1da", lightcolor="#cbd1da",
                        arrowsize=0, width=8)
        style.map("Slim.Vertical.TScrollbar", background=[("active", "#aab3c0")])

        # clam 默认把只读下拉画成灰底，看着像禁用了。改成跟旁边的输入框一个样。
        style.configure("TCombobox", fieldbackground=PANEL_BG, background=PANEL_BG,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL_BG)],
                  background=[("readonly", PANEL_BG)],
                  foreground=[("readonly", TEXT)],
                  arrowcolor=[("readonly", MUTED)])
        # 下拉弹出来的列表是独立的 Listbox，样式不跟着 Combobox 走，得单独上色
        for pattern, value in (
                ("*TCombobox*Listbox.background", PANEL_BG),
                ("*TCombobox*Listbox.foreground", TEXT),
                ("*TCombobox*Listbox.selectBackground", ACCENT_SOFT),
                ("*TCombobox*Listbox.selectForeground", TEXT),
                ("*TCombobox*Listbox.font", font(10))):
            self.option_add(pattern, value)

        self.active_name = None
        self.embedded = None      # 当前塞在窗口里的那个 conhost
        self._pending = None      # 正在等窗口冒出来的那次启动
        self._placing = False
        self._width_before_embed = None
        self.ws_filter = tk.StringVar()   # 工作区筛选框
        self.ws_view = []                 # 当前真正显示出来的工作区，快捷键按它算序号
        self.model_rows = {}              # 名称 -> 卡片，测试结果要回填到上面
        self._testing = False
        self._results = queue.Queue()
        self._handoff_proc = None         # 正在写交接文档的那个 claude
        self._handoff_ctx = None          # (工作区, 目标文件, 写之前的 mtime)
        self._handoff_log = None
        # 这个启动器开出去、还开着的 claude：[{name, path, proc}]
        self.running = []
        self.version_queue = queue.Queue()   # 后台问出来的 claude 版本号，主线程来取
        # 目录骨架和一次性迁移都在启动时做完，之后各处只管用，不必再判存在。
        for directory in (TOOL_DIR, PRESET_DIR):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                pass
        self.migrated_presets = migrate_presets()

        self.config_data = load_config()
        fresh = self.config_data is None
        if fresh:
            self.config_data = default_config()
        if not os.path.exists(CONFIG_FILE):
            # 首次在新位置落盘：全新安装顺手把默认工作区目录建出来，从旧位置
            # 迁过来的就原样照搬，不再多扫一遍目录。
            try:
                os.makedirs(self.config_data["workplace"], exist_ok=True)
            except OSError:
                pass
            if fresh:
                merge_scanned(self.config_data)
            save_config(self.config_data)

        self._build_ui()
        self.refresh_models()
        self.refresh_workspaces()
        self._probe_claude_version()
        self.after(1000, self._poll_running)
        self.ws_filter.trace_add("write", lambda *_: self.refresh_workspaces())
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Control-f>", self._focus_filter)
        self.bind_all("<Control-F>", self._focus_filter)
        for number in range(1, 10):
            self.bind_all("<Control-Key-{}>".format(number),
                          lambda _e, n=number: self.launch_nth(n))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._restore_geometry()

    def _restore_geometry(self):
        saved = self.config_data["window"]
        if not saved:
            return
        self.geometry("{}x{}".format(saved["w"], saved["h"]))
        self.update_idletasks()
        place_window(self, saved["x"], saved["y"])

    def _on_close(self):
        # 必须先放出去：父窗口一销毁，挂在它下面的子窗口会跟着被销毁，
        # 那等于把用户正在跑的 claude 一起干掉。
        self.detach_terminal()
        try:
            x, y = window_position(self)
            self.config_data["window"] = {
                "x": x, "y": y,
                "w": self.winfo_width(), "h": self.winfo_height()}
            save_config(self.config_data)
        except Exception:
            pass
        self.destroy()

    # ── 布局 ──

    def _build_ui(self):
        # 顶上是条状态条：当前模型、claude 装的是哪版、以及去配置目录的入口。
        # 这里不再写一遍"Claude 启动器"——窗口标题栏上已经有那个名字了，
        # 正文再来一遍纯属占地方。
        header = tk.Frame(self, bg=PANEL_BG)
        header.pack(fill="x")
        line = tk.Frame(header, bg=PANEL_BG)
        line.pack(fill="x", padx=20, pady=12)
        PillButton(line, "打开配置目录", self._open_tool_dir,
                   bg=PANEL_BG).pack(side="right")

        tk.Label(line, text="当前模型", bg=PANEL_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(0, 8))
        self.status_var = tk.StringVar(value="未设置")
        # 模型名做成个小胶囊。有模型时用强调色，没设的时候是中性灰——
        # 空状态不该长得像报错或者链接。
        self.model_chip = tk.Label(line, textvariable=self.status_var, bg=HOVER_BG,
                                   fg=MUTED, font=font(10, True), padx=10, pady=2)
        self.model_chip.pack(side="left")

        self.version_var = tk.StringVar(value="正在查 claude 版本…")
        tk.Label(line, textvariable=self.version_var, bg=PANEL_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(16, 0))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # 没装 claude 才挂出来的告警条，装好了整条不占地方
        self.banner = tk.Frame(self, bg=ALERT_BG)
        self._build_claude_banner()

        footer = tk.Frame(self, bg=PAGE_BG)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x")
        switches = tk.Frame(footer, bg=PAGE_BG)
        switches.pack(fill="x", padx=16, pady=(8, 0))
        self.embed_var = tk.BooleanVar(value=False)
        # 会花用户 token 的功能，默认关；这个勾只影响新开的会话，不动已经跑着的
        self.auto_handoff_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_handoff")))
        # 括号里写的是各自的真名：内嵌那条走的是 conhost（不是默认的 Windows
        # Terminal，字形回退差些），自动刷文档靠的是 Claude Code 的 Stop hook。
        # 熟练用户要的是这两个词，好去翻文档、翻配置文件；只写大白话他就得猜。
        for var, text, command in (
                (self.embed_var, "内嵌终端 conhost（不勾就在新窗口里开）",
                 self._on_embed_toggle),
                (self.auto_handoff_var, "自动刷交接文档 Stop hook（会多用 token）",
                 self._on_auto_handoff_toggle)):
            tk.Checkbutton(switches, text=text, variable=var, command=command,
                           bg=PAGE_BG, fg=TEXT, font=font(9), activebackground=PAGE_BG,
                           selectcolor=PANEL_BG, highlightthickness=0, bd=0,
                           ).pack(side="left", padx=(0, 18))
        self.feedback_var = tk.StringVar(
            value="点工作区选「新会话」或「接着上次聊」；Ctrl+1~9 一样，Ctrl+F 跳到筛选框。")
        tk.Label(footer, textvariable=self.feedback_var, bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").pack(fill="x", padx=20, pady=(2, 8))

        # footer 先 pack 是为了让它先把自己的高度要走——窗口被拉矮时该挤的是
        # 中间那块列表，不是这行提示。
        body = tk.Frame(self, bg=PAGE_BG)
        body.pack(fill="both", expand=True, padx=14)

        # side 是常年都在的左列；panel 是内嵌终端，平时不摆出来。
        # 两者都 side="left"，终端一出现就从右侧长出来，窗口跟着变宽。
        self.side = tk.Frame(body, bg=PAGE_BG)
        self.side.pack(side="left", fill="both", expand=True)

        # 先建好但不 pack——没会话在跑的时候，界面上不该看出有这么一块。
        # 一有活的会话，_render_running() 就把它插到模型区上面。
        self._build_running(self.side)

        self.model_list = self._build_section(
            self.side, "模型", max_height=MODEL_MAX,
            actions=[("＋ 添加", self._open_model_dialog),
                     ("测试", self.test_all_models),
                     ("刷新", self.refresh_models)])

        self.ws_list = self._build_section(
            self.side, "工作区", max_height=WORKSPACE_MAX, expand=True,
            actions=[("＋ 新建文件夹", self._open_quick_workspace_dialog),
                     ("＋ 选已有目录", self._open_add_workspace_dialog),
                     ("重新扫描", self.rescan)],
            search=self.ws_filter, note=self._build_workplace_note)

        self.panel = tk.Frame(body, bg=PAGE_BG)
        self._build_terminal(self.panel)

    def _build_claude_banner(self):
        """找不到 claude 时在顶上挂一条，给三条明路。找到就什么都不摆。"""
        self.claude_path = find_claude()
        if self.claude_path is not None:
            return
        banner = self.banner
        # 建的时候 body 还没 pack，所以这一 pack 就正好落在标题栏和正文之间
        banner.pack(fill="x")
        inner = tk.Frame(banner, bg=ALERT_BG)
        inner.pack(fill="x", padx=20, pady=10)
        tk.Label(inner, text="没找到 claude，装好才能启动。", bg=ALERT_BG,
                 fg=WARN, font=font(10, True)).pack(side="left")
        PillButton(inner, "重新检测", self._recheck_claude, bg=ALERT_BG,
                   ).pack(side="right", padx=(6, 0))
        PillButton(inner, "打开官网说明", self._open_install_page, bg=ALERT_BG,
                   ).pack(side="right", padx=(6, 0))
        PillButton(inner, "一键安装", self._install_claude, primary=True,
                   bg=ALERT_BG).pack(side="right")
        tk.Frame(banner, bg=BORDER, height=1).pack(fill="x", side="bottom")

    def _recheck_claude(self):
        self.claude_path = find_claude()
        if self.claude_path is None:
            self.feedback_var.set("还是没找到。装完可能要重开一次启动器，PATH 才会刷新。")
            return
        self.banner.pack_forget()
        self.feedback_var.set("找到 claude 了：{}".format(self.claude_path))

    def _open_install_page(self):
        try:
            os.startfile(CLAUDE_INSTALL_URL)
        except OSError as e:
            messagebox.showerror("打不开", "拉不起浏览器：\n{}".format(e))
            return
        self.feedback_var.set("已在浏览器里打开安装说明。")

    def _install_claude(self):
        """调 winget 装。这是动系统的操作，先问一声，不偷偷跑。"""
        if not shutil.which("winget"):
            messagebox.showinfo(
                "没有 winget",
                "这台机器上没有 winget，没法一键装。\n\n"
                "点「打开官网说明」照着装，或者装好 Node 之后跑：\n"
                "npm install -g @anthropic-ai/claude-code")
            return
        if not messagebox.askyesno(
                "安装 claude",
                "会用 winget 装 Anthropic 官方的 Claude Code：\n\n"
                "winget install --id {}\n\n"
                "会弹一个新窗口显示进度，装完自己关掉就行。要继续吗？"
                .format(CLAUDE_WINGET_ID)):
            return
        try:
            subprocess.Popen(
                ["winget", "install", "--id", CLAUDE_WINGET_ID,
                 "--accept-package-agreements", "--accept-source-agreements"],
                creationflags=CREATE_NEW_CONSOLE)
        except Exception as e:
            messagebox.showerror("启动失败", "拉不起 winget：\n{}".format(e))
            return
        self.feedback_var.set("正在装 claude，装完点「重新检测」。")

    def _build_terminal(self, parent):
        """内嵌终端那一块，先建好但不摆出来，等真要内嵌时再 pack。"""
        self.term_head = tk.Frame(parent, bg=PAGE_BG)
        tk.Label(self.term_head, text="终端", bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        self.term_name_var = tk.StringVar()
        tk.Label(self.term_head, textvariable=self.term_name_var, bg=PAGE_BG,
                 fg=MUTED, font=font(10)).pack(side="left", padx=(10, 0))
        PillButton(self.term_head, "放到独立窗口", self.detach_terminal,
                   bg=PAGE_BG).pack(side="right", padx=(6, 0))

        self.term_holder = tk.Frame(parent, bg="#1c1c1c", height=240)
        self.term_holder.pack_propagate(False)
        self.term_holder.bind("<Configure>", self._on_term_resize)

    def _build_section(self, parent, title, max_height, actions, expand=False,
                       search=None, note=None):
        head = tk.Frame(parent, bg=PAGE_BG)
        head.pack(fill="x", pady=(16, 6))
        tk.Label(head, text=title, bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        for text, command in reversed(actions):
            PillButton(head, text, command, bg=PAGE_BG).pack(side="right", padx=(6, 0))
        # 搜索框得跟在标题后面、列表前面，不然 pack 顺序会把列表挤到它上面去
        if search is not None:
            box = tk.Frame(parent, bg=PAGE_BG)
            box.pack(fill="x", pady=(0, 6))
            tk.Label(box, text="筛选", bg=PAGE_BG, fg=MUTED,
                     font=font(9)).pack(side="left", padx=(2, 6))
            self.filter_entry = make_entry(box, search, width=10)
            self.filter_entry.pack(side="left", fill="x", expand=True)
        if note is not None:
            note(parent)
        area = ScrollArea(parent, max_height=max_height)
        # 记下标题栏，"正在跑"那块要靠它把自己插到模型区上面
        area.head = head
        if expand:
            area.pack(fill="both", expand=True)
        else:
            area.pack(fill="x")
        return area

    def _on_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        for target in (self.model_list, self.ws_list):
            if target.contains(widget):
                target.scroll(-int(event.delta / 120))
                return

    # ── 正在跑的会话 ──

    def _build_running(self, parent):
        """「正在跑」那块。建好先不 pack，等真有会话了再插进去。

        只管从这个启动器开出去的窗口——记的是 Popen 句柄，poll() 一下就知道
        那个控制台还开没开着。别的终端里自己敲的 claude 它看不见，也没打算看见。
        """
        self.running_frame = tk.Frame(parent, bg=PAGE_BG)
        head = tk.Frame(self.running_frame, bg=PAGE_BG)
        head.pack(fill="x", pady=(16, 6))
        tk.Label(head, text="正在跑", bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        tk.Label(head, text="（从这个启动器开出去的窗口）", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(8, 0))
        self.running_rows = tk.Frame(self.running_frame, bg=PAGE_BG)
        self.running_rows.pack(fill="x")

    def _render_running(self):
        """照 self.running 重画那几行。空了就整块收起来。"""
        for child in self.running_rows.winfo_children():
            child.destroy()
        if not self.running:
            self.running_frame.pack_forget()
            self.model_list.fit()
            self.ws_list.fit()
            return
        for item in self.running:
            line = tk.Frame(self.running_rows, bg=PANEL_BG,
                            highlightbackground=BORDER, highlightthickness=1)
            line.pack(fill="x", pady=3)
            tk.Label(line, text="●", bg=PANEL_BG, fg=ACCENT,
                     font=font(10)).pack(side="left", padx=(12, 8), pady=8)
            text = tk.Frame(line, bg=PANEL_BG)
            text.pack(side="left", fill="x", expand=True)
            tk.Label(text, text=item["name"], bg=PANEL_BG, fg=TEXT, font=font(10),
                     anchor="w").pack(fill="x")
            tk.Label(text, text=ellipsize(item["path"], 320, 9), bg=PANEL_BG,
                     fg=MUTED, font=font(9), anchor="w").pack(fill="x")
            # 现在就让它写，读的是硬盘上已经存下来的那份会话记录——所以哪怕
            # 里面那份 claude 还开着也照写不误，只是会 fork 出一份副本。
            PillButton(line, "整理交接文档",
                       lambda it=item: self.write_handoff(it),
                       bg=PANEL_BG).pack(side="right", padx=(8, 10), pady=8)
        self.running_frame.pack(fill="x", before=self.model_list.head)
        self.model_list.fit()
        self.ws_list.fit()

    def _poll_running(self):
        """每秒一趟：看一眼那几扇窗口还开着没，顺手把后台问到的版本号贴上。

        两件事都走这条心跳是因为它是现成的、启动时就起来的定时器；再造一个
        只为取个字符串不值当。
        """
        try:
            while True:
                self.version_var.set(self.version_queue.get_nowait())
        except queue.Empty:
            pass

        live = [item for item in self.running if item["proc"].poll() is None]
        if len(live) != len(self.running):
            gone = len(self.running) - len(live)
            self.running = live
            self._render_running()
            if gone:
                self.feedback_var.set("有会话关掉了，正在跑的那块已经跟着更新。")
        self.after(1000, self._poll_running)

    def track_running(self, name, path, process):
        """把一个刚开出去的会话记进名单，界面立刻多出一行。"""
        for item in self.running:
            if item["path"] == path:
                # 同一个目录又开了一个，只留最新那个，免得同一行重复
                item.update({"name": name, "proc": process})
                self._render_running()
                return
        self.running.append({"name": name, "path": path, "proc": process})
        self._render_running()

    # ── 顶栏 ──

    def _probe_claude_version(self):
        """后台问一句 `claude --version`，结果通过队列丢回主线程。

        不能直接在主线程跑：claude 那个 shim 启动要一两秒，卡在启动路径上窗口
        就成了白板。也不能在工作线程里碰 tk——tkinter 不是线程安全的，那边的
        规矩是只有主线程能改控件。所以这里只往队列里塞字符串，
        _poll_running 每次醒过来顺手把它捞出来贴上。
        """
        exe = find_claude()
        if not exe:
            self.version_queue.put("没找到 claude")
            return

        def work():
            try:
                result = subprocess.run(
                    [exe, "--version"], capture_output=True,
                    creationflags=CREATE_NO_WINDOW, timeout=20)
                # 明确按 UTF-8 解：这台机器子进程默认走 cp936，版本串里万一有
                # 非 ASCII 会解出乱码甚至抛异常。
                text = result.stdout.decode("utf-8", "replace").strip()
                if not text:
                    text = result.stderr.decode("utf-8", "replace").strip()
                # 输出可能不止一行（新版偶尔会跟一句更新提示），只留第一行。
                if text:
                    self.version_queue.put("claude " + text.splitlines()[0])
                else:
                    self.version_queue.put("读不到版本号")
            except Exception:
                self.version_queue.put("读不到版本号")

        threading.Thread(target=work, daemon=True).start()

    def _update_model_chip(self, text, known):
        """模型名胶囊。text 是要显示的字，known 是说这个名字是不是一个真预设。

        配色跟着 known 走：认得出来的预设才用强调色，其余一律中性灰——没有预设
        和"预设已经对不上现在这份 settings.json"都得是灰的。空状态长得像链接或者
        报错都是误导，用户会以为点它有用、或者以为哪里错了。
        """
        self.status_var.set(text)
        self.model_chip.configure(bg=ACCENT_SOFT if known else HOVER_BG,
                                  fg=ACCENT if known else MUTED)

    # ── 模型 ──

    def refresh_models(self):
        self.model_list.clear()
        self.model_rows = {}
        presets = discover_presets()
        self.active_name = active_preset(presets)
        inner = self.model_list.inner

        if not presets:
            tk.Label(inner, text="还没有模型预设，点右上角「＋ 添加」建一个。",
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
            self._update_model_chip("未设置", False)
            self.model_list.fit()
            return

        for name in sorted(presets):
            path = presets[name]
            row = Row(inner, title=name, subtitle=describe_preset(path), marker=True,
                      active=(name == self.active_name), height=50,
                      on_click=lambda n=name, p=path: self.switch_model(n, p),
                      actions=[("移除", lambda n=name, p=path: self.remove_model(n, p)),
                               ("编辑", lambda n=name, p=path: self._open_model_dialog((n, p)))])
            row.pack(fill="x", pady=3)
            self.model_rows[name] = row
        self._update_model_chip(self.active_name or "自定义 / 未知",
                                bool(self.active_name))
        self.model_list.fit()

    def test_all_models(self):
        """并发打一遍所有预设，结果落在各自的卡片右侧。"""
        if self._testing:
            self.feedback_var.set("上一轮还在测，等等。")
            return
        presets = discover_presets()
        jobs = [(name, presets[name]) for name in self.model_rows if name in presets]
        if not jobs:
            self.feedback_var.set("没有可测的模型预设。")
            return

        self._testing = True
        self._results = queue.Queue()
        for row in self.model_rows.values():
            row.set_warn("测试中…", MUTED)
        self.feedback_var.set("正在测 {} 个模型…".format(len(jobs)))

        def worker(name, path):
            self._results.put((name, test_preset(path)))

        for name, path in jobs:
            threading.Thread(target=worker, args=(name, path), daemon=True).start()
        self.after(100, lambda: self._drain_results(len(jobs)))

    def _drain_results(self, remaining):
        """在主线程里收后台线程塞进队列的结果——Tk 控件只能主线程碰。"""
        try:
            while True:
                name, (ok, note) = self._results.get_nowait()
                remaining -= 1
                row = self.model_rows.get(name)
                if row is not None:
                    try:
                        row.set_warn(note, OK if ok else WARN)
                    except tk.TclError:
                        pass
        except queue.Empty:
            pass
        if remaining > 0:
            self.after(100, lambda: self._drain_results(remaining))
            return
        self._testing = False
        self.feedback_var.set("测完了：绿色=打得通，红色=打不通。")

    def switch_model(self, name, path):
        try:
            shutil.copyfile(path, SETTINGS_FILE)
        except Exception as e:
            messagebox.showerror("切换失败", "写入 settings.json 失败：\n{}".format(e))
            return
        self.feedback_var.set(
            "已切换到 {}（{}）。重启 Claude Code 后生效。".format(name, read_model(path)))
        self.refresh_models()

    def _open_model_dialog(self, edit=None):
        """edit 传 (名称, 路径) 是编辑，传 None 是新增，两者共用一套表单。"""
        old_name, old_path = edit or (None, None)
        is_edit = old_path is not None
        env = read_env(old_path) if is_edit else {}

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "编辑模型" if is_edit else "添加模型")

        fields = [("预设名称", "name", False), ("Base URL", "base_url", False),
                  ("API Token", "token", True), ("模型名", "model", False)]
        values = {"name": old_name or "", "base_url": env.get("ANTHROPIC_BASE_URL", ""),
                  "token": env.get("ANTHROPIC_AUTH_TOKEN", ""),
                  "model": env.get("ANTHROPIC_MODEL", "")}

        row = 0
        hint_var = tk.StringVar(
            value="从下拉里挑一个供应商，地址和模型名会自动填好，你只要贴自己的 key。")

        # 常用供应商：选一个就把 Base URL 和模型名填好，用户只剩贴 key 这件事。
        picked = tk.Frame(body, bg=PAGE_BG)
        picked.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(picked, text="常用供应商", bg=PAGE_BG, fg=MUTED, font=font(9),
                 ).pack(side="left", padx=(0, 6))
        entries = {}
        provider_var = tk.StringVar()
        combo = ttk.Combobox(picked, textvariable=provider_var, state="readonly",
                             width=30, font=font(10),
                             values=["（自己填）"] + [p[0] for p in PROVIDERS])
        combo.current(0)
        combo.pack(side="left")

        # 记住名字框里那个名字是不是下拉给填的：是的话换一家就跟着换，
        # 用户自己敲过的就不动。免得挑完 DeepSeek 改挑 OpenRouter，名字还留着 DeepSeek。
        auto_name = [""]

        def pick(_event=None):
            """选中哪个就把那家的地址/模型名灌进对应的框。"""
            for short, label, base, model in PROVIDERS:
                if short != provider_var.get():
                    continue
                entries["base_url"][0].set(base)
                entries["model"][0].set(model)
                current = entries["name"][0].get().strip()
                if not current or current == auto_name[0]:
                    entries["name"][0].set(label)
                    auto_name[0] = label
                entries["token"][1].focus_set()
                hint_var.set(
                    "已填好 {} 的地址（{}）和模型名 {}。贴上你的 key；"
                    "模型名各家会变，不对就改一下再点「测试」验。"
                    .format(short, host_of(base), model))
                return

        combo.bind("<<ComboboxSelected>>", pick)
        if is_edit:
            # 编辑已有预设时，地址对得上哪家就把下拉停在哪家
            for index, provider in enumerate(PROVIDERS, start=1):
                if values["base_url"].rstrip("/") == provider[2]:
                    combo.current(index)
                    break
        row += 1

        for label, key, secret in fields:
            tk.Label(body, text=label, bg=PAGE_BG, fg=MUTED, font=font(10),
                     anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=values[key])
            entry = make_entry(body, var, width=40, secret=secret)
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)
            entries[key] = (var, entry)
            if key == "token":
                shown = tk.BooleanVar(value=False)

                def toggle(v=shown, e=entry):
                    e.configure(show="" if v.get() else "*")

                tk.Checkbutton(body, text="显示", variable=shown, command=toggle,
                               bg=PAGE_BG, fg=MUTED, font=font(9),
                               activebackground=PAGE_BG, selectcolor=PANEL_BG,
                               highlightthickness=0, bd=0,
                               ).grid(row=row, column=2, sticky="w", padx=(8, 0))
            row += 1
        body.columnconfigure(1, weight=1)

        tk.Label(body, textvariable=hint_var, bg=PAGE_BG, fg=MUTED, font=font(9),
                 justify="left", anchor="w", wraplength=470,
                 ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        row += 1

        # 改一个当前没在用的预设时，默认不去动正在生效的 settings.json
        apply_now = tk.BooleanVar(value=(not is_edit) or old_name == self.active_name)
        tk.Checkbutton(body, text="保存后立即启用" if is_edit else "添加后立即启用",
                       variable=apply_now, bg=PAGE_BG, fg=TEXT, font=font(10),
                       activebackground=PAGE_BG, selectcolor=PANEL_BG,
                       highlightthickness=0, bd=0,
                       ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row += 1

        note = "改名会一并重命名 claude_settings\\<名称>.json；" if is_edit else \
               "设置会存成 .claude_tool\\claude_settings\\<名称>.json；"
        tk.Label(body, text=note + "env 之外的公共设置沿用原文件。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                 ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))

        save = lambda: self._save_model(dialog, entries, apply_now, edit)
        finish_form(dialog, [("保存", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        entries["name"][1].focus_set()
        self._center(dialog)

    def _save_model(self, dialog, entries, apply_now, edit):
        _old_name, old_path = edit or (None, None)
        name = entries["name"][0].get().strip()
        base_url = entries["base_url"][0].get().strip()
        token = entries["token"][0].get().strip()
        model = entries["model"][0].get().strip()

        if not all([name, base_url, token, model]):
            messagebox.showerror("缺少信息", "四个字段都要填。", parent=dialog)
            return
        if re.search(ILLEGAL_CHARS, name):
            messagebox.showerror("名称非法",
                                 "名称不能包含 < > : \" / \\ | ? * 和空格。", parent=dialog)
            return

        target = preset_path(name)
        renamed = old_path is not None and os.path.normcase(target) != os.path.normcase(old_path)
        if (old_path is None or renamed) and os.path.exists(target):
            messagebox.showerror("名称冲突", "预设「{}」已存在。".format(name), parent=dialog)
            return

        preset = self._preset_template()
        if old_path is not None:
            try:
                with open(old_path, "r", encoding="utf-8") as f:
                    preset = json.load(f)
            except Exception:
                pass
        preset["env"] = {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_MODEL": model,
        }

        try:
            os.makedirs(PRESET_DIR, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(preset, f, ensure_ascii=False, indent=2)
            if renamed:
                os.remove(old_path)
        except Exception as e:
            messagebox.showerror("保存失败", "写入 {} 失败：\n{}".format(target, e), parent=dialog)
            return

        dialog.destroy()
        if apply_now.get():
            self.switch_model(name, target)
        else:
            self.feedback_var.set(
                "{}模型：{}".format("已更新" if old_path else "已新增", name))
            self.refresh_models()

    def remove_model(self, name, path):
        message = "删除模型预设「{}」？\n\n{}".format(name, path)
        if name == self.active_name:
            message += "\n\n它正是当前生效的模型，删掉后 settings.json 保持原样。"
        if not messagebox.askyesno("删除模型", message):
            return
        try:
            os.remove(path)
        except Exception as e:
            messagebox.showerror("删除失败", "删不掉 {}：\n{}".format(path, e))
            return
        self.feedback_var.set("已删除模型预设 {}。".format(name))
        self.refresh_models()

    def _preset_template(self):
        """从现有预设继承 env 之外的公共设置，作为新预设的模板。"""
        presets = discover_presets()
        candidates = []
        if self.active_name and self.active_name in presets:
            candidates.append(presets[self.active_name])
        candidates.extend(presets.values())
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {k: v for k, v in data.items() if k != "env"}
            except Exception:
                continue
        return {
            "enabledPlugins": {},
            "language": "简体中文",
            "skipDangerousModePermissionPrompt": True,
        }

    # ── 工作区 ──

    def refresh_workspaces(self):
        self.ws_list.clear()
        inner = self.ws_list.inner
        workspaces = self.config_data["workspaces"]
        needle = self.ws_filter.get().strip().lower()

        # 筛选后编号会变，所以快捷键按的是"屏幕上第几个"，不是配置里的下标。
        view = [item for item in workspaces
                if needle in item["name"].lower() or needle in item["path"].lower()] \
            if needle else list(workspaces)
        self.ws_view = view

        if not workspaces:
            tk.Label(inner, text="还没有工作区。右上角「＋ 新建文件夹」会在下面那个目录里"
                                 "建一个新的；已经有目录了就用「＋ 选已有目录」。",
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
            return
        if not view:
            tk.Label(inner, text="没有匹配「{}」的工作区。".format(self.ws_filter.get().strip()),
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
            self.ws_list.fit()
            return

        for item in view:
            path = item["path"]
            exists = os.path.isdir(path)
            # 右侧那格放"这行现在什么状态"：有没有交接文档、上次聊是多久以前。
            # 不往副标题里塞是有原因的——副标题是路径，长了会被省略号吃掉尾巴，
            # 而"上次聊"正好就是被吃掉的那部分，等于白写。右侧这格是右对齐的，
            # 永远不会被截断。
            if not exists:
                note, note_color = "目录不存在", WARN
            else:
                stamp = last_chat_time(path)
                parts = []
                if os.path.isfile(os.path.join(path, HANDOFF_FILE)):
                    parts.append("有交接文档")
                if stamp is not None:
                    parts.append("上次聊 " + humanize_ago(stamp))
                # 有交接文档是"现在能接着干"的信号，用强调色；只是时间就是中性灰。
                note = " · ".join(parts)
                note_color = ACCENT if parts[:1] == ["有交接文档"] else MUTED
            # actions 是从右往左摆的（下标 0 在最右边），所以这里的顺序要倒着念：
            # 屏幕上从左到右是 打开 ↑ ↓ 改名 移除。上移/下移用箭头不用词，是因为
            # 一行里塞五个两字词会糊成一片，而箭头没有认不出来的风险。
            Row(inner, title=item["name"], subtitle=path, height=52,
                warn=note, warn_color=note_color,
                on_click=lambda it=item: self.open_workspace(it),
                actions=[("移除", lambda it=item: self.remove_workspace(it)),
                         ("改名", lambda it=item: self.rename_workspace(it)),
                         ("↓", lambda it=item: self.move_workspace(it, 1)),
                         ("↑", lambda it=item: self.move_workspace(it, -1)),
                         ("开目录", lambda p=path: self.open_folder(p))],
                ).pack(fill="x", pady=3)
        self.ws_list.fit()

    def move_workspace(self, item, step):
        """把工作区在列表里挪一格。step 是 -1 上移、+1 下移。

        挪的是配置里 workspaces 的顺序——Ctrl+1~9 选的就是屏幕上第几个，而屏幕
        顺序就是这个数组的顺序。所以挪完立刻落盘，下次打开还是这个次序。

        用 is 而不是 == 找位置：配置里出现两条内容完全一样的工作区时，== 会一直
        认成第一个，按下去就没反应了。
        """
        workspaces = self.config_data["workspaces"]
        index = next((i for i, w in enumerate(workspaces) if w is item), None)
        if index is None:
            return
        target = index + step
        if not 0 <= target < len(workspaces):
            self.feedback_var.set("「{}」已经在{}了。".format(
                item["name"], "最上面" if step < 0 else "最下面"))
            return
        workspaces[index], workspaces[target] = workspaces[target], workspaces[index]
        save_config(self.config_data)
        self.refresh_workspaces()

    def open_workspace(self, item):
        """点工作区先问一句：新开一个还是接着上次聊，要不要先读交接文档。"""
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return

        handoff = os.path.join(path, HANDOFF_FILE)
        has_handoff = os.path.isfile(handoff)

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, item["name"])

        tk.Label(body, text=path, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                 justify="left", wraplength=430).grid(row=0, column=0, columnspan=2,
                                                      sticky="w")

        read_var = tk.BooleanVar(value=has_handoff)
        if has_handoff:
            tk.Checkbutton(body, text="先读交接文档 " + HANDOFF_FILE + "，接着上次的进度干",
                           variable=read_var, bg=PAGE_BG, fg=TEXT, font=font(10),
                           activebackground=PAGE_BG, selectcolor=PANEL_BG,
                           highlightthickness=0, bd=0, anchor="w",
                           ).grid(row=1, column=0, columnspan=2, sticky="w",
                                  pady=(12, 0))
        row_after = 2 if has_handoff else 1

        # 权限等级：预选这个工作区上次用的那档，选了就记回工作区条目里。
        # 按下的永远是下拉里那一行的下标，真正的值（claude 要的那串英文）从
        # PERMISSION_VALUES 现取——界面上摆的字和传给 claude 的字是两回事。
        perm_row = tk.Frame(body, bg=PAGE_BG)
        perm_row.grid(row=row_after, column=0, columnspan=2, sticky="ew",
                      pady=(12, 0))
        tk.Label(perm_row, text="权限", bg=PAGE_BG, fg=MUTED,
                 font=font(10)).pack(side="left")
        perm_var = tk.StringVar(value=permission_option(workspace_permission(item)))
        perm_combo = ttk.Combobox(
            perm_row, textvariable=perm_var, state="readonly", width=30,
            font=font(10), values=[permission_option(mode)
                                  for mode in PERMISSION_VALUES])
        perm_combo.pack(side="left", padx=(10, 0))
        # 命令行参数和解释分两行：熟练用户认的是 --permission-mode 后面那串英文，
        # 中文那句他只当注释看，两行各给各的。合在一行会超宽折行，换档时对话框
        # 还会跟着跳高度。
        perm_flag = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w")
        perm_flag.grid(row=row_after + 1, column=0, columnspan=2, sticky="w",
                       pady=(4, 0))
        # wraplength 给到 470：最长那句（auto）量出来 435px，卡在 430 上就会把
        # 句末那个句号甩到第二行去。
        perm_hint = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9),
                             justify="left", anchor="w", wraplength=470)
        perm_hint.grid(row=row_after + 2, column=0, columnspan=2, sticky="w",
                       pady=(2, 0))

        def show_permission(value):
            perm_flag.configure(text="命令行参数 --permission-mode " + value)
            perm_hint.configure(text=permission_hint(value))

        def pick_permission(_event=None):
            show_permission(PERMISSION_VALUES[perm_combo.current()])

        show_permission(workspace_permission(item))
        perm_combo.bind("<<ComboboxSelected>>", pick_permission)
        row_after += 3

        tk.Label(body, text="没读过的目录就选「开新会话」。想回到上次那段对话就选"
                            "「接着上次聊」——它接的是这个话题里最近的一次会话。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                 wraplength=430,
                 ).grid(row=row_after, column=0, columnspan=2, sticky="w",
                        pady=(12, 0))

        def go(cont):
            prompt = READ_HANDOFF_PROMPT if read_var.get() else None
            # 记下这次挑的权限等级，下次点这个工作区预选它。变了才落盘。
            chosen = PERMISSION_VALUES[perm_combo.current()]
            if chosen != item.get("permission"):
                item["permission"] = chosen
                if item in self.config_data["workspaces"]:
                    save_config(self.config_data)
            dialog.destroy()
            self.launch_workspace(item, cont=cont, prompt=prompt)

        finish_form(dialog, [("接着上次聊", lambda: go(True), True),
                             ("开新会话", lambda: go(False), False)])
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dialog.bind("<Return>", lambda e: go(True))
        self._center(dialog)

    def launch_workspace(self, item, cont=False, prompt=None):
        """真正把 claude 拉起来，新窗口还是塞进本窗口看那个勾。"""
        path = item["path"]
        permission = workspace_permission(item)
        settings = None
        if self.auto_handoff_var.get():
            try:
                settings = ensure_handoff_hook_settings()
            except OSError as e:
                messagebox.showerror("挂 hook 失败",
                                     "写不了 hook 配置，这次就不自动刷交接文档了：\n{}"
                                     .format(e))

        if self.embed_var.get():
            self.embed_workspace(item, cont, prompt, settings, permission)
            return
        try:
            process = launch(path, cont, prompt, settings, permission)
        except Exception as e:
            messagebox.showerror("启动失败", "启动 claude 失败：\n{}".format(e))
            return
        self.track_running(item["name"], path, process)
        self.feedback_var.set("已在新窗口启动{}（权限：{}）：{}".format(
            "（接着上次聊）" if cont else "", permission_option(permission), path))

    def launch_nth(self, number):
        """Ctrl+1~9：和鼠标点一样，也弹那个选择框。"""
        if 1 <= number <= len(self.ws_view):
            self.open_workspace(self.ws_view[number - 1])
            return "break"

    def _focus_filter(self, _event=None):
        self.filter_entry.focus_set()
        self.filter_entry.select_range(0, "end")
        return "break"

    # ── 内嵌终端 ──

    def _on_embed_toggle(self):
        if not self.embed_var.get():
            self.detach_terminal()

    def _on_auto_handoff_toggle(self):
        self.config_data["auto_handoff"] = self.auto_handoff_var.get()
        save_config(self.config_data)
        if self.auto_handoff_var.get():
            # 这行是单行不折的，长了会被窗口边裁掉，所以说不了 hook 文件在哪；
            # 文件在配置目录的 hooks\ 下面，顶栏那个按钮一步就到。
            self.feedback_var.set(
                "已挂上 Stop hook：聊够 {} 轮且隔 {} 分钟，新开的会话会自己把 {} "
                "刷一遍，多用掉的 token 算在你账上。".format(
                    HANDOFF_MIN_TURNS, HANDOFF_COOLDOWN // 60, HANDOFF_FILE))
        else:
            self.feedback_var.set("已摘掉 Stop hook，新开的会话不再自动刷交接文档。")

    def embed_workspace(self, item, cont=False, prompt=None, settings=None,
                        permission=None):
        """把 claude 塞进本窗口。已经有一个的话先把它放出去，不打断它。"""
        if self._pending:
            self.feedback_var.set("上一个还在启动，稍等一下。")
            return
        self.detach_terminal()
        try:
            process, known = spawn_console(item["path"], cont, prompt, settings,
                                           permission)
        except Exception as e:
            messagebox.showerror("启动失败", "启动 claude 失败：\n{}".format(e))
            return
        self._pending = (process, known, item, 0)
        self.feedback_var.set("正在把 claude 装进窗口…")
        self.after(80, self._poll_console)

    def _poll_console(self):
        """控制台窗口是异步建的，拿到之前一直轮询，别把界面卡住。"""
        process, known, item, tries = self._pending
        hwnd = fresh_console(known)
        if hwnd is None:
            if tries >= 75:
                self._pending = None
                self.feedback_var.set("等不到控制台窗口，claude 可能已经退出了。")
                return
            self._pending = (process, known, item, tries + 1)
            self.after(80, self._poll_console)
            return

        self._pending = None
        self.term_name_var.set(item["path"])
        self.term_head.pack(fill="x", pady=(0, 6))
        self.term_holder.pack(fill="both", expand=True)
        self._room_for_terminal(True)
        self.update_idletasks()
        self.embedded = EmbeddedConsole(process, hwnd, self.term_holder)
        self.track_running(item["name"], item["path"], process)
        self.feedback_var.set(
            "claude 已内嵌在 {}。conhost 没有字体回退，个别符号会是方框。"
            .format(item["path"]))

    def _room_for_terminal(self, want):
        """内嵌时左列收窄、终端从右侧长出来，窗口不够宽就往右撑；退出去还原。"""
        if want:
            self._width_before_embed = self.winfo_width()
            self.panel.pack(side="left", fill="both", expand=True, padx=(14, 0))
            self.side.configure(width=SIDE_WIDTH)
            self.side.pack_propagate(False)
            self.side.pack_configure(fill="y", expand=False)
            self._widen(SIDE_WIDTH + TERMINAL_MIN_WIDTH)
        else:
            self.panel.pack_forget()
            self.side.pack_propagate(True)
            self.side.pack_configure(fill="both", expand=True)
            if self._width_before_embed:
                self._widen(self._width_before_embed)
            self._width_before_embed = None
        self.model_list.fit()
        self.ws_list.fit()

    def _widen(self, width):
        """改宽度，并保证整扇窗口还留在虚拟桌面内——贴到右边缘就往左挪。"""
        if not width:
            return
        u = ctypes.windll.user32
        left = u.GetSystemMetrics(76)
        right = left + u.GetSystemMetrics(78)
        width = min(width, right - left)
        x, y = window_position(self)
        if x + width > right:
            x = max(left, right - width)
        self.geometry("{}x{}".format(width, self.winfo_height()))
        self.update_idletasks()
        place_window(self, x, y)

    def detach_terminal(self):
        """把内嵌的控制台放回独立窗口，进程不动。"""
        if not self.embedded:
            return
        try:
            self.embedded.detach()
        except Exception:
            pass
        self.embedded = None
        self.term_head.pack_forget()
        self.term_holder.pack_forget()
        self.term_name_var.set("")
        self._room_for_terminal(False)

    def _on_term_resize(self, _event):
        if self.embedded and not self._placing:
            self._placing = True
            try:
                self.embedded.place()
            finally:
                self._placing = False

    def open_folder(self, path):
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        os.startfile(path)

    def _open_tool_dir(self):
        """顶栏那个按钮：把 ~/.claude_tool 用资源管理器打开。

        配置、模型预设、交接文档的记账本都在这儿，手改的时候比在界面里点来点去快。
        目录不存在就先建出来——首次启动本来就该有，但用户手删了也别报个错就完事。
        """
        try:
            os.makedirs(TOOL_DIR, exist_ok=True)
            os.startfile(TOOL_DIR)
        except OSError as exc:
            messagebox.showerror("打不开", "打开 {} 失败：\n{}".format(TOOL_DIR, exc))

    def write_handoff(self, item):
        """点"正在跑"那块里的按钮：在那个目录里 fork 一份会话，让它写 handoff.md。"""
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        if self._handoff_proc is not None and self._handoff_proc.poll() is None:
            self.feedback_var.set("上一份交接文档还在写，等它写完。")
            return

        target = os.path.join(path, HANDOFF_FILE)
        try:
            before = os.path.getmtime(target)
        except OSError:
            before = 0.0

        # 输出导到临时文件而不是管道：没人读的管道写满会把子进程卡死。
        log = tempfile.NamedTemporaryFile(prefix="handoff_", suffix=".log",
                                          delete=False)
        try:
            self._handoff_proc = subprocess.Popen(
                [claude_exe(), "-c", "--fork-session", "-p", HANDOFF_PROMPT,
                 "--allowedTools", HANDOFF_TOOLS],
                cwd=path, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            log.close()
            os.remove(log.name)
            messagebox.showerror("启动失败", "叫不起 claude：\n{}".format(e))
            return
        log.close()

        self._handoff_ctx = (item, target, before)
        self._handoff_log = log.name
        self.feedback_var.set(
            "正在给「{}」整理交接文档，可能要几十秒，别关这个窗口。".format(item["name"]))
        self.after(400, self._poll_handoff)

    def _poll_handoff(self):
        """claude 是另一个进程，只能轮询着等它。"""
        proc = self._handoff_proc
        if proc is None:
            return
        if proc.poll() is None:
            self.after(500, self._poll_handoff)
            return

        self._handoff_proc = None
        item, target, before = self._handoff_ctx
        self._handoff_ctx = None
        detail = self._read_handoff_log()

        if os.path.exists(target) and os.path.getmtime(target) > before:
            self.feedback_var.set(
                "「{}」的交接文档写好了：{}".format(item["name"], target))
            return
        if proc.returncode != 0:
            self.feedback_var.set("写交接文档失败：{}".format(
                detail or "退出码 {}".format(proc.returncode)))
            return
        self.feedback_var.set(
            "跑完了但没生成 {}——这个目录可能还没聊过，没有会话可以交接。".format(HANDOFF_FILE))

    def _read_handoff_log(self, limit=160):
        """把 claude 吐的最后一句拿来做失败原因，然后清掉临时文件。"""
        text = ""
        try:
            with open(self._handoff_log, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError:
            pass
        try:
            os.remove(self._handoff_log)
        except OSError:
            pass
        self._handoff_log = None
        return " ".join(text.split())[-limit:]

    def rename_workspace(self, item):
        new_name = simpledialog.askstring("重命名工作区", "显示名称：",
                                          initialvalue=item["name"], parent=self)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showerror("名称为空", "显示名称不能为空。")
            return
        item["name"] = new_name
        save_config(self.config_data)
        self.refresh_workspaces()
        self.feedback_var.set("已重命名为 {}。".format(new_name))

    def remove_workspace(self, item):
        if not messagebox.askyesno(
                "移除工作区",
                "把「{}」从列表里拿掉？\n目录本身不会被删除。".format(item["name"])):
            return
        index = next((i for i, w in enumerate(self.config_data["workspaces"])
                      if w is item), -1)
        if index < 0:
            return
        del self.config_data["workspaces"][index]
        save_config(self.config_data)
        self.refresh_workspaces()
        self.feedback_var.set("已移除 {}。".format(item["name"]))

    def _set_workplace(self, base):
        """换默认工作区目录：老的那个从扫描列表里换掉，别的扫描目录留着。"""
        old = self.config_data.get("workplace")
        self.config_data["workplace"] = base
        roots = list(self.config_data["roots"])
        if old:
            roots = [r for r in roots if path_key(r) != path_key(old)]
        if all(path_key(r) != path_key(base) for r in roots):
            roots.insert(0, base)
        self.config_data["roots"] = roots
        self._refresh_workplace_note()

    def _build_workplace_note(self, parent):
        """工作区列表顶上那行：新建的文件夹建在哪儿，以及改它的按钮。"""
        line = tk.Frame(parent, bg=PAGE_BG)
        line.pack(fill="x", pady=(0, 6))
        tk.Label(line, text="新建文件夹建在", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(2, 6))
        # 按钮先 pack 把它那份宽度占住，否则长路径会把按钮挤出窗口。
        PillButton(line, "更改目录", self._open_workplace_dialog,
                   bg=PAGE_BG).pack(side="right")
        self.workplace_label = tk.Label(line, bg=PAGE_BG, fg=TEXT, font=font(9),
                                        anchor="w")
        self.workplace_label.pack(side="left", fill="x", expand=True)
        # 宽度是布局给的，只有 Configure 之后才知道，所以每次都按当前宽度重算
        self.workplace_label.bind("<Configure>", lambda _e: self._refresh_workplace_note())
        self._refresh_workplace_note()

    def _refresh_workplace_note(self):
        room = max(self.workplace_label.winfo_width() - 4, 60)
        text = ellipsize(self.config_data["workplace"], room, 9)
        if self.workplace_label.cget("text") != text:
            self.workplace_label.configure(text=text)

    def _open_workplace_dialog(self):
        """改默认工作区目录。选完就落盘，以后「＋ 新建文件夹」都建在这儿。"""
        current = self.config_data["workplace"]
        chosen = filedialog.askdirectory(
            parent=self, title="选择默认工作区目录",
            initialdir=current if os.path.isdir(current) else TOOL_DIR)
        if not chosen:
            return
        base = os.path.normpath(chosen)
        if path_key(base) == path_key(current):
            return
        self._set_workplace(base)
        save_config(self.config_data)
        self.feedback_var.set(
            "默认工作区目录改成 {}，以后「＋ 新建文件夹」就建在这儿。".format(base))

    def _open_quick_workspace_dialog(self):
        """「＋ 新建文件夹」：在默认工作区目录下面建一个新文件夹，当工作区用。"""
        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "新建工作区文件夹")

        tk.Label(body, text="文件夹名称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        folder_var = tk.StringVar()
        folder_entry = make_entry(body, folder_var, width=32)
        folder_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        tk.Label(body, text="简称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        name_var = tk.StringVar()
        make_entry(body, name_var, width=32).grid(row=1, column=1, sticky="ew",
                                                  padx=(12, 0), pady=4)
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="简称留空就用文件夹名。", bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").grid(row=2, column=0, columnspan=2,
                                                sticky="w", pady=(2, 0))

        base_var = tk.StringVar(value=self.config_data["workplace"])
        line = tk.Frame(body, bg=PAGE_BG)
        line.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        tk.Label(line, text="建在", bg=PAGE_BG, fg=MUTED, font=font(9),
                 ).pack(side="left")
        tk.Label(line, textvariable=base_var, bg=PAGE_BG, fg=TEXT, font=font(9),
                 anchor="w").pack(side="left", padx=(6, 8), fill="x", expand=True)

        def choose_base():
            chosen = filedialog.askdirectory(parent=dialog, title="选择默认工作区目录",
                                             initialdir=base_var.get() or TOOL_DIR)
            if chosen:
                base_var.set(os.path.normpath(chosen))

        PillButton(line, "更改目录", choose_base, bg=PAGE_BG).pack(side="right")

        save = lambda: self._save_quick_workspace(dialog, folder_var, name_var, base_var)
        finish_form(dialog, [("创建", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        folder_entry.focus_set()
        self._center(dialog)

    def _save_quick_workspace(self, dialog, folder_var, name_var, base_var):
        folder = folder_var.get().strip()
        if not folder:
            messagebox.showerror("缺少名称", "请填写文件夹名称。", parent=dialog)
            return
        if re.search(ILLEGAL_CHARS, folder):
            messagebox.showerror(
                "名称非法",
                "文件夹名称不能包含 < > : \" / \\ | ? * 和空格。", parent=dialog)
            return

        base = os.path.normpath(base_var.get().strip() or WORKPLACE_DIR)
        target = os.path.join(base, folder)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            messagebox.showerror("创建失败", "建不了目录：\n{}".format(e), parent=dialog)
            return
        if not os.path.isdir(target):
            messagebox.showerror("创建失败", "目录没建起来：\n{}".format(target),
                                 parent=dialog)
            return

        name = name_var.get().strip() or folder
        known = {path_key(w["path"]) for w in self.config_data["workspaces"]}
        if path_key(target) not in known:
            self.config_data["workspaces"].append({"name": name, "path": target})

        self._set_workplace(base)
        save_config(self.config_data)
        dialog.destroy()
        self.refresh_workspaces()
        self.feedback_var.set("已建好工作区 {}：{}".format(name, target))

    def _open_add_workspace_dialog(self):
        """「＋ 选已有目录」：挑一个已经存在的目录，加进列表里当工作区。"""
        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "选已有目录当工作区")

        tk.Label(body, text="显示名称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        name_var = tk.StringVar()
        name_entry = make_entry(body, name_var, width=38)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        tk.Label(body, text="目录路径", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        path_var = tk.StringVar()
        make_entry(body, path_var, width=38).grid(row=1, column=1, sticky="ew",
                                                  padx=(12, 0), pady=4)

        def browse():
            chosen = filedialog.askdirectory(parent=dialog, title="选择工作区目录")
            if chosen:
                chosen = os.path.normpath(chosen)
                path_var.set(chosen)
                if not name_var.get().strip():
                    name_var.set(os.path.basename(chosen))

        PillButton(body, "浏览…", browse, bg=PAGE_BG).grid(row=1, column=2, padx=(8, 0))
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="显示名称留空就用文件夹名。", bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").grid(row=3, column=0, columnspan=3,
                                                sticky="w", pady=(10, 0))

        save = lambda: self._save_new_workspace(dialog, name_var, path_var)
        finish_form(dialog, [("保存", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        name_entry.focus_set()
        self._center(dialog)

    def _save_new_workspace(self, dialog, name_var, path_var):
        path = os.path.normpath(path_var.get().strip())
        if not path or path == ".":
            messagebox.showerror("缺少路径", "请填写目录路径。", parent=dialog)
            return
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path), parent=dialog)
            return

        name = name_var.get().strip() or os.path.basename(path)
        known = {os.path.normcase(os.path.normpath(w["path"]))
                 for w in self.config_data["workspaces"]}
        if os.path.normcase(path) in known:
            messagebox.showerror("重复添加", "这个目录已经在列表里了。", parent=dialog)
            return

        self.config_data["workspaces"].append({"name": name, "path": path})
        save_config(self.config_data)
        dialog.destroy()
        self.refresh_workspaces()
        self.feedback_var.set("已添加工作区 {}。".format(name))

    def rescan(self):
        added = merge_scanned(self.config_data)
        save_config(self.config_data)
        self.refresh_workspaces()
        if added:
            self.feedback_var.set("新发现 {} 个工作区：{}".format(len(added), "、".join(added)))
        else:
            self.feedback_var.set("扫描目录下没有新工作区。")

    def _center(self, dialog):
        """把对话框摆到主窗口中间。"""
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry("+{}+{}".format(x, y))


if __name__ == "__main__":
    # Claude Code 的 Stop hook 就是回头调这个文件，所以要先认这个参数——
    # 这条路上不能碰 tkinter，也不该弹出任何窗口。
    if HOOK_FLAG in sys.argv:
        sys.exit(run_handoff_hook())
    Launcher().mainloop()
