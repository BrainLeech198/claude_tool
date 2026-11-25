"""模型预设：一个模型一个 json。

预设文件放 ~/.claude_tool/<agent>_settings/ 下面，**按 agent 分开**：claude 的
在 claude_settings/、codebuddy 的在 codebuddy_settings/（见 agents.py 的
preset_dir）。换模型就是把预设覆盖到**那个 agent 自己那份** settings.json 上
（claude 是 ~/.claude/settings.json，codebuddy 是 ~/.codebuddy/settings.json）。

带 agent_id 的这几个函数不传就是 claude——单档时的行为跟以前一字不差。

那张"常用供应商"表搬去 providers.py 了（它现在能配置、能被刷新），这里只留下
按它来猜预设名的那一处。
"""
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request

from claude_tool import agents, gateway
from claude_tool.paths import (
    CLAUDE_DIR,
    LEGACY_PREFIX,
    LEGACY_PRESET_DIR,
    PRESET_DIR,
    PRESET_SUFFIX,
    TOOL_DIR,
)
from claude_tool.providers import load_providers


# ── 常用供应商 ────────────────────────────────────────────────────────────
# "添加模型"表单里那个下拉栏用的：选一个就把地址和模型名填好，用户只剩贴 key。
# 那张表本身在 providers.py——内置一份打底，可以被 ~/.claude_tool/providers.json
# 覆盖，也可以被 AI 查一遍刷新。**每次现读**，别在这儿存成模块级常量：存下来就
# 意味着用户刷新完得重启才看得见。


def host_of(base):
    """从 Base URL 里抠出域名，给提示语用。"""
    return base.split("//")[-1].split("/")[0]


# ── 模型预设 ──────────────────────────────────────────────────────────────


def preset_path(name, agent_id=None):
    """预设名 -> 文件路径。名称里的非法字符在表单那关已经挡掉了。

    往哪个 agent 的预设目录里落由 agent_id 定；不传就是 claude。
    """
    return os.path.join(agents.get(agent_id).preset_dir, name + PRESET_SUFFIX)


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


def discover_presets(agent_id=None):
    """扫描这个 agent 的预设目录下所有 *.json，返回 {名称: 完整路径}。"""
    preset_dir = agents.get(agent_id).preset_dir
    presets = {}
    if not os.path.isdir(preset_dir):
        return presets
    for filename in os.listdir(preset_dir):
        if not filename.endswith(PRESET_SUFFIX):
            continue
        name = filename[:-len(PRESET_SUFFIX)]
        if name:
            presets[name] = os.path.join(preset_dir, filename)
    return presets


def read_env(path):
    """读取预设文件里的 env 段，读不到就返回空字典。

    拿到的是**文件里原样那份**：OpenAI 协议的预设这儿存的还是上游地址和上游
    key。开会话、测试要用的那份走 session_env()——那个会把地址换成本地网关。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("env", {})
    except Exception:
        return {}


def read_meta(path):
    """预设里启动器自己的那段（协议、上游地址…）。没有就返回空字典。

    放在 env 之外是有意的：env 是给 Claude Code 看的，这段只有启动器和网关认。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    meta = data.get("claude_tool")
    return meta if isinstance(meta, dict) else {}


# 只给 Responses 协议的官方端点。1.5.0 之前的启动器存不了协议字段，用户把
# OpenAI 官方地址填进来时留下的就是这种"看着像 Anthropic、其实是 Responses"的
# 老预设；认出来之后自动改走网关，不然 Claude Code 会去拼 /v1/v1/messages。
OPENAI_HOSTS = ("api.openai.com",)


def infer_protocol(env, meta):
    """判断一份预设该走哪种协议。显式写了的听文件的，没写的从地址猜。"""
    protocol = str((meta or {}).get("protocol") or "")
    if protocol in ("anthropic", "openai-responses"):
        return protocol
    base = str((env or {}).get("ANTHROPIC_BASE_URL") or "")
    if host_of(base).casefold() in OPENAI_HOSTS:
        return "openai-responses"
    return "anthropic"


def effective_upstream(env, meta):
    """网关要的上游配置（地址 / key / 模型名）。

    显式的 claude_tool 段优先；没有就用 env 里那份（1.5.0 之前存的老预设）。
    不是 Responses 协议、或者三样缺一样，返回 None。
    """
    if infer_protocol(env, meta) != "openai-responses":
        return None
    base_url = str((meta or {}).get("base_url")
                   or (env or {}).get("ANTHROPIC_BASE_URL") or "").strip()
    api_key = str((meta or {}).get("api_key")
                  or (env or {}).get("ANTHROPIC_AUTH_TOKEN")
                  or (env or {}).get("ANTHROPIC_API_KEY") or "").strip()
    model = str((meta or {}).get("model")
                or (env or {}).get("ANTHROPIC_MODEL") or "").strip()
    if not (base_url and api_key and model):
        return None
    return {"protocol": "openai-responses", "base_url": base_url,
            "api_key": api_key, "model": model}


def protocol_of(path):
    """这份预设走哪种协议。没写协议的老预设按地址认，认不出才是 Anthropic。"""
    return infer_protocol(read_env(path), read_meta(path))


def agent_of(path):
    """按预设文件所在的目录认它属于哪个 agent；认不出回落到 claude。"""
    folder = os.path.normcase(os.path.dirname(os.path.normpath(path)))
    for agent_id in agents.ids():
        if os.path.normcase(agents.get(agent_id).preset_dir) == folder:
            return agent_id
    return agents.DEFAULT_AGENT


def session_env(path, agent_id=None):
    """开会话/测试那一刻真正要用的 env。

    Anthropic 协议的预设原样返回。OpenAI 协议的预设把 ANTHROPIC_BASE_URL 换成
    本地网关那条路由、令牌换成网关令牌——上游 key 只留在网关进程里。模型别名
    （haiku/sonnet/opus、small-fast）也一起钉成同一个上游模型：Claude Code 做
    后台小任务时会用别名发请求，不钉的话网关会收到一个上游不认识的模型名。

    网关起不来抛 gateway.GatewayError，消息直接能摆给用户看。
    """
    env = dict(read_env(path))
    meta = read_meta(path)
    if infer_protocol(env, meta) != "openai-responses":
        return env

    upstream = effective_upstream(env, meta)
    if upstream is None:
        raise gateway.GatewayError(
            "「{}」这份预设的 OpenAI 配置不全（地址 / key / 模型名缺一样），"
            "到「添加模型」里补上再试。".format(os.path.basename(path)))
    model = upstream["model"]

    resolved_agent = agent_id or agent_of(path)
    name = os.path.basename(path)
    if name.endswith(PRESET_SUFFIX):
        name = name[:-len(PRESET_SUFFIX)]
    state = gateway.ensure()

    env["ANTHROPIC_BASE_URL"] = gateway.route_base(
        state["port"], resolved_agent, name)
    env["ANTHROPIC_AUTH_TOKEN"] = state["token"]
    # Claude Code 如果同时看到 ANTHROPIC_API_KEY，可能优先拿它发 x-api-key；
    # 上游 key 不能跟着请求进网关，统一收成网关令牌。
    if env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = state["token"]
    env["ANTHROPIC_MODEL"] = model
    for key in ("ANTHROPIC_SMALL_FAST_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        env[key] = model
    return env


def read_model(path):
    """从预设文件读取 ANTHROPIC_MODEL，读不到则返回占位。"""
    env = read_env(path)
    return env.get("ANTHROPIC_MODEL") or read_meta(path).get("model") or "未知模型"


def describe_preset(path):
    """卡片副标题：模型名 · 供应商域名，域名读不到就只显示模型名。"""
    env = read_env(path)
    meta = read_meta(path)
    if infer_protocol(env, meta) == "openai-responses":
        host = str(meta.get("base_url")
                   or env.get("ANTHROPIC_BASE_URL") or "").split("//")[-1].split("/")[0]
        model = str(meta.get("model") or read_model(path))
        return "{}  ·  {} · 经本地网关".format(model, host) if host else model
    host = env.get("ANTHROPIC_BASE_URL", "").split("//")[-1].split("/")[0]
    model = read_model(path)
    return "{}  ·  {}".format(model, host) if host else model


def active_preset(presets, agent_id=None):
    """返回当前 settings.json 内容所对应的预设名称；若为自定义配置则返回 None。

    认的是**这个 agent 自己那份** settings.json。
    """
    try:
        with open(agents.get(agent_id).settings_file, "r", encoding="utf-8") as f:
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


def apply_preset(name, agent_id=None):
    """把这份预设覆盖写到这个 agent 自己的 settings.json 上，返回写好的路径。

    这就是"换模型"。整份文件照抄、不只是 env：预设里可能还有 language 之类的
    非 env 设置，整份抄才跟界面上看到的那份逐字一样。

    界面（ui/models.py 的 switch_model）和命令行（cli 的 use）都走这儿——一条
    写法，别让两条路哪天写出不一样的东西来。目标目录不存在就建（全新安装时
    ~/.claude 可能还没有）。

    调用方负责提示"重启后生效"：那句话只有它知道用户在跟什么打交道。
    """
    profile = agents.get(agent_id)
    source = preset_path(name, agent_id)
    if not os.path.isfile(source):
        raise FileNotFoundError("预设「{}」不在盘上：{}".format(name, source))
    os.makedirs(os.path.dirname(profile.settings_file), exist_ok=True)

    if protocol_of(source) == "openai-responses":
        # OpenAI 协议这份的 env 得先换成网关那条路由再写下去，否则 Claude Code
        # 会拿着上游地址去拼 /v1/messages。网关起不来就原样抛出去，调用方会提示。
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("预设「{}」读出来不是一份配置".format(name))
        data["env"] = session_env(source, agent_id)
        with open(profile.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return profile.settings_file

    shutil.copyfile(source, profile.settings_file)
    return profile.settings_file


def test_preset(path, timeout=15):
    """照着预设打一次最小请求，看这个 endpoint 通不通。

    返回 (是否可用, 说明)。说明要么是耗时，要么是状态码/错误名，直接摆卡片上。

    OpenAI 协议的预设走 session_env：这一测走的是网关整条链路（翻译 + 上游），
    比只打上游更能说明"这个模型能不能在 Claude Code 里用"。
    """
    try:
        env = session_env(path)
    except gateway.GatewayError as e:
        return False, str(e)
    except Exception:
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


# ── 测试结果留痕 ──────────────────────────────────────────────────────────
# 每次测完把结果和时刻记下来，重开设置窗时卡片上直接标"上次测通：3 分钟前"，
# 不用每次重测；失败的会一直标着，直到下一次测通为止。
#
# 存的是**内容指纹**不是文件名：预设被改过（换了 key、改了模型名），旧结果就自动
# 作废——不然改完还标着上次那个"失败 401"，会让人以为改动没生效。

TEST_LOG_FILE = os.path.join(TOOL_DIR, "model_tests.json")


def preset_test_identity(path):
    """标识接受测试的那份文件，区分原地编辑和同路径替换。"""
    try:
        with open(path, "rb") as f:
            stamp = os.fstat(f.fileno())
            return (stamp.st_dev, stamp.st_ino, stamp.st_ctime_ns, stamp.st_mtime_ns,
                    hashlib.sha1(f.read()).hexdigest())
    except OSError:
        return None


def load_test_results():
    """读出留痕表 {规范化路径: {ok, note, when, fp}}；读不动给空表。"""
    try:
        with open(TEST_LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_test_results(results):
    os.makedirs(TOOL_DIR, exist_ok=True)
    with open(TEST_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def record_test_result(path, ok, note, identity=None):
    """在主线程记下结果；带请求身份时，不给已更换的文件记旧结果。"""
    current = preset_test_identity(path)
    if current is None or (identity is not None and current != identity):
        return False
    results = load_test_results()
    key = os.path.normcase(os.path.normpath(path))
    results[key] = {"ok": bool(ok), "note": note, "when": time.time(), "fp": current[-1]}
    save_test_results(results)
    return True


def cached_test_result(path):
    """这个预设上次的测试结果，返回 (ok, note, when)；没测过、或者预设改过了给 None。

    路径对不上、指纹对不上、时间戳读不出来，都当"没测过"——宁可多测一次，也不
    拿着一条对不上号的旧结论误导人。
    """
    entry = load_test_results().get(os.path.normcase(os.path.normpath(path)))
    if not isinstance(entry, dict):
        return None
    when = entry.get("when")
    if not isinstance(when, (int, float)):
        return None
    identity = preset_test_identity(path)
    if identity is None or entry.get("fp") != identity[-1]:
        return None
    return bool(entry.get("ok")), entry.get("note") or "", when


# ── 导入当前配置 ──────────────────────────────────────────────────────────
# 新手最常见的开局不是"从零填表单"，是"我已经在别的窗口里用着 claude 了，想让
# 启动器认出来"。可 Base URL 和 API Token 本来就躺在那份 settings.json 里，
# 让人从别处抄一遍再粘进表单，纯属折腾。所以给一条一键路：读出来，存成预设。
#
# 读的是**那个 agent 自己那份** settings.json（claude 或 codebuddy），全程只读
# ——全项目会写它的地方只有 switch_model() 一处，而且是有意的覆盖。

IMPORT_OK = "ok"            # 有第三方服务商配置，能导
IMPORT_NO_FILE = "no-file"  # 机器上还没这份配置 = 还没用过 Claude Code
IMPORT_NO_ENV = "no-env"    # 有配置但没那三样 = 走的是官方账号登录
IMPORT_ALREADY = "already"  # 已经有内容一样的预设了


def read_current_settings(agent_id=None):
    """这个 agent 现在那份 settings.json 的内容，读不到给空字典。"""
    try:
        with open(agents.get(agent_id).settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def probe_current_settings(agent_id=None):
    """当前配置能不能导成预设，返回 (状态, 附加数据)。

    already 的附加数据是那个已有预设的名字，好让提示语能直说"就是「X」"。
    ok 的附加数据是整份 settings 内容——存的时候照抄整份而不是只抄 env，
    这样它跟当前配置逐字一样，列表里立刻认得出是"当前"。
    """
    data = read_current_settings(agent_id)
    if not data:
        return IMPORT_NO_FILE, None
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    if not (env.get("ANTHROPIC_BASE_URL") and env.get("ANTHROPIC_AUTH_TOKEN")
            and env.get("ANTHROPIC_MODEL")):
        return IMPORT_NO_ENV, None
    name = active_preset(discover_presets(agent_id), agent_id)
    if name:
        return IMPORT_ALREADY, name
    return IMPORT_OK, data


def suggest_preset_name(env):
    """给导入的预设起个名字。

    先拿域名去常用供应商表里对——用户认的是"DeepSeek"这种名字，不是
    "api.deepseek.com"。对不上就用模型名，再不行用域名。都没有返回 None。
    """
    base = str(env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    model = str(env.get("ANTHROPIC_MODEL") or "").strip()
    host = host_of(base) if base else ""
    if host:
        for row in load_providers():
            if host_of(row.base_url) == host:
                return row.preset
    return model or host or None


# ── 导出 / 导入（换机器、备份用）────────────────────────────────────────────
# 预设就一个个 json 躺在 preset_dir 里，导出就是把它们收成**一份**文件，导入再摊回去。
# 不收成 zip：内容全是 json，单文件更好传、也更方便肉眼看一眼里面是什么（别带 key
# 外传那条不是这儿能拦的，写入时提醒一句就够）。

BUNDLE_KIND = "claude-tool.model-presets"
BUNDLE_VERSION = 1


def export_payload(agent_id=None):
    """把这一档现有的预设打成一个可存盘的 dict。

    逐份照读，读不动的跳过——单份坏了不该让整次备份失败。
    """
    bundle = {}
    for name, path in discover_presets(agent_id).items():
        try:
            with open(path, "r", encoding="utf-8") as f:
                bundle[name] = json.load(f)
        except Exception:
            continue
    return {
        "kind": BUNDLE_KIND,
        "version": BUNDLE_VERSION,
        "agent": agents.get(agent_id).id,
        "presets": bundle,
    }


def bundle_names(path):
    """一眼看出一份导出文件里有哪些预设，给"导入前先确认"用。读不动抛异常。"""
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or payload.get("kind") != BUNDLE_KIND:
        raise ValueError("这不像一份模型预设的导出文件")
    presets = payload.get("presets")
    if not isinstance(presets, dict) or not presets:
        raise ValueError("这份文件里一个预设都没有")
    return [name for name, data in presets.items() if isinstance(data, dict)]


def import_bundle(path, agent_id=None, overwrite=False):
    """把一份导出文件里的预设写进这一档的目录，返回 (写入, 跳过) 两个名字列表。

    默认不覆盖同名——备份还原时更常见的是"这台机器上自己那份别丢"，覆盖是破坏性的。
    真要覆盖（比如清过一遍再还原），传 overwrite=True。
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or payload.get("kind") != BUNDLE_KIND:
        raise ValueError("这不像一份模型预设的导出文件")
    bundle = payload.get("presets")
    if not isinstance(bundle, dict) or not bundle:
        raise ValueError("这份文件里一个预设都没有")

    preset_dir = agents.get(agent_id).preset_dir
    os.makedirs(preset_dir, exist_ok=True)
    written, skipped = [], []
    for name, data in bundle.items():
        if not isinstance(data, dict):
            skipped.append(name)
            continue
        target = os.path.join(preset_dir, name + PRESET_SUFFIX)
        if os.path.exists(target) and not overwrite:
            skipped.append(name)
            continue
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        written.append(name)
    return written, skipped
