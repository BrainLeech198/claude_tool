"""插件清单 —— `plugin.json` 的读取与校验。

## 为什么清单必须是一个独立文件

信任确认要在**加载插件代码之前**，把「名称 / 作者 / 版本 / 路径」摆给用户看。
清单要是写在 Python 模块里，就得先 import 才能读到它——那会儿别人的代码已经跑过
了，"确认"只剩一句空话。所以 `plugin.json` 是硬要求：它只写**是什么**，不写
**怎么算**（那是代码的事）。

## 校验分两档，别混

- **坏插件** —— 读不到、不是 JSON、缺必填字段、`id` 跟目录名对不上。这类**不算
  崩**：用户手写清单少个逗号是常事，不该把整个插件页干掉。记一条错误、照实说。
- **门禁不过** —— 清单本身是好的，只是这台宿主编不动它（`api_version` 不符、
  `min_host` 比宿主新）。灰着、写明原因，**不加载**。

两档分开是因为**对用户的下一步不同**：前者要他去改文件，后者要他去升宿主或者
换一个插件版本。

    python -c "from claude_tool.plugins import manifest; print(manifest.read(r'路径'))"
"""
import json
import os
import re

# 清单文件名。**不许改名**，也不许换成 .py/.toml——上面那段说的就是这个。
FILE_NAME = "plugin.json"

# id 要拿来当模块名和状态文件的键，收窄到"字母数字下划线"。
ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
# 入口写法：`<相对路径>.py:<函数名>`。只收 .py——别的一律当"我不知道怎么加载"。
ENTRY_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_./-]*\.py):([A-Za-z_]\w*)$")

# 必填 / 可选。可选字段缺了填空串，插件作者的清单能短一点。
REQUIRED = ("id", "name", "version", "api_version", "min_host", "entry")
OPTIONAL = ("author", "description", "homepage")

# 宿主**当前**提供的接口版本。破坏性变更才加：加一个可选字段不算，改一个已有
# 函数的签名才算（见设计说明 5.6）。
API_VERSION = 1


class BadManifest(Exception):
    """这份清单不能当插件用。`str(e)` 是给用户看的一句话。"""


def version_tuple(text, field):
    """把 `1.2.3` 变成 `(1, 2, 3)`。

    只吃数字点号，外加一个可选的 `-后缀`（`1.0.0-beta` 这种，后缀直接丢掉——
    预发布号在插件这套里没有语义）。**别的写法一律拒绝**，不猜：宁可说"你这版
    本号我看不懂"，也别把它当 0 悄悄放过去。
    """
    if not isinstance(text, str):
        raise BadManifest("{} 得是字符串，现在给的是 {}".format(
            field, type(text).__name__))
    core = text.strip().split("-")[0].split("+")[0]
    parts = core.split(".") if core else []
    if not parts or not all(p.isdigit() for p in parts):
        raise BadManifest("{} 得写成 1.2.3 这种，现在写的是 {!r}".format(field, text))
    return tuple(int(p) for p in parts)


def compare(left, right):
    """比两个版本号元组。短的一边补 0，所以 1.2 == 1.2.0。"""
    size = max(len(left), len(right))
    left = left + (0,) * (size - len(left))
    right = right + (0,) * (size - len(right))
    return (left > right) - (left < right)


class Manifest:
    """一份过了校验的清单。

    字段都是洗干净的：必填的齐了、类型对了、版本号已经解析成元组。**它不代表
    这个插件能跑** —— 能不能跑还要过 `gate()`。
    """

    def __init__(self, path, data):
        self.path = path
        self.id = data["id"].strip()
        self.name = data["name"].strip()
        self.version = data["version"].strip()
        self.version_tuple = version_tuple(self.version, "version")
        self.api_version = data["api_version"]
        self.min_host = data["min_host"].strip()
        self.min_host_tuple = version_tuple(self.min_host, "min_host")
        self.entry = data["entry"].strip()
        match = ENTRY_RE.match(self.entry)
        self.entry_file = match.group(1)
        self.entry_func = match.group(2)
        self.author = (data.get("author") or "").strip()
        self.description = (data.get("description") or "").strip()
        self.homepage = (data.get("homepage") or "").strip()

    @property
    def folder(self):
        return os.path.dirname(self.path)

    def __repr__(self):
        return "<Manifest {} {} v{}>".format(self.id, self.name, self.version)


def read(plugin_dir):
    """读一个插件目录里的 `plugin.json`；读不成抛 `BadManifest`。"""
    path = os.path.join(plugin_dir, FILE_NAME)
    if not os.path.isfile(path):
        raise BadManifest("没有 {}（每个插件都必须有它，清单不能写在代码里）"
                          .format(FILE_NAME))
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise BadManifest("{} 不是合法 JSON：{}".format(FILE_NAME, exc))
    except OSError as exc:
        raise BadManifest("{} 读不了：{}".format(FILE_NAME, exc))

    if not isinstance(data, dict):
        raise BadManifest("{} 顶层要是一个对象（{{...}}），现在是 {}".format(
            FILE_NAME, type(data).__name__))

    for field in REQUIRED:
        if field not in data:
            raise BadManifest("{} 里少了 {!r}".format(FILE_NAME, field))

    for field in ("id", "name", "version", "min_host", "entry"):
        if not isinstance(data[field], str) or not data[field].strip():
            raise BadManifest("{} 的 {!r} 要是非空字符串".format(FILE_NAME, field))

    if not isinstance(data["api_version"], int) or isinstance(data["api_version"], bool):
        raise BadManifest("{} 的 'api_version' 要是一个整数".format(FILE_NAME))

    # 这两个格式检查必须排在造 `Manifest` **之前**：构造函数一进去就拿 entry 去匹
    # 配正则（要拆出文件名和函数名），写法不对的话它先炸一个 AttributeError，
    # 用户看到的是 "NoneType has no attribute group"，不是这里这句人话。
    if not ID_RE.match(data["id"].strip()):
        raise BadManifest("id {!r} 只能有字母、数字、下划线".format(data["id"].strip()))
    if not ENTRY_RE.match(data["entry"].strip()):
        raise BadManifest("entry {!r} 得写成 '文件.py:函数名'（比如 "
                          "'__init__.py:register'）".format(data["entry"].strip()))

    manifest = Manifest(path, data)

    folder_name = os.path.basename(os.path.normpath(plugin_dir))
    if manifest.id != folder_name:
        raise BadManifest("id {!r} 跟目录名 {!r} 对不上——插件管理里按目录名列，"
                          "两处不一样会认错人".format(manifest.id, folder_name))

    return manifest


def gate(manifest, host_version, api_version=API_VERSION):
    """门禁：台宿主编不编得动这个插件。**返回 None = 能用**，否则返回原因。

    两个检查刻意分开报：`api_version` 不符是"这套接口对不上"，`min_host` 不够是
    "启动器该升级了"，让用户知道该往哪边使劲。
    """
    if manifest.api_version != api_version:
        return "这个插件要的接口版本是 {}，启动器现在提供的是 {}（接口对不上，不加载）"\
            .format(manifest.api_version, api_version)
    if compare(version_tuple(host_version, "宿主版本"),
               manifest.min_host_tuple) < 0:
        return "这个插件要求启动器至少 {}，现在是 {}（升级启动器，或者换一个插件版本）"\
            .format(manifest.min_host, host_version)
    return None
