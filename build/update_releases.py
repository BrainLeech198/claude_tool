"""把刚打出来的这一版写进 docs/releases.js。

不用手动跑——build/打包.bat 和 build/打包.sh 的最后一步都会调它：

    python build/update_releases.py            # Windows（打包.bat 调的就是这条）
    python3 build/update_releases.py linux     # Linux（打包.sh 调的）

不写平台就是 Windows，所以那条老命令一个字没变。两个平台各写各的那一格
（`windows` / `linux`），互不覆盖：同一个版本号先打 Windows 再打 Linux，第二次
是在已有那条上补 `linux`，`notes` 和另一格原样留着。

版本号从 build/claude_tool.iss 里读，那是唯一一处写死版本的地方；产物从
Output\\ 里按版本号找。同一个版本号只更新不重复，别的版本一个字不动。

**发布说明（notes）得自己填**：那是写给人看的话，机器猜不出来。自动补进来的条目
notes 是空的，页面上就不显示那一行；想写就在 docs/releases.js 里补一句。

**配着测过哪个版本的 claude 不用填**：脚本自己跑一次本机 `claude --version`，
记进这一条记录的 `claude` 字段。打包机上装的是哪个版本，官网上就写哪个。没装
claude、或者读不出版本号，这格就留空，页面上不显示（跟 notes 一个道理）。
注意它有平台差异：Linux 上得 PATH 里真有 claude 才读得到，读不到就留空。

文件名是 ASCII 的：打包.bat 只能用 ASCII，它要按名字调这个脚本。（这个文件本身
没有那个限制，中文随便写——cmd 只限制自己解析的那份 bat。）

Gitee 那边的「发行版」得手动建一遍：tag 就打版本号本身（`0.1.0`，别加 v 前缀），
把 Output 里那个 setup.exe（或者 Linux 的 tar.gz）传成附件。官网的下载直链就按
这个形状拼出来的，少了哪一步点下载就是 404。
"""
import glob
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

# 每个平台怎么在 Output/ 里认出自己的产物。Windows 是死的（Inno 的
# OutputBaseFilename 定下了）；Linux 用通配是因为 tar 的名字里带架构（uname -m），
# 脚本不写死，将来在 ARM 上打也认得出。
ARTIFACTS = {
    "windows": "ClaudeLauncher-{}-Setup.exe",
    "linux": "ClaudeLauncher-{}-linux-*.tar.gz",
}
# releases.js 里每条记录都有的三个平台格子，没有的那格是 null，页面上画成灰的。
PLATFORM_KEYS = ("windows", "linux", "macos")


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


def find_artifact(platform, version):
    """在 Output/ 里认出这一版这个平台的产物，返回文件名。

    Linux 用通配匹配架构那一段，所以可能一匹配就是好几个（同一版打了 x86_64 又打
    了 aarch64）。releases.js 里一格只放得下一个，这时候不猜——报出来让人自己删。
    """
    pattern = os.path.join(OUTPUT, ARTIFACTS[platform].format(version))
    hits = sorted(os.path.basename(p) for p in glob.glob(pattern))
    if not hits:
        sys.exit("找不到产物：{}\n先把这个平台的打包那几步跑完。".format(pattern))
    if len(hits) > 1:
        sys.exit("Output/ 里这一版的 {} 产物不止一个，releases.js 一格放不下：\n"
                 "  {}\n只留要发的那一个，其余挪走再跑。".format(platform,
                                                              "\n  ".join(hits)))
    return hits[0]


def main():
    platform = sys.argv[1] if len(sys.argv) > 1 else "windows"
    if platform not in ARTIFACTS:
        sys.exit("不认识这个平台：{}（只能是 {}）".format(
            platform, " / ".join(sorted(ARTIFACTS))))

    version = read_version()
    name = find_artifact(platform, version)
    size = os.path.getsize(os.path.join(OUTPUT, name))
    date = time.strftime("%Y-%m-%d")

    with open(DATA, encoding="utf-8") as f:
        text = f.read()
    start = text.index(MARKER) + len(MARKER)
    end = text.index("];", start) + 1
    releases = json.loads(text[start:end])

    fresh = {"version": version, "date": date, "notes": "",
             "claude": local_claude(), "windows": None, "linux": None,
             "macos": None}
    fresh[platform] = {"file": name, "size": size}
    for old in releases:
        if old.get("version") == version:
            # 重打同一个版本：只刷新日期和本平台那格的 file/size，其余一律留着。
            # 说明（notes）是手写的；本平台那格里还可能有个手填的 url（指到镜像
            # 源），整格换掉就把它们一起抹了。另外两格是别的平台的东西，一个字节
            # 都不动——先打 Windows 再打 Linux，第二次不能把第一次写的抹了。
            old["date"] = date
            pkg = old.get(platform)
            if isinstance(pkg, dict):
                pkg.update(fresh[platform])
            else:
                old[platform] = fresh[platform]
            for key in PLATFORM_KEYS + ("notes",):
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

    print("docs/releases.js  <-  v{} · {} · {} · {:.1f} MB · claude {}".format(
        version, platform, name, size / 1048576, fresh["claude"] or "没测到"))


if __name__ == "__main__":
    main()
