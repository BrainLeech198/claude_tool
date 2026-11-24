"""本地协议转换网关：Anthropic Messages API -> OpenAI Responses API。

## 为什么要有这个

Claude Code 只认 Messages API：它把 ANTHROPIC_BASE_URL 后面拼上 /v1/messages
就发请求。而 OpenAI 官方只给 /v1/responses（而且带工具调用时 chat/completions
那条路会直接 400）。中间缺一层翻译，这个模块就是那层。

只翻译跑到本地 127.0.0.1 上的一个小服务，**不引任何第三方库**——跟托盘那套
ctypes 一样，启动器要能免安装跑起来，多一个依赖就多一份打包和升级的账。

## 进程模型

它是**独立后台进程**，不是长在启动器窗口里的线程。原因很实在：Claude Code 的
会话是另开的终端窗口，用户把启动器退了，正在跑的会话不该跟着断线。所以：

- `claude_tool gateway --serve` 是前台那条路，`ensure()` 用 DETACHED_PROCESS
  把它甩到后台；
- 端口和令牌落在 ~/.claude_tool/gateway.json，下次起来先按原来的端口和令牌
  试，能连上就直接用；
- 闲够 IDLE_SECONDS 自己退，不常驻到天荒地老。

## 路由

    http://127.0.0.1:<port>/a/<agent>/<预设名>/v1/messages
    http://127.0.0.1:<port>/a/<agent>/<预设名>/v1/messages/count_tokens

预设名和 agent 都在路径里，所以一个网关能同时服务好几份预设、好几个 agent，
上游地址和 key 现读预设文件（用户改了不用重启网关）。上游 key **不出这个进程**
——Claude Code 那边拿到的是网关自己的一次性令牌。

## 只做 Responses

供应商表里那几家（DeepSeek、Kimi、GLM…）本来就提供 /anthropic 入口，用不着
转换。真正缺的只有 OpenAI 官方这种只给原生协议的模型，而它在 chat/completions
上不支持「工具 + 推理」，官方也让你走 /responses。所以第一版只翻 Responses，
不做 chat/completions，少一套要跟的细节。
"""
import hmac
import http.server
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from claude_tool.paths import TOOL_DIR

HOST = "127.0.0.1"
STATE_FILE = os.path.join(TOOL_DIR, "gateway.json")
LOG_FILE = os.path.join(TOOL_DIR, "gateway.log")

APP_MARK = "claude_tool.gateway"
PROTOCOL = "openai-responses"

# 上游一次生成可能要几分钟，读超时给得比普通请求宽很多。
UPSTREAM_TIMEOUT = 600
# 起来之后闲这么久就自己退。正在用的会话一直在发请求，12 小时足够覆盖“开着窗口
# 去开会/睡觉”的停顿，又不会让一个没人用的进程留到下一次重启。
IDLE_SECONDS = 12 * 60 * 60
# ensure() 里等后台进程起来的耐心。冷启动要解包/导入，三秒左右是正常的。
START_TIMEOUT = 20

MAX_LOG_BYTES = 1024 * 1024

ROUTE_RE = re.compile(
    r"^/a/([^/]+)/([^/]+)(/v1/messages(?:/count_tokens)?|/v1/models)$")


class GatewayError(RuntimeError):
    """网关起不来、或者这份预设没法交给它。消息直接摆给用户看。"""


# ── 状态文件 / 进程生命周期 ───────────────────────────────────────────────


def base_url(port):
    return "http://{}:{}".format(HOST, int(port))


def route_base(port, agent_id, name):
    """某份预设该用的 ANTHROPIC_BASE_URL。

    预设名可能带中文和空格，路径里要转义；Claude Code 会原样拼 /v1/messages。
    """
    return "{}/a/{}/{}".format(base_url(port),
                               urllib.parse.quote(str(agent_id), safe=""),
                               urllib.parse.quote(str(name), safe=""))


def _read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        data["port"] = int(data.get("port"))
    except (TypeError, ValueError):
        return {}
    data["token"] = str(data.get("token") or "")
    return data


def _write_state(port, token):
    os.makedirs(TOOL_DIR, exist_ok=True)
    data = {
        "app": APP_MARK,
        "port": int(port),
        "token": token,
        "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    temp = STATE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, STATE_FILE)


def _healthy(port, timeout=0.8):
    if not port:
        return False
    try:
        with urllib.request.urlopen(
                base_url(port) + "/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("app") == APP_MARK


def status():
    """网关现在什么状况。running 为假时，port/token 可能是上次留下的旧值。"""
    state = _read_state()
    running = bool(state.get("port")) and _healthy(state["port"])
    result = dict(state)
    result["running"] = running
    if running:
        result["base_url"] = base_url(state["port"])
    return result


def preset_url(agent_id, name):
    """现查网关、给某份预设拼路由地址；网关没起来就抛 GatewayError。"""
    state = ensure()
    return route_base(state["port"], agent_id, name)


def ensure(wait=START_TIMEOUT):
    """确保后台网关在跑，返回状态（含 port/token）。起不来抛 GatewayError。

    在界面线程里也会被调到（第一次开会话之前），所以已经起着时只做一次 0.8 秒
    上下的健康检查就返回；只有真需要冷启动才花那几秒。
    """
    state = status()
    if state.get("running"):
        return state

    _spawn()
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.15)
        state = status()
        if state.get("running"):
            return state
    raise GatewayError(
        "本地转换网关没起来。看看 {} 里的报错；也可以在命令行跑 "
        "`claude_tool gateway --serve` 看它卡在哪。".format(LOG_FILE))


def _spawn():
    """把 --serve 甩到后台。已经有一个在起也照甩，起来那个会自己让位。"""
    os.makedirs(TOOL_DIR, exist_ok=True)
    try:
        if os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".old")
    except OSError:
        pass

    if getattr(sys, "frozen", False):
        argv = [sys.executable, "gateway", "--serve"]
        cwd = None
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        argv = [sys.executable, "-m", "claude_tool", "gateway", "--serve"]
        cwd = root

    state = _read_state()
    if state.get("port"):
        argv += ["--port", str(state["port"])]

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": cwd,
        "close_fds": True,
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW：不挂控制台、不闪黑框。
        kwargs["creationflags"] = 0x00000008 | 0x08000000
    else:
        kwargs["start_new_session"] = True

    log = open(LOG_FILE, "ab")
    try:
        kwargs["stdout"] = log
        kwargs["stderr"] = log
        subprocess.Popen(argv, **kwargs)
    finally:
        log.close()


def stop():
    """停掉后台网关。返回停完之后的 status。"""
    state = _read_state()
    pid = state.get("pid")
    if status().get("running") and isinstance(pid, int):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline:
        if not status().get("running"):
            return status()
        time.sleep(0.1)
    return status()


# ── 翻译：Anthropic 请求 -> Responses 请求 ────────────────────────────────


def _blocks(value):
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _system_text(system):
    if isinstance(system, str):
        return system.strip()
    parts = []
    for block in _blocks(system):
        if block.get("type") == "text" and str(block.get("text") or "").strip():
            parts.append(str(block.get("text")).strip())
    return "\n\n".join(parts)


def _image_part(block):
    """Anthropic 的 image 块 -> Responses 的 input_image。认不出的返回 None。"""
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") == "base64":
        media = str(source.get("media_type") or "image/png")
        data = str(source.get("data") or "")
        if not data:
            return None
        return {"type": "input_image", "image_url": "data:{};base64,{}".format(
            media, data)}
    if source.get("type") == "url":
        url = str(source.get("url") or "")
        if url:
            return {"type": "input_image", "image_url": url}
    return None


def _tool_output(block):
    """tool_result 块 -> function_call_output。

    内容可以是字符串，也可以是内容块数组；带图片时按数组回，否则拼成一段字。
    Responses 那边 output 两样都收。
    """
    call_id = str(block.get("tool_use_id") or "")
    raw = block.get("content")
    text_parts, parts = [], []
    if isinstance(raw, str):
        text = raw
        if text:
            text_parts.append(text)
            parts.append({"type": "input_text", "text": text})
    else:
        for item in _blocks(raw):
            kind = item.get("type")
            if kind == "text":
                text = str(item.get("text") or "")
                if text:
                    text_parts.append(text)
                    parts.append({"type": "input_text", "text": text})
            elif kind == "image":
                part = _image_part(item)
                if part:
                    parts.append(part)
    text = "\n".join(text_parts)
    if block.get("is_error"):
        text = "Error: " + text if text else "Error"
        parts = [{"type": "input_text", "text": text}]
    if len(parts) <= 1:
        output = text
    else:
        output = parts
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _tool_choice(choice):
    if not isinstance(choice, dict):
        return None
    kind = choice.get("type")
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool" and choice.get("name"):
        return {"type": "function", "name": str(choice["name"])}
    return None


def to_responses(body, meta, stream):
    """把一份 Messages API 请求翻成 Responses API 请求。

    丢掉 Anthropic 有、OpenAI 没有的东西（cache_control、thinking 签名、
    top_k、metadata…）；这些丢了不影响对话，硬塞过去反而会被上游 400。
    """
    items = []
    counter = [0]

    def next_id():
        counter[0] += 1
        return "call_{}".format(counter[0])

    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        for block in _blocks(message.get("content")):
            kind = block.get("type")
            if kind == "text":
                text = str(block.get("text") or "")
                if not text:
                    continue
                if role == "assistant":
                    # 文档明说 content 可以直接给字符串；用字符串比 output_text
                    # 块更稳——不是每个 OpenAI 兼容实现都认输入里的块形状。
                    items.append({"role": "assistant", "content": text})
                else:
                    items.append({"role": "user", "content": [
                        {"type": "input_text", "text": text}]})
            elif kind == "image" and role != "assistant":
                part = _image_part(block)
                if part:
                    items.append({"role": "user", "content": [part]})
            elif kind == "tool_use" and role == "assistant":
                items.append({
                    "type": "function_call",
                    "call_id": str(block.get("id") or next_id()),
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {},
                                            ensure_ascii=False),
                })
            elif kind == "tool_result":
                items.append(_tool_output(block))
            elif kind == "document":
                items.append({"role": "user", "content": [
                    {"type": "input_text", "text": "[附件已省略]"}]})
            # thinking / redacted_thinking：OpenAI 这边没有对应物，直接丢。

    if not items:
        items.append({"role": "user", "content": [
            {"type": "input_text", "text": "…"}]})

    payload = {
        "model": str(meta.get("model") or body.get("model") or ""),
        "input": items,
        "stream": bool(stream),
        # 中转不该让上游把整段对话留底。用户在自己机器上跑，隐私默认收紧。
        "store": False,
    }

    instructions = _system_text(body.get("system"))
    if instructions:
        payload["instructions"] = instructions

    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        entry = {
            "type": "function",
            "name": str(tool["name"]),
            "parameters": tool.get("input_schema") or {"type": "object"},
        }
        description = str(tool.get("description") or "")
        if description:
            entry["description"] = description
        tools.append(entry)
    if tools:
        payload["tools"] = tools
        choice = _tool_choice(body.get("tool_choice"))
        if choice is not None:
            payload["tool_choice"] = choice

    max_tokens = body.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        payload["max_output_tokens"] = max_tokens

    effort = str(meta.get("reasoning_effort") or "").strip()
    if effort:
        payload["reasoning"] = {"effort": effort}

    return payload


def estimate_tokens(body):
    """粗估输入 token。Claude Code 拿这个判断上下文余量，够用就行。

    中英混排按 3 个字符 ≈ 1 token 算（英文偏保守、中文偏宽松），每张图按
    1600 记。宁可略高：估低了会让它以为还有余量，压缩时来不及。
    """
    try:
        raw = json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = ""
    images = raw.count('"type": "image"') + raw.count('"type":"image"')
    chars = max(len(raw) - images * 200, 0)
    return max(1, int(chars / 3.0) + images * 1600)


# ── 翻译：Responses 回话 -> Anthropic 回话 ────────────────────────────────


def _parse_arguments(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _usage(payload, fallback_input=0):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    try:
        input_tokens = int(usage.get("input_tokens") or fallback_input or 0)
    except (TypeError, ValueError):
        input_tokens = fallback_input or 0
    try:
        output_tokens = int(usage.get("output_tokens") or 0)
    except (TypeError, ValueError):
        output_tokens = 0
    result = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    details = usage.get("input_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens"):
        result["cache_read_input_tokens"] = int(details["cached_tokens"])
    return result


def _stop_reason(response, has_tool):
    if has_tool:
        return "tool_use"
    details = response.get("incomplete_details") if isinstance(response, dict) else None
    if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
        return "max_tokens"
    return "end_turn"


def to_anthropic_response(reply, model, input_tokens):
    """非流式的 Responses 回话 -> Anthropic message。"""
    content, has_tool = [], False
    for item in reply.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("output_text", "text"):
                    text = str(part.get("text") or "")
                    if text:
                        content.append({"type": "text", "text": text})
                elif part.get("type") == "refusal":
                    text = str(part.get("refusal") or "")
                    if text:
                        content.append({"type": "text", "text": text})
        elif kind == "function_call":
            has_tool = True
            content.append({
                "type": "tool_use",
                "id": str(item.get("call_id") or item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "input": _parse_arguments(item.get("arguments")),
            })
    if not content:
        content.append({"type": "text", "text": ""})
    message_id = str(reply.get("id") or "")
    if not message_id.startswith("msg_"):
        message_id = "msg_" + (message_id or secrets.token_hex(12))
    usage = _usage(reply, input_tokens)
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": _stop_reason(reply, has_tool),
        "stop_sequence": None,
        "usage": {"input_tokens": usage["input_tokens"],
                  "output_tokens": usage["output_tokens"]},
    }


class StreamClosed(Exception):
    """客户端那头已经断了，别接着写。"""


class _Sse:
    """往 wfile 里按 Anthropic 的 SSE 格式写事件。"""

    def __init__(self, wfile):
        self.wfile = wfile

    def emit(self, event, data):
        chunk = "event: {}\ndata: {}\n\n".format(
            event, json.dumps(data, ensure_ascii=False))
        try:
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            raise StreamClosed()


class AnthropicStream:
    """把 Responses 的流式事件翻成 Anthropic 的 content_block 事件序列。

    上游给什么就先记什么，到 finish() 才把停因和用量补上——Anthropic 的协议
    本来也是这么分两段的（message_start 里没有 output_tokens）。
    """

    def __init__(self, sse, model, input_tokens):
        self.sse = sse
        self.model = model
        self.input_tokens = input_tokens
        self.index = -1
        self.blocks = {}          # 上游 item_id -> {"index", "open", "kind"}
        self.text_seen = {}       # item_id -> 有没有发过 delta
        self.args_seen = {}       # item_id -> 有没有发过 arguments delta
        self.tool_used = False
        self.finished = False
        self.message_id = "msg_" + secrets.token_hex(12)

    def start(self):
        self.sse.emit("message_start", {
            "type": "message_start",
            "message": {
                "id": self.message_id,
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": self.input_tokens,
                          "output_tokens": 0},
            },
        })

    # -- 块的开合 ---------------------------------------------------------

    def _open(self, key, block):
        self.index += 1
        self.blocks[key] = {"index": self.index, "open": True,
                            "kind": block.get("type")}
        self.sse.emit("content_block_start", {
            "type": "content_block_start",
            "index": self.index,
            "content_block": block,
        })
        return self.index

    def _delta(self, key, delta):
        entry = self.blocks.get(key)
        if entry is None or not entry["open"]:
            return
        self.sse.emit("content_block_delta", {
            "type": "content_block_delta",
            "index": entry["index"],
            "delta": delta,
        })

    def _close(self, key):
        entry = self.blocks.get(key)
        if entry is None or not entry["open"]:
            return
        entry["open"] = False
        self.sse.emit("content_block_stop", {
            "type": "content_block_stop",
            "index": entry["index"],
        })

    # -- 事件 -------------------------------------------------------------

    def on_event(self, event):
        kind = event.get("type")
        if kind == "response.output_item.added":
            item = event.get("item") or {}
            item_id = str(item.get("id") or item.get("call_id") or "")
            if item.get("type") == "function_call":
                self.tool_used = True
                self.args_seen[item_id] = False
                self._open(item_id, {
                    "type": "tool_use",
                    "id": str(item.get("call_id") or item_id),
                    "name": str(item.get("name") or ""),
                    "input": {},
                })
            elif item.get("type") == "message":
                # 文本块等第一个 delta 再开：空块会让 Claude Code 画一段空白。
                self.text_seen[item_id] = False
        elif kind in ("response.output_text.delta", "response.refusal.delta"):
            item_id = str(event.get("item_id") or "text")
            delta = event.get("delta")
            if kind == "response.refusal.delta":
                delta = delta or event.get("refusal") or ""
            if not delta:
                return
            if item_id not in self.blocks:
                self._open(item_id, {"type": "text", "text": ""})
            self.text_seen[item_id] = True
            self._delta(item_id, {"type": "text_delta", "text": str(delta)})
        elif kind == "response.function_call_arguments.delta":
            item_id = str(event.get("item_id") or "")
            if item_id not in self.blocks:
                return
            self.args_seen[item_id] = True
            self._delta(item_id, {"type": "input_json_delta",
                                  "partial_json": str(event.get("delta") or "")})
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            item_id = str(item.get("id") or item.get("call_id") or "")
            if item.get("type") == "function_call":
                if item_id not in self.blocks:
                    # 有些代理不发 added、直接给一整个 done：补一个整块。
                    self.tool_used = True
                    self._open(item_id, {
                        "type": "tool_use",
                        "id": str(item.get("call_id") or item_id),
                        "name": str(item.get("name") or ""),
                        "input": {},
                    })
                if not self.args_seen.get(item_id) and item.get("arguments"):
                    self._delta(item_id, {
                        "type": "input_json_delta",
                        "partial_json": str(item["arguments"]),
                    })
                self._close(item_id)
            elif item.get("type") == "message" and not self.text_seen.get(item_id):
                # 同理：没有增量、只有整段文本的回话。
                text = ""
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in (
                            "output_text", "text", "refusal"):
                        text += str(part.get("text") or part.get("refusal") or "")
                if text:
                    self._open(item_id, {"type": "text", "text": ""})
                    self._delta(item_id, {"type": "text_delta", "text": text})
                self._close(item_id)
            elif item.get("type") == "message":
                self._close(item_id)
        elif kind in ("response.completed", "response.incomplete"):
            response = event.get("response") or {}
            usage = _usage(response, self.input_tokens)
            self.finish(usage["output_tokens"],
                        _stop_reason(response, self.tool_used))
        elif kind in ("response.failed", "error"):
            response = event.get("response") or {}
            error = response.get("error") or event.get("error") or {}
            message = str(error.get("message") or "上游返回了错误但没给说明")
            raise GatewayUpstreamError(message)

    def finish(self, output_tokens=0, stop_reason=None):
        if self.finished:
            return
        self.finished = True
        for key in list(self.blocks):
            self._close(key)
        self.sse.emit("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason or (
                    "tool_use" if self.tool_used else "end_turn"),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": int(output_tokens or 0)},
        })
        self.sse.emit("message_stop", {"type": "message_stop"})

    def emit_full_as_stream(self, reply):
        """上游整段返回（没按流式给）时，补一套事件出来。"""
        message = to_anthropic_response(reply, self.model, self.input_tokens)
        for block in message["content"]:
            key = "full_{}".format(self.index + 1)
            if block["type"] == "text":
                self._open(key, {"type": "text", "text": ""})
                if block["text"]:
                    self._delta(key, {"type": "text_delta", "text": block["text"]})
            else:
                self.tool_used = True
                self._open(key, {"type": "tool_use", "id": block["id"],
                                 "name": block["name"], "input": {}})
                self._delta(key, {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(block["input"], ensure_ascii=False),
                })
            self._close(key)
        self.finish(message["usage"]["output_tokens"],
                    message["stop_reason"])


class GatewayUpstreamError(RuntimeError):
    pass


# ── HTTP 服务 ─────────────────────────────────────────────────────────────


def _upstream_error_message(raw):
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw.strip()[:500] or "上游没给说明"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if message:
                return str(message)[:1000]
        for key in ("message", "detail"):
            if data.get(key):
                return str(data[key])[:1000]
    return raw.strip()[:500] or "上游没给说明"


def _anthropic_error(status, message):
    if status == 400:
        kind = "invalid_request_error"
    elif status == 401:
        kind = "authentication_error"
    elif status == 403:
        kind = "permission_error"
    elif status == 404:
        kind = "not_found_error"
    elif status == 413:
        kind = "request_too_large"
    elif status == 429:
        kind = "rate_limit_error"
    else:
        kind = "api_error"
    return {"type": "error", "error": {"type": kind, "message": message}}


def _upstream_status(status):
    """上游状态码翻成给 Claude Code 看的：不认识的 5xx 统一当 502。"""
    if status in (400, 401, 403, 404, 413, 429):
        return status
    if status >= 500:
        return 502
    return 400


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, token):
        self.token = token
        self.last_activity = time.time()
        super().__init__(address, handler)

    def touch(self):
        self.last_activity = time.time()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # 默认会把每个请求打到 stderr（即 gateway.log）。留一行就够，别刷屏。
    def log_message(self, format, *args):
        try:
            sys.stderr.write("{} {}\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), format % args))
            sys.stderr.flush()
        except Exception:
            pass

    # -- 小工具 -----------------------------------------------------------

    @property
    def token(self):
        return getattr(self.server, "token", "")

    def _path(self):
        return urllib.parse.urlsplit(self.path).path

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._json(status, _anthropic_error(status, message))

    def _authorized(self):
        presented = self.headers.get("x-api-key") or ""
        auth = self.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            presented = presented or auth[7:].strip()
        expected = self.token or ""
        if not expected:
            return True
        if not presented:
            return False
        # 按字节比：用户手改坏成非 ASCII 时 compare_digest 的 str 版本会抛
        # TypeError。
        return hmac.compare_digest(str(presented).encode("utf-8"),
                                   expected.encode("utf-8"))

    def _body(self):
        try:
            length = int(self.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, TypeError):
            return None

    def _preset(self):
        """路径里的 agent/预设名 -> 上游配置。路由不认就 None。"""
        match = ROUTE_RE.match(self._path())
        if not match:
            return None, None
        agent_id = urllib.parse.unquote(match.group(1))
        name = urllib.parse.unquote(match.group(2))
        from claude_tool import agents
        if agent_id not in agents.ids():
            return None, None
        from claude_tool.presets import preset_path
        path = preset_path(name, agent_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None, None
        meta = data.get("claude_tool") if isinstance(data, dict) else None
        if not isinstance(meta, dict) or meta.get("protocol") != PROTOCOL:
            return None, None
        if not all(meta.get(key) for key in ("base_url", "api_key", "model")):
            return None, None
        return path, meta

    # -- 路由 -------------------------------------------------------------

    def do_GET(self):
        self.server.touch()
        path = self._path()
        if path == "/health":
            self._json(200, {"ok": True, "app": APP_MARK,
                             "pid": os.getpid()})
            return
        match = ROUTE_RE.match(path)
        if not match:
            self._error(404, "不认识的地址：{}".format(path))
            return
        if not self._authorized():
            self._error(401, "网关令牌不对")
            return
        preset, meta = self._preset()
        if preset is None:
            self._error(404, "这份预设不存在，或者不是 OpenAI 协议")
            return
        if match.group(3) == "/v1/models":
            model = str(meta.get("model"))
            self._json(200, {"data": [{"id": model, "type": "model",
                                       "display_name": model}],
                             "has_more": False})
            return
        self._error(404, "不认识的地址：{}".format(path))

    def do_POST(self):
        self.server.touch()
        path = self._path()
        match = ROUTE_RE.match(path)
        if not match:
            self._error(404, "不认识的地址：{}".format(path))
            return
        if not self._authorized():
            self._error(401, "网关令牌不对")
            return
        preset, meta = self._preset()
        if preset is None:
            self._error(404, "这份预设不存在，或者不是 OpenAI 协议")
            return
        body = self._body()
        if not isinstance(body, dict):
            self._error(400, "请求体不是一份 JSON")
            return

        route = match.group(3)
        if route == "/v1/messages/count_tokens":
            self._json(200, {"input_tokens": estimate_tokens(body)})
            return
        if route != "/v1/messages":
            self._error(404, "不认识的地址：{}".format(path))
            return
        self._messages(body, meta)

    # -- 转发 -------------------------------------------------------------

    def _upstream_url(self, meta):
        base = str(meta.get("base_url") or "").rstrip("/")
        return base if base.endswith("/responses") else base + "/responses"

    def _messages(self, body, meta):
        stream = bool(body.get("stream"))
        model = str(body.get("model") or meta.get("model") or "")
        payload = to_responses(body, meta, stream)
        input_tokens = estimate_tokens(body)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._upstream_url(meta), data=data,
            headers={
                "content-type": "application/json",
                "accept": "text/event-stream" if stream else "application/json",
                "authorization": "Bearer " + str(meta.get("api_key") or ""),
                "user-agent": "claude_tool-gateway",
            },
            method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            self._error(_upstream_status(e.code),
                        _upstream_error_message(raw))
            return
        except urllib.error.URLError as e:
            self._error(502, "连不上上游（{}）：{}".format(
                _host_of(meta.get("base_url")), getattr(e, "reason", e)))
            return
        except (OSError, ValueError) as e:
            self._error(502, "请求上游时出错：{}".format(e))
            return

        with response:
            content_type = (response.headers.get("content-type") or "").lower()
            # 有些代理转发 SSE 时不带 content-type。请求了流式、又不是明确的
            # JSON，就按 SSE 读——读不出来那头会自己发 error 事件。
            if stream and "application/json" not in content_type:
                self._stream(response, model, input_tokens)
                return
            raw = response.read()
        try:
            reply = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, TypeError):
            self._error(502, "上游回的话读不出来")
            return
        if not isinstance(reply, dict) or reply.get("error"):
            self._error(502, _upstream_error_message(raw.decode(
                "utf-8", "replace")))
            return
        if stream:
            # 上游没按流式回：自己补一套 Anthropic 的事件。
            self._start_sse()
            sse = _Sse(self.wfile)
            try:
                translator = AnthropicStream(sse, model, input_tokens)
                translator.start()
                translator.emit_full_as_stream(reply)
            except StreamClosed:
                pass
            return
        self._json(200, to_anthropic_response(reply, model, input_tokens))

    def _start_sse(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True

    def _stream(self, response, model, input_tokens):
        self._start_sse()
        sse = _Sse(self.wfile)
        translator = AnthropicStream(sse, model, input_tokens)
        try:
            translator.start()
            for event in _iter_sse(response):
                translator.on_event(event)
                if translator.finished:
                    break
            if not translator.finished:
                translator.finish(0, None)
        except StreamClosed:
            return
        except GatewayUpstreamError as e:
            try:
                sse.emit("error", _anthropic_error(502, str(e)))
            except StreamClosed:
                pass
        except (OSError, ValueError) as e:
            try:
                sse.emit("error", _anthropic_error(
                    502, "读上游回话时断了：{}".format(e)))
            except StreamClosed:
                pass


def _iter_sse(response):
    """逐事件读上游 SSE。data 行可能多行，拼起来再解。"""
    data_lines = []
    for raw in response:
        try:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
        except Exception:
            continue
        if not line:
            if data_lines:
                chunk = "\n".join(data_lines)
                data_lines = []
                if chunk.strip() == "[DONE]":
                    return
                try:
                    event = json.loads(chunk)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        # event: 行和注释行（:）不看：数据里自带 type。
    if data_lines:
        chunk = "\n".join(data_lines)
        if chunk.strip() != "[DONE]":
            try:
                event = json.loads(chunk)
            except ValueError:
                return
            if isinstance(event, dict):
                yield event


def _host_of(base_url_value):
    text = str(base_url_value or "")
    text = text.split("//")[-1]
    return text.split("/")[0] or "上游"


# ── 前台入口 ──────────────────────────────────────────────────────────────


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def serve(port=None, idle=IDLE_SECONDS):
    """前台跑网关，返回进程退出码。被 ensure() 甩到后台的就是这条路。"""
    state = _read_state()
    token = state.get("token") or secrets.token_hex(16)
    candidates = []
    for value in (port, state.get("port")):
        if value and int(value) not in candidates:
            candidates.append(int(value))
    candidates.append(0)

    server = None
    for index, candidate in enumerate(candidates):
        try:
            server = _Server((HOST, candidate), _Handler, token)
            break
        except OSError:
            # 那个端口上已经有一个健康的本家（多半是并发 ensure 抢着起的），
            # 让它继续服务，这个进程直接退，别去抢同一个 state 文件。
            if candidate and _healthy(candidate):
                return 0
            if index == len(candidates) - 1:
                raise
    if server is None:
        raise GatewayError("没有可用的本地端口")

    actual = server.server_address[1]
    _write_state(actual, token)
    sys.stderr.write("gateway listening on {}\n".format(base_url(actual)))
    sys.stderr.flush()

    if idle:
        _start_idle_watchdog(server, idle)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _start_idle_watchdog(server, idle):
    def watch():
        while True:
            time.sleep(60)
            if time.time() - server.last_activity > idle:
                server.shutdown()
                return

    threading.Thread(target=watch, daemon=True).start()
