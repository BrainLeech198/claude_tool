"""把刚打出来的这一版写进 docs/releases.js。

不用手动跑——build/打包.bat 的最后一步会调它。想单独补一条也可以：

    python build/update_releases.py

版本号从 build/claude_tool.iss 里读，那是唯一一处写死版本的地方；安装包从
Output\\ 里按版本号找。同一个版本号只更新不重复，别的版本一个字不动。

**发布说明（notes）得自己填**：那是写给人看的话，机器猜不出来。自动补进来的条目
notes 是空的，页面上就不显示那一行；想写就在 docs/releases.js 里补一句。

**配着测过哪个版本的 claude 不用填**：脚本自己跑一次本机 `claude --version`，
记进这一条记录的 `claude` 字段。打包机上装的是哪个版本，官网上就写哪个。没装
claude、或者读不出版本号，这格就留空，页面上不显示（跟 notes 一个道理）。

文件名是 ASCII 的：打包.bat 只能用 ASCII，它要按名字调这个脚本。（这个文件本身
没有那个限制，中文随便写——cmd 只限制自己解析的那份 bat。）

Gitee 那边的「发行版」得手动建一遍：tag 就打版本号本身（`0.1.0`，别加 v 前缀），
把 Output 里那个 setup.exe
传成附件。官网的下载直链就按这个形状拼出来的，少了哪一步点下载就是 404。
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 找 claude、解版本号都借包里的那份定义——官网上写下的这个数，回头要跟启动器
# 自己查出来的比大小（claude_tool/versions.py），两边解出来的形状必须一样。
sys.path.insert(0, ROOT)
from claude_tool.claude import find_claude        # noqa: E402
from claude_tool.versions import number, parse    # noqa: E402

ISS = os.path.join(ROOT, "build", "claude_tool.iss")
DATA = os.path.join(ROOT, "docs", "releases.js")
OUTPUT = os.path.join(ROOT, "Output")
# releases.js 里那行赋值。这个脚本只换等号后面那个数组，文件里其它东西（注释、
# DOWNLOAD_BASE）原样留着。
MARKER = "window.RELEASES = "


def read_version():
    # .iss 是 UTF-8 带 BOM 的（Inno 靠 BOM 认出它是 UTF-8），utf-8-sig 吃掉 BOM。
    with open(ISS, encoding="utf-8-sig") as f:
        text = f.read()
    found = re.search(r'^#define\s+AppVersion\s+"([^"]+)"', text, re.M)
    if not found:
        sys.exit("claude_tool.iss 里没找到 #define AppVersion")
    return found.group(1)


def local_claude():
    """本机上 claude 的版号文本（"2.1.278"）；没装或读不出来返回空串。

    `claude --version` 在老版本上有时往 stderr 写，两路都收；输出可能不止一行
    （新版偶尔跟一句更新提示），parse 只认第一段数字，正好。整件事都不该拦住
    打包：读不到就返回空串，记进记录里，页面上不显示那一格。
    """
    exe = find_claude()
    if not exe:
        return ""
    try:
        result = subprocess.run([exe, "--version"], capture_output=True,
                                timeout=30)
    except Exception:
        return ""
    text = (result.stdout or result.stderr or b"").decode("utf-8", "replace")
    parts = parse(text)
    return number(parts) if parts else ""


def main():
    version = read_version()
    exe = "ClaudeLauncher-{}-Setup.exe".format(version)
    installer = os.path.join(OUTPUT, exe)
    if not os.path.isfile(installer):
        sys.exit("找不到安装包：{}\n先把 打包.bat 的 PyInstaller 和 Inno 两步跑完。".format(installer))

    size = os.path.getsize(installer)
    date = time.strftime("%Y-%m-%d")

    with open(DATA, encoding="utf-8") as f:
        text = f.read()
    start = text.index(MARKER) + len(MARKER)
    end = text.index("];", start) + 1
    releases = json.loads(text[start:end])

    fresh = {"version": version, "date": date, "notes": "",
             "claude": local_claude(),
             "windows": {"file": exe, "size": size}, "linux": None, "macos": None}
    for old in releases:
        if old.get("version") == version:
            # 重打同一个版本：只刷新日期和 file/size，其余一律留着。说明（notes）
            # 是手写的；windows 里还可能有个手填的 url（指到镜像源），整块换掉
            # 就把它们一起抹了。
            old["date"] = date
            pkg = old.get("windows")
            if isinstance(pkg, dict):
                pkg.update(fresh["windows"])
            else:
                old["windows"] = fresh["windows"]
            for key in ("notes", "linux", "macos"):
                old.setdefault(key, fresh[key])
            # claude 这格只补空的：上一版记录里已经写了"配 claude X 测过"，就不
            # 改动它——重打包不等于重新测过，别让"最近一次打包时本机是哪个版本"
            # 悄悄改掉已经发出去的那句话。空的时候才填（上次打包时没装 claude）。
            if not old.get("claude"):
                old["claude"] = fresh["claude"]
            break
    else:
        releases.insert(0, fresh)   # 最新的排最前面，页面上那三张卡片拿的就是 0 号

    body = json.dumps(releases, ensure_ascii=False, indent=2)
    with open(DATA, "w", encoding="utf-8", newline="\n") as f:
        f.write(text[:start] + body + text[end:])

    print("docs/releases.js  <-  v{} · {} · {:.1f} MB · claude {}".format(
        version, exe, size / 1048576, fresh["claude"] or "没测到"))


if __name__ == "__main__":
    main()
