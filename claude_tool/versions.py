"""本机 claude 要不要更新。

两个源各答一半：

- **npm registry** 上是 claude 真正的最新版（一个 JSON，取 version 就完了）。
- **官网的 releases.js** 里记着"这一版启动器配着测过的 claude"，那是打包时读
  打包机上的 `claude --version` 写进去的（见 build/update_releases.py）。

两个都拿不到就什么都不说——离线、被墙、官网还没更新，都不该让用户看见一个报错。
所以这里的函数一律返回 None 而不是抛异常，调用方拿到 None 就闭嘴。

**只告诉，不替用户升级。** 升级动作照旧由用户自己去官方那套装法做（顶栏那个
入口就是官网说明页），这里不跑 npm，也不动 claude 的任何文件。

这个模块不碰 tkinter，也不在 import 的时候发请求——`__main__.py` 那条 hook 路径
import 到它也不该有任何副作用。
"""
import json
import re
import urllib.request

NPM_LATEST = "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"
SITE_RELEASES = "https://brainleech198.github.io/claude_tool/releases.js"
TIMEOUT = 6

# releases.js 里那行赋值，跟 build/update_releases.py 认的是同一个形状
MARKER = "window.RELEASES = "


def parse(text):
    """从 "claude 2.1.278" / "2.1.278" / "2.1.278 (Claude Code)" 里揪出版本号。

    返回 (2, 1, 278)；一个数字都没有就返回 None。只认第一段连续的数字点号，
    后面的尾巴（`(Claude Code)` 之类）一概不看——比大小用不着它。
    """
    found = re.search(r"\d+(?:\.\d+)*", str(text or ""))
    if not found:
        return None
    return tuple(int(part) for part in found.group(0).split("."))


def number(parts):
    """(2, 1, 278) -> "2.1.278"，写进配置、提示里给人看的那一份。"""
    return ".".join(str(part) for part in parts)


def _padded(parts):
    """2.1 和 2.1.0 要能比大小，短的补零再比。"""
    return parts + (0,) * max(0, 3 - len(parts))


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "claude_tool"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", "replace")


def npm_version():
    """npm 上 claude 的最新版号，拿不到返回 None。"""
    try:
        return parse(json.loads(_get(NPM_LATEST)).get("version"))
    except Exception:
        return None


def newest_claude(text):
    """releases.js 那串文本里，最新一条记录的 claude 字段。

    数组第一条就是最新那版（update_releases.py 往 0 号位插），所以不解析日期、
    也不比版本号，直接取 0 号。
    """
    start = text.index(MARKER) + len(MARKER)
    end = text.index("];", start) + 1
    releases = json.loads(text[start:end])
    if not isinstance(releases, list) or not releases:
        return None
    value = releases[0].get("claude")
    return value if isinstance(value, str) and value.strip() else None


def site_version():
    """官网上那一版启动器配着测过的 claude 版号，拿不到返回 None。"""
    try:
        return parse(newest_claude(_get(SITE_RELEASES)))
    except Exception:
        return None


def check(local):
    """本机这版 claude 落后了吗。

    local 是启动器已经拿到的那串（"claude 2.1.150"，就是顶栏显示的那个）。
    npm 优先，它答不上来（没网、被墙）才回头问官网——官网那份是"打包时测过的
    版本"，比 npm 的实时值旧，只能当兜底。

    返回 (最新版号文本, 拿的是哪个源, 本机是否落后)，查不到返回 None。
    本机版本读不出来时 stale 给 False：宁可不提示，也不要拿一个没读到的东西
    去比出一个"你落后了"。
    """
    local_parts = parse(local)
    for source, getter in (("npm", npm_version), ("官网", site_version)):
        latest = getter()
        if latest is None:
            continue
        stale = (local_parts is not None
                 and _padded(latest) > _padded(local_parts))
        return number(latest), source, stale
    return None
