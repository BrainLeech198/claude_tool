"""输入 key 之后，问服务商「你这儿在卖哪些模型」。

只是便利，不是必经之路：拉不到时用户始终可以手填模型名，所以这里一律返回
(模型名列表, 一句话说明)，不抛异常。说明直接摆到「添加模型」那扇窗里。

各家的列表接口并不都在同一个位置：官方 Anthropic 挂 /v1/models，OpenAI 兼容的
挂 /v1/models 或 /models，而本表里那些 Anthropic 兼容入口（地址常带 /anthropic）
底下未必有模型列表——得退回到域名根上再试。所以按候选地址逐个问，问到能解出
模型名为止。返回的形状也不统一（data[].id、models[].name、还有套娃数组的），
解析照 starburst_recorder 那套松着来：只在数组项里认 id / model / name。
"""
import json
import urllib.error
import urllib.request

TIMEOUT = 8

# 一次最多收这么多。聚合平台动辄上千条，下拉摆不下也没人翻，取前 500 个就够。
MAX_MODELS = 500

# 推断不出来的那几家：列表接口不在 base 或域名根下面，单独记一笔。
KNOWN_MODEL_PATHS = {
    "open.bigmodel.cn": "/api/paas/v4/models",
}


def candidate_urls(base_url):
    """按可能性排出的模型列表地址，逐个试。

    base 结尾带 /anthropic 的，先试它自己，再退回到域名根；带 /v1 的不要
    再拼一层 /v1，免得出现 /v1/v1/models。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return []

    candidates = []
    scheme, _, rest = base.partition("://")
    host = rest.split("/")[0].lower()
    known = KNOWN_MODEL_PATHS.get(host)
    if known:
        candidates.append(scheme + "://" + host + known)

    roots = [base]
    if base.endswith("/anthropic"):
        roots.append(base[:-len("/anthropic")])
    for root in roots:
        paths = ("/models",) if root.endswith("/v1") else ("/v1/models", "/models")
        for path in paths:
            url = root + path
            if url not in candidates:
                candidates.append(url)
    return candidates


def parse_models(body):
    """从列表接口的返回里抠出模型名。

    不假设固定形状：数组项里有 id 用 id（OpenAI / Anthropic / OpenRouter），
    没有就退到 model（Ollama），再没有才用 name（有些代理只给显示名）。
    对象和嵌套数组继续往下找，最多五层。
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []

    names = []
    seen = set()

    def add(value):
        name = value.strip() if isinstance(value, str) else ""
        if name and len(name) <= 200 and name not in seen:
            seen.add(name)
            names.append(name)

    def visit(node, depth):
        if depth > 5 or node is None:
            return
        if isinstance(node, list):
            for item in node:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict):
                    for key in ("id", "model", "name"):
                        if isinstance(item.get(key), str):
                            add(item[key])
                            break
            return
        if isinstance(node, dict):
            for value in node.values():
                visit(value, depth + 1)

    visit(data, 0)
    return sorted(names, key=str.lower)


def _failure(status, reason=""):
    """状态码/网络原因翻成人话；只挑一句，摆界面用。"""
    if status == 0:
        return "连不上 · " + reason if reason else "连不上"
    if status in (401, 403):
        return "这家不认这把 key（{}），模型名照样能手填。".format(status)
    if status == 404:
        return "这家没开放模型列表（404），模型名从控制台复制过来手填。"
    if status == 429:
        return "被限流了（429），等一会儿点「刷新列表」。"
    if status >= 500:
        return "服务商自己出错了（{}），等会儿再刷新。".format(status)
    return "没拉到模型列表（{}），模型名照样能手填。".format(status)


def fetch_models(base_url, token, timeout=TIMEOUT):
    """问一圈，返回 (模型名列表, 一句话说明)。

    401/403、429、5xx、连不上这几种换个地址也没用，碰到就停；只有 404/400
    这种「这个路径不对」才继续试下一个候选地址。
    """
    token = (token or "").strip()
    urls = candidate_urls(base_url)
    if not urls:
        return [], "服务地址要能拼出模型列表地址，先填一个 http(s) 的地址。"
    if not token:
        return [], "填上 key 才能问这家有哪些模型。"

    # 两套密钥头都带上：Anthropic 认 x-api-key，OpenAI 兼容的认 Bearer。
    headers = {
        "accept": "application/json",
        "authorization": "Bearer " + token,
        "x-api-key": token,
        "anthropic-version": "2023-06-01",
        "user-agent": "claude_tool",
    }

    last = "没拉到模型列表，模型名照样能手填。"
    for url in urls:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = _failure(e.code)
            if e.code in (404, 400):
                continue
            return [], last
        except urllib.error.URLError as e:
            reason = str(getattr(e, "reason", "") or "").strip()
            return [], _failure(0, reason)
        except (OSError, ValueError) as e:
            return [], "回话读不出来（{}）".format(type(e).__name__)

        models = parse_models(body)[:MAX_MODELS]
        if models:
            return models, "拉到 {} 个模型，点下拉挑一个。".format(len(models))
        last = "返回里没认出模型名，模型名照样能手填。"

    return [], last
