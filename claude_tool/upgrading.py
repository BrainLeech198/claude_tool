"""「没装」之前先看一眼：是不是正卡在 npm 自动升级的空档里。

npm 换包分两步：先把旧包目录改名（`.claude-code-xxxxxx`）、把 claude / claude.cmd
这些启动器从 npm 目录里删掉，等新版下完才重建启动器。中间空档可长达十几分钟，这
期间 PATH 上找不到命令行，看着跟「没装」一模一样——用户一点「装/修」就是
`npm install -g` 正好踩进升级中间态，反而真可能装坏。

只看盘上的痕迹，不联网、不跑 npm、不动任何文件：

  - npm 目录里的改名残骸（`.claude.cmd-xxxxxx` 这种点开头、带随机尾巴的）
  - 包目录还在、启动器没了（npm 升级空档的正中间）
  - 几百字节、不是 PE 的占位 exe（新版二进制还没下完）

返回的是人话证据列表，给界面上的告示当依据；空表就是真没装、或者真装好了。
"""
import os

from claude_tool import agents

# 小于这个字节数、又不是 MZ 开头的 .exe，当成升级没下完的占位。真 exe 无论
# 多小都带 PE 头；npm 的 .cmd/.ps1 启动器本来就是几百字节的文本，不走这条判。
STUB_MAX = 4096


def _layout(profile):
    """npm 包的 (相对目录段, 残骸可能躺着的相对目录段们)；没有 npm 包给 (None, ())。"""
    package = profile.npm_package
    if not package:
        return None, ()
    parts = tuple(p for p in package.split("/") if p)
    if len(parts) == 2:
        return ("node_modules",) + parts, ("node_modules", parts[0])
    return ("node_modules",) + parts, ("node_modules",)


def _stems(profile):
    """残骸名里可能出现的那几个词头：包名、可执行名、shim 名。"""
    stems = set()
    if profile.npm_package:
        stems.add(profile.npm_package.split("/")[-1])
    for name in profile.exe_names:
        stems.add(name)
        base = name.split(".")[0]
        stems.add(base)
        stems.add(base + ".cmd")
        stems.add(base + ".ps1")
    return stems


def _remnant(entry, stems):
    """`.claude.cmd-jVm5tZOx` 这种残骸认出来返回原名，不是返回 None。"""
    if not entry.startswith("."):
        return None
    stem, dash, suffix = entry[1:].rpartition("-")
    if dash and stem in stems and suffix.isalnum():
        return entry
    return None


def _stub(path):
    """几百字节、不是 PE 开头的 exe：升级没下完的占位。"""
    try:
        if os.path.getsize(path) >= STUB_MAX:
            return False
        with open(path, "rb") as handle:
            return handle.read(2) != b"MZ"
    except OSError:
        return False


def _roots(profile, env):
    """npm 全局目录最可能在的那几个地方：PATH 各段、%APPDATA%\\npm、档位自己的搜索目录。"""
    dirs = [part for part in (env.get("PATH") or "").split(os.pathsep) if part]
    appdata = env.get("APPDATA")
    if appdata:
        dirs.append(os.path.join(appdata, "npm"))
    dirs += list(profile.search_dirs())
    seen, out = set(), []
    for directory in dirs:
        key = os.path.normcase(os.path.abspath(directory))
        if key in seen:
            continue
        seen.add(key)
        out.append(directory)
    return out


def evidence(profile=None, environ=None):
    """这个 agent 身上有没有「正在自动升级」的痕迹，有几条给几条（人话）。

    空列表 = 没有任何痕迹（真没装、或真装好了）。只读盘，一个字节都不落。
    """
    profile = profile or agents.get(agents.DEFAULT_AGENT)
    env = os.environ if environ is None else environ
    pkg_rel, scan_rel = _layout(profile)
    stems = _stems(profile)
    found = []
    for root in _roots(profile, env):
        for rel in (None,) + scan_rel:
            folder = root if rel is None else os.path.join(root, *rel)
            try:
                entries = sorted(os.listdir(folder))
            except OSError:
                continue
            for entry in entries:
                if _remnant(entry, stems):
                    found.append("npm 目录里有改名残骸 {}——自动升级的痕迹"
                                 .format(os.path.join(folder, entry)))
        if pkg_rel and os.path.isdir(os.path.join(root, *pkg_rel)):
            if not any(os.path.exists(os.path.join(root, name))
                       for name in profile.exe_names):
                found.append("包还在、启动器没了（{} 下）——正卡在自动升级的空档"
                             .format(root))
        for name in profile.exe_names:
            if not name.lower().endswith(".exe"):
                continue
            places = [os.path.join(root, name)]
            if pkg_rel:
                places.append(os.path.join(root, *(pkg_rel + (name,))))
                places.append(os.path.join(root, *(pkg_rel + ("bin", name))))
            for path in places:
                if _stub(path):
                    found.append("{} 是几百字节的占位——自动升级没下完".format(path))
    return found


def notice(profile=None):
    """「正在自动升级」的告示全文。evidence 是判断依据（盘上的痕迹），这段是说给
    人听的结论和下一步——命令行、界面、安装面板都拿它当同一份口径。
    """
    profile = profile or agents.get(agents.DEFAULT_AGENT)
    package = profile.npm_package or profile.exe_names[0]
    lines = [
        "{} 没被卸载、PATH 也没丢：这是 npm 自动升级的空档——升级时先把启动器删掉、"
        "等新版下完才重建，中间几分钟到十几分钟找不到命令行是正常的。"
        .format(profile.label),
        "",
        "现在别重装、别跑 npm install，正好会踩进升级中间态。等升级完，开个新窗口"
        "就能用（老终端读的是旧状态）。",
        "",
        "真卡住了（十几分钟还这样），再手动重装：",
        "  npm install -g {}".format(package),
    ]
    # 这个开关是 claude-code 自己读的，别的档位说了也是白说，别误导。
    if profile.id == "claude":
        lines += [
            "",
            "想不再被静默升级打断，在 {} 的 env 里加：".format(profile.settings_file),
            '  "DISABLE_AUTOUPDATER": "1"',
        ]
    return "\n".join(lines)
