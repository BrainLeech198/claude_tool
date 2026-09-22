"""那张「常用供应商」表：内置一份打底，可以被外部配置覆盖，也可以被刷新。

原来是写死在 presets.py 里的 11 行——厂家和模型名都会过时，过时了只能改代码跟。
现在读 ~/.claude_tool/providers.json；文件不在、读坏了、或者一条合法行都没有，就
回退到内置那份，所以升级上来的用户行为跟以前一模一样。

刷新有两条路，都要用户在「添加模型」那扇窗里手动点，不会自己跑：

- query_with_ai()：拿用户自己已经配好的某个预设，打一次 Messages API，让它把当下
  各家在卖什么、地址是什么列成 JSON。
- fetch_community()：从仓库 Pages（不通再试 Gitee 那份）拉一份维护好的同格式文件。

两条路都只回行、不写盘。写盘是用户在复核列表里勾完之后调 save_providers()——
AI 报的地址是可能编的，直接落盘等于拿用户当小白鼠。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import NamedTuple

from claude_tool.paths import ILLEGAL_FILE_CHARS, TOOL_DIR

PROVIDERS_FILE = os.path.join(TOOL_DIR, "providers.json")

# 社区维护的那份清单放仓库 docs/ 下，Pages 就从那儿出。Gitee 那份是退路：这仓库的
# 主力在 Gitee 上，而 github.com 在有些网络里根本不通（Pages 也跟着不通）。
COMMUNITY_URLS = (
    "https://brainleech198.github.io/claude_tool/providers.json",
    "https://gitee.com/Mr_brainleech/claude_tool/raw/master/docs/providers.json",
)

# 一次最多收这么多家。列表是要人一行行看的，给回来五十条没人看得下去，而且
# 越到后面越是模型自己凑数编的。
MAX_ROWS = 20

AI_TIMEOUT = 60
AI_MAX_TOKENS = 2048


class Provider(NamedTuple):
    """一家：下拉里显示的名字、存成预设时的名字、地址、模型名。

    NamedTuple 是为了两边都顺手——老代码按元组拆（`for name, preset, base, model
    in rows`）照旧能跑，新代码想要 `row.base_url` 也行。
    """
    name: str
    preset: str
    base_url: str
    model: str


# 内置那份。下拉里的名字 = 名称那一格自动填的内容，所以两列大多数是同一样东西。
#
# Base URL 全部实测过：拿假 key 打 <base>/v1/messages，回 401/403 说明端点存在，
# 同时往同域名的假路径打一次确认它回 404——否则一个"什么都回 401"的网关会把
# 不存在的路径也伪装成存在。下面这些是过了双重检查的。
# （OpenAI 格式的 /v1/chat/completions 端点不算数，Claude Code 只认 Messages API。）
#
# 模型名是照各家公开文档填的，会过时；聚合平台的模型名还得照它们的目录填。过时了
# 不用改代码：在「添加模型」那扇窗里点「更新这张表」，或者直接手改 providers.json。

BUILTIN = (
    Provider("DeepSeek", "DeepSeek", "https://api.deepseek.com/anthropic",
             "deepseek-chat"),
    Provider("智谱 GLM", "智谱 GLM", "https://open.bigmodel.cn/api/anthropic",
             "glm-4.6"),
    Provider("Kimi", "月之暗面 Kimi", "https://api.moonshot.cn/anthropic",
             "kimi-k2-turbo-preview"),
    Provider("硅基流动", "硅基流动", "https://api.siliconflow.cn",
             "deepseek-ai/DeepSeek-V3"),
    # 国外的聚合平台：一个 key 能用很多家的模型
    Provider("OpenRouter", "OpenRouter", "https://openrouter.ai/api",
             "anthropic/claude-sonnet-4.6"),
    Provider("Vercel AI 网关", "Vercel AI 网关", "https://ai-gateway.vercel.sh",
             "anthropic/claude-sonnet-4.6"),
    Provider("Requesty", "Requesty", "https://router.requesty.ai",
             "anthropic/claude-sonnet-4.6"),
    Provider("Nano-GPT", "Nano-GPT", "https://nano-gpt.com/api",
             "claude-sonnet-4.6"),
    # 国内厂商的海外站
    Provider("MiniMax 国际", "MiniMax 国际", "https://api.minimax.io/anthropic",
             "MiniMax-M2"),
    Provider("Moonshot 国际", "Moonshot 国际", "https://api.moonshot.ai/anthropic",
             "kimi-k2-turbo-preview"),
    # 官方
    Provider("Anthropic 官方", "Anthropic 官方", "https://api.anthropic.com",
             "claude-sonnet-4-6"),
)

# 来源写进文件里，界面上好直说是"AI 查的"还是"社区清单拉的"
SOURCE_LABELS = {"ai": "AI 查询", "community": "社区清单"}

ARRAY_RE = re.compile(r"\[.*\]", re.S)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _row(item):
    """一条外部数据 -> Provider；字段不全、地址不像话、名字存不下去就返回 None。

    providers.json 是给人手改的，AI 那条路更是不可控，所以两道进来的口子都按同一
    把尺子过：四样都得是非空字符串，地址得是 http(s)，名字得能落成文件名。坏行整条
    丢——猜出来的地址写进表里，用户下一步就是拿它去打请求。

    preset 缺了就跟着 name 走：两列大多数时候本来就是同一个东西，而这是唯一一处
    补默认值不会造成误解的地方。
    """
    if not isinstance(item, dict):
        return None
    name = _text(item.get("name"))
    preset = _text(item.get("preset")) or name
    base_url = _text(item.get("base_url"))
    model = _text(item.get("model"))
    if not (name and base_url and model):
        return None
    if not base_url.startswith(("http://", "https://")):
        return None
    # 名字存不下去的话，从下拉里挑中它就是往表单里填一个保存不了的名称
    if re.search(ILLEGAL_FILE_CHARS, name) or re.search(ILLEGAL_FILE_CHARS, preset):
        return None
    return Provider(name, preset, base_url, model)


def parse_rows(payload):
    """从解出来的 JSON 里抠出合法行，返回 (行, 说明)。

    说明是能直接摆到界面上的那句话：成的时候是"12 条，丢掉了 2 条不合格的"，败的
    时候是"一条能用的都没有"。调用方两种都照原样显示。
    """
    if isinstance(payload, dict):
        payload = payload.get("rows")
    if not isinstance(payload, list):
        return [], "回话里没有一张表"
    rows, seen, dropped = [], set(), 0
    for item in payload:
        row = _row(item)
        if row is None or row.name in seen:
            dropped += 1
            continue
        seen.add(row.name)
        rows.append(row)
        if len(rows) >= MAX_ROWS:
            break
    if not rows:
        return [], "一条能用的都没有"
    note = "{} 条".format(len(rows))
    if dropped:
        note += "，丢掉了 {} 条不合格的".format(dropped)
    return rows, note


def load_table():
    """读配置，返回 (行, 元信息)。元信息是 {"updated", "source"}，内置那份给空串。

    读不出来就用内置那份——一份坏掉的 providers.json 不该让用户在下拉里一片空白。
    """
    meta = {"updated": "", "source": ""}
    try:
        with open(PROVIDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return list(BUILTIN), meta
    rows, _note = parse_rows(data)
    if not rows:
        return list(BUILTIN), meta
    if isinstance(data, dict):
        meta["updated"] = _text(data.get("updated"))
        meta["source"] = _text(data.get("source"))
    return rows, meta


def load_providers():
    """只要那几行，不要元信息。下拉、提示语这些地方用的就是它。"""
    return load_table()[0]


def save_providers(rows, source):
    """写盘，返回写到的路径。写不进去抛 OSError，由调用方去提示用户。"""
    os.makedirs(TOOL_DIR, exist_ok=True)
    data = {
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "source": source,
        "rows": [row._asdict() for row in rows],
    }
    with open(PROVIDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return PROVIDERS_FILE


# ── 两条取数的路 ──────────────────────────────────────────────────────────

# 示例那一行单独拎出来：它满是花括号，混在下面那段里一起做 .format() 会被当成
# 占位符（KeyError: '"name"'）。
ROW_SHAPE = ('{"name": "下拉里显示的短名", "preset": "存成预设时用的名字", '
             '"base_url": "地址", "model": "模型名"}')

SYSTEM = "".join([
    "你在帮一个 Claude Code 启动器维护它的「常用供应商」表。一行说的是：一家服务商、",
    "一个能接 Anthropic Messages API 的地址、它在卖的一个模型名。",
    "只输出一个 JSON 数组，不要解释、不要用代码块包起来，每一项的格式：\n",
    ROW_SHAPE, "\n",
    "name 和 preset 填同一家的同一个名字就行。base_url 必须是 Messages API 的地址",
    "（官方端点通常是自家域名后面加 /anthropic，聚合平台用它自己的域名），",
    "拿不准的整条别写。最多 {} 条。".format(MAX_ROWS),
])


def _prompt(current):
    """把现有那张表也递过去：让它照着改，而不是凭空想一套新的。"""
    lines = ["现在这张表是这样（名字 / 地址 / 模型名）："]
    for row in current:
        lines.append("- {} / {} / {}".format(row.name, row.base_url, row.model))
    lines += [
        "",
        "请给出你现在确定还在卖、地址还有效的那些服务商（国内的和海外聚合平台都要）。"
        "尽量沿用上面这些家的名字，模型名换成当下在售的，地址变了就写新地址；"
        "觉得该添的也添上。不要编造你不确定的地址。只输出 JSON 数组。",
    ]
    return "\n".join(lines)


def reply_text(reply):
    """Messages API 那份回话里把文字抠出来。"""
    if not isinstance(reply, dict):
        return ""
    parts = []
    for block in reply.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def query_with_ai(base, token, model, current=(), timeout=AI_TIMEOUT):
    """拿一个已经配好的预设去打一次 Messages API，让它把当下的清单列出来。

    返回 (行, 说明)。这里跟 commander.ask 那种"出了岔子就悄悄退回上一档"不一样：
    用户正盯着这扇窗等结果，所以每种失败都得回一句能看懂的话。
    """
    base = (base or "").rstrip("/")
    if not (base and token and model):
        return [], "这个预设没配全（地址/密钥/模型名缺一样），换一个再试。"
    body = json.dumps({
        "model": model,
        "max_tokens": AI_MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": _prompt(current)}],
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/v1/messages", data=body,
        headers={
            "content-type": "application/json",
            "authorization": "Bearer " + token,
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return [], "这个模型不认（HTTP {}），换一个再试。".format(e.code)
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", "") or "").strip()
        return [], "连不上 · " + reason if reason else "连不上"
    except (OSError, ValueError) as e:
        return [], "回话读不出来（{}）".format(type(e).__name__)

    found = ARRAY_RE.search(reply_text(reply))
    if not found:
        return [], "回话里没有 JSON 数组"
    try:
        payload = json.loads(found.group(0))
    except ValueError:
        return [], "回话里那段 JSON 没解开"
    return parse_rows(payload)


def fetch_community(urls=COMMUNITY_URLS, timeout=20):
    """拉社区维护的那份表，前面那份不通就试下一份。"""
    last = "拉不到"
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = "HTTP {}".format(e.code)
            continue
        except urllib.error.URLError:
            last = "连不上"
            continue
        except (OSError, ValueError):
            last = "那份文件读不出来"
            continue
        rows, note = parse_rows(payload)
        if rows:
            return rows, note
        last = note
    return [], last
