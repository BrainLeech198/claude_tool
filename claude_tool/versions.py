"""本机 claude 要不要更新，以及启动器自己要不要更新。

**claude 那份**两个源各答一半：

- **npm registry** 上是 claude 真正的最新版（一个 JSON，取 version 就完了）。
- **官网的 releases.js** 里记着"这一版启动器配着测过的 claude"，那是打包时读
  打包机上的 `claude --version` 写进去的（见 build/update_releases.py）。

两个都拿不到就什么都不说——离线、被墙、官网还没更新，都不该让用户看见一个报错。
所以这里的函数一律返回 None 而不是抛异常，调用方拿到 None 就闭嘴。

**只告诉，不替用户升级。** 升级动作照旧由用户自己去官方那套装法做（顶栏那个
入口就是官网说明页），这里不跑 npm，也不动 claude 的任何文件。

**启动器自己那份**只有一个源，就是同一份 releases.js：里头 0 号记录就是最新那
一版，`version` 是启动器版本号，底下几张平台卡写着这一版的产物叫什么、多大。
比大小用的还是本模块这套 parse / 补零。下载和打开不在这儿——见 selfupdate.py，
这个模块只管"要不要"，一个字节都不落盘。

这个模块不碰 tkinter，也不在 import 的时候发请求——`__main__.py` 那条 hook 路径
import 到它也不该有任何副作用。
"""
import collections
import json
import re
import urllib.request

NPM_LATEST = "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"
SITE = "https://brainleech198.github.io/claude_tool/"
SITE_RELEASES = SITE + "releases.js"
TIMEOUT = 6

# releases.js 里那几行赋值，跟 build/update_releases.py、docs/index.html 认的是
# 同一个形状。MARKER 那个也管着写回：update_releases.py 就换等号后面那个数组。
MARKER = "window.RELEASES = "
DOWNLOAD_BASE_MARKER = "window.DOWNLOAD_BASE = "
MIRROR_BASE_MARKER = "window.MIRROR_BASE = "
# releases.js 里每条记录的平台格子，跟 update_releases.py 的 PLATFORM_KEYS 对齐
PLATFORM_KEYS = ("windows", "linux", "macos")

# 查启动器自己有没有新版的结果：网上最新是几版、本机落不落后，外加那条记录的
# 原件（底下几张平台卡）和两个下载基地址。界面只读，不改。
LauncherRelease = collections.namedtuple(
    "LauncherRelease", "latest stale entry base mirror")


def parse(text):
    """从 "claude 2.1.278" / "2.1.278" / "2.1.278 (Claude Code)" 里揪出版本号。

    返回 (2, 1, 278)；一个数字都没有就返回 None。只认第一段连续的数字点号，
    后面的尾巴（`(Claude Code)` 之类）一概不看——比大小用不着它，启动器自己的
    "0.2.0" 走同一条路。
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


def newer(latest, local):
    """latest 比 local 新吗。两串都是 "x.y.z" 那种文本，解不出来的一律不算新
    ——宁可不提示，也不要拿一个没读到的东西去比出一个"你落后了"。"""
    latest_parts, local_parts = parse(latest), parse(local)
    if latest_parts is None or local_parts is None:
        return False
    return _padded(latest_parts) > _padded(local_parts)


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "claude_tool"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", "replace")


def _literal(text, marker):
    """releases.js 里 `window.X = "...";` 那个引号里的东西，没有就 None。

    只认单独一行的、引号里没有别的花样的那种写法——这文件的格式是我们自己定的
    （见 docs/releases.js 的开头），不按 JS 解析。
    """
    found = re.search(re.escape(marker) + r'"([^"\n]*)"', text)
    return found.group(1).strip() if found else None


def releases(text):
    """releases.js 那串文本里的记录列表，0 号是最新那版。解不出来给空表。

    不解析日期、也不比版本号——update_releases.py 往 0 号位插，页面那三张卡拿的
    也是 0 号，顺序就是"新在前"这一个约定。
    """
    try:
        start = text.index(MARKER) + len(MARKER)
        end = text.index("];", start) + 1
        found = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
    return found if isinstance(found, list) else []


def newest_claude(text):
    """releases.js 里最新一条记录的 claude 字段，没有就 None。"""
    for entry in releases(text):
        if isinstance(entry, dict):
            value = entry.get("claude")
            return value if isinstance(value, str) and value.strip() else None
    return None


def npm_version():
    """npm 上 claude 的最新版号，拿不到返回 None。"""
    try:
        return parse(json.loads(_get(NPM_LATEST)).get("version"))
    except Exception:
        return None


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


def launcher(local):
    """启动器自己网上有没有新版。拿不到那份清单就返回 None。

    local 是本机这版的文本（claude_tool.__version__）。返回 LauncherRelease；
    清单里没有启动器版本号、或者本机这个号解不出来，也当查不到——比不出新旧。

    **一次请求把两个基地址一起带回来**（Gitee 主源 + GitHub 镜像），省得下载那
    一步再跑一趟官网；`url` 那个可选字段也跟着记录一起给出去，selfupdate 认它
    优先。
    """
    if parse(local) is None:
        return None
    try:
        text = _get(SITE_RELEASES)
    except Exception:
        return None
    entries = releases(text)
    entry = entries[0] if entries else None
    if not isinstance(entry, dict):
        return None
    latest = entry.get("version")
    if parse(latest) is None:
        return None
    latest_parts = parse(latest)
    return LauncherRelease(
        latest=number(latest_parts),
        stale=_padded(latest_parts) > _padded(parse(local)),
        entry=entry,
        base=_literal(text, DOWNLOAD_BASE_MARKER),
        mirror=_literal(text, MIRROR_BASE_MARKER),
    )
