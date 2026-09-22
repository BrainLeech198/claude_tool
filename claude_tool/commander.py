"""第 2 档那个"替用户拍板"的副手。

主模型摆选项框问人的时候用户不在，没人点。这一档让另一个模型读一遍这个会话最近
发生了什么，替用户把那个问题答了；答案再当成理由还回主模型，让它接着干（走的是
handoff 里 PreToolUse 拦 AskUserQuestion 那一路）。

三条守则，都是设计说明里定下的：

- **它只答，不做。**「谁来答它的问题」和「谁允许它动手」是两个旋钮，各管各的。
  答完之后真正执行仍按那个会话自己的权限等级走。
- **答不出来就交回去。** 超时、报错、答案对不上任何一个选项，一律返回 `None`，
  由调用方退回第 1 档那句「你自己定」。不能因为一个答不上来的副手把用户的目标
  卡死在半路。
- **不碰会话。** 只读 `transcript_path` 那个文件，只发一次 HTTP，不写任何东西。

走的是 `presets.test_preset` 那条**已经验过**的裸 HTTP 路：同一个 endpoint、同一套
请求头。不走 `claude -p`——那条路一次要带上两万多 token 的系统提示词（探针里量到
的），替人拍个板不值当。
"""
import json
import os
import re
import urllib.error
import urllib.request

from claude_tool.presets import preset_path, read_env
from claude_tool.providers import reply_text

# 上下文只取最近这些字符。会话记录那个 jsonl 长起来能到几十兆，整个读进来纯属浪费，
# 而且指挥模型要判断的是"眼下这一步"，远处那些轮次帮不上忙还稀释重点。
CONTEXT_CHARS = 8000
# 取回来的字符还要再截一次：一轮里塞着一整篇文件内容是常事。
TURN_CHARS = 1200
MAX_TURNS = 24

# 超时压在 30 秒以内：Claude Code 给 hook 的默认上限是 60 秒，而这条路失败了大不了
# 退回第 1 档，没必要让用户干等。
DEFAULT_TIMEOUT = 25
MAX_TOKENS = 1024

SYSTEM = (
    "你在替一个不在电脑前的用户拍板。你会看到一段 AI 编程会话最近的经过，以及那个 AI "
    "正要向用户提的问题。按上下文挑一个最合理的选项，你不要自己去执行任何操作，只做选择。"
    "只输出一个 JSON 对象，不要解释、不要用代码块包起来，格式：\n"
    '{"answers": [{"header": "问题标题", "choice": "选中的选项label"}]}\n'
    "有多个问题时每个都要给一条。某题允许多选时，choice 用一个数组装多个 label。"
    "必须从给定选项的 label 里原样挑，不要自己造新的。"
)

ANSWER_RE = re.compile(r"\{.*\}", re.S)


def read_context(path, limit=CONTEXT_CHARS):
    """从会话记录里抠出最近这几轮，当指挥模型的上下文。

    文件是一行一条 JSON 的 jsonl。从尾部倒着读，凑够 limit 就停——长会话那个文件
    能有几十兆，整个读进来纯属浪费。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            # 中文一个字三个字节，按四倍留余量，免得切在半个字上
            if size > limit * 4:
                f.seek(size - limit * 4)
                f.readline()          # 从半行处切开的头一截丢掉
            raw = f.read()
    except OSError:
        return []

    turns = []
    for line in raw.decode("utf-8", "replace").splitlines():
        turn = _read_turn(line)
        if turn:
            turns.append(turn)
    return turns[-MAX_TURNS:]


def _read_turn(line):
    """一条 jsonl -> "用户：…" / "助手：…" 这样一行。读不出就返回空串。"""
    try:
        record = json.loads(line)
    except Exception:
        return ""
    if not isinstance(record, dict):
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    role = message.get("role")
    if role not in ("user", "assistant"):
        return ""
    text = _content_text(message.get("content"))
    if not text:
        return ""
    return "{}：{}".format("用户" if role == "user" else "助手",
                           text[:TURN_CHARS])


def _content_text(content):
    """会话记录里 content 有两种长相：一整段字符串，或者一列内容块。

    块的种类很多（text / tool_use / tool_result / thinking …）。这里只留 text，
    工具调用缩成一行名字——工具返回那坨文件内容对"该选哪个选项"没帮助，只会把
    上下文撑爆。
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text") or "").strip())
        elif kind == "tool_use":
            parts.append("[调用 {}]".format(block.get("name") or "工具"))
        elif kind == "tool_result":
            parts.append("[工具返回]")
    return " ".join(p for p in parts if p).strip()


def build_prompt(questions, turns):
    """把上下文和那个问题拼成一段话。"""
    lines = ["这个会话最近是这样的：", ""]
    lines += turns or ["（读不到会话记录）"]
    lines += ["", "现在它要问用户的是：", ""]
    for question in questions:
        lines.append("- 标题：{}".format(question.get("header") or ""))
        lines.append("  问题：{}".format(question.get("question") or ""))
        lines.append("  可选项：")
        for option in question.get("options") or []:
            lines.append("    · label：{}  —— {}".format(
                option.get("label") or "",
                (option.get("description") or "")[:160]))
        if question.get("multiSelect"):
            lines.append("  （这题可以多选）")
    lines += ["", "按上下文替他选，只回那个 JSON。"]
    return "\n".join(lines)


def parse_reply(text, questions):
    """把模型的回话解析成 [(标题, 选择)]；对不上任何一个选项就返回 None。

    解析松弛、校验严格：外面套着解释或代码块都认（正则抠第一个 JSON），但挑出来的
    label 必须原样出现在选项表里。答非所问宁可判失败交回第 1 档，也别把一句编出来的
    选择当成用户的意思喂回去。
    """
    found = ANSWER_RE.search(text or "")
    if not found:
        return None
    try:
        body = json.loads(found.group(0))
    except Exception:
        return None
    answers = body.get("answers")
    if not isinstance(answers, list):
        return None

    by_header = {}
    for item in answers:
        if isinstance(item, dict):
            by_header[str(item.get("header") or "")] = item.get("choice")

    picked = []
    for question in questions:
        header = question.get("header") or ""
        choice = by_header.get(header)
        if choice is None:
            return None               # 有一题没答，整份不要
        labels = option_labels(question)
        if isinstance(choice, list):
            chosen = [_match_label(one, labels) for one in choice]
            chosen = [one for one in chosen if one]
            if not chosen:
                return None
        else:
            chosen = _match_label(choice, labels)
            if not chosen:
                return None
        picked.append((header, "、".join(chosen) if isinstance(chosen, list)
                       else chosen))
    return picked or None


def option_labels(question):
    labels = []
    for option in question.get("options") or []:
        if isinstance(option, dict) and option.get("label"):
            labels.append(str(option["label"]))
    return labels


def _match_label(choice, labels):
    """挑出来的字符串对上哪个 label。对不上返回 None。

    先原样，再折叠空白和大小写，最后认"包含了整个 label"——模型爱在 label 后面
    补一句为什么，那是可以接受的；反过来 label 只是它那句话的一小截时也认。
    """
    text = str(choice).strip()
    if text in labels:
        return text
    flat = re.sub(r"\s+", "", text).lower()
    for label in labels:
        if re.sub(r"\s+", "", label).lower() == flat:
            return label
    for label in labels:
        if re.sub(r"\s+", "", label).lower() in flat:
            return label
    return None


def ask(name, questions, transcript_path, timeout=DEFAULT_TIMEOUT):
    """让预设 `name` 那个模型替用户拍板。返回 [(标题, 选择)] 或 None。

    任何一步出岔子都返回 None——配置读不出来、端点连不上、回话不是 JSON、
    label 对不上，一律交给调用方退回第 1 档。
    """
    if not name or not questions:
        return None
    env = read_env(preset_path(name))
    base = str(env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    token = str(env.get("ANTHROPIC_AUTH_TOKEN") or "")
    model = str(env.get("ANTHROPIC_MODEL") or "")
    if not (base and token and model):
        return None

    prompt = build_prompt(questions, read_context(transcript_path))
    body = json.dumps({
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
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
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return parse_reply(reply_text(reply), questions)
