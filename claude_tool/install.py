"""没装 claude 的时候，帮着照这台机器装一个。

这层只回答两件事：这台机器能走哪几条装法、每条那张命令长什么样。不含 tkinter，
也不自己开窗口——面板那半在 ui/dialogs.py，跑起来之后的输出往界面上流。

**参数都能塞假的进来**（platform / which / node）：探针靠这个在三种平台上各撞
一遍，不用真装一次 claude。

装法全照官方那套（见 DOCS_URL）：官方现在推**原生安装脚本**——不依赖 Node、
装完自己管更新；winget / brew / npm 是备选，装完更新归各自的包管理器管。这一点
在面板上得跟用户说清楚，不然它跟启动器里「查更新」看到的口径会对不上。
"""
import os
import re
import shutil
import subprocess
import sys
import threading

from claude_tool.claude import CREATE_NO_WINDOW
from claude_tool.paths import LOCAL_BIN, LOCAL_NAMES

DOCS_URL = "https://code.claude.com/docs/en/setup"
NATIVE_PS1 = "https://claude.ai/install.ps1"
NATIVE_SH = "https://claude.ai/install.sh"
WINGET_ID = "Anthropic.ClaudeCode"
BREW_CASK = "claude-code"
NPM_PACKAGE = "@anthropic-ai/claude-code"

# npm 那条路要 Node 18 以上：Claude Code 的包用了老版本 Node 不认的语法，
# 低了会在启动时报一句语法错误，看着像"装了但坏了"。
MIN_NODE = (18,)

# 安装命令有时候会挂着不动（等一个永远不来的网络）。兜一个上限，到点掐掉，
# 别让面板一直转下去。
INSTALL_TIMEOUT = 600

PLATFORM_NAMES = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}

# "自己查"和"查出来是没装"得分开——前者要真去问一次机器，后者是探针塞进来的答案。
UNSET = object()


def platform_name(platform=None):
    platform = platform or sys.platform
    return PLATFORM_NAMES.get(platform, platform)


def node_version(which=shutil.which):
    """本机 Node 的版本 (20, 11, 0)，没装或者读不出来返回 None。"""
    exe = which("node")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "--version"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,
                              creationflags=CREATE_NO_WINDOW, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_version(proc.stdout)


def parse_version(raw):
    """从 "v20.11.0" / "20.11.0" 里揪出版本号，一个数字都没有返回 None。

    跟 versions.py 那个 parse 是一个路数，但那边认的是 claude 自己的版本串；
    这里只要 node 那种干干净净的 "vX.Y.Z"，不值得为它把两边焊在一起。
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    found = re.search(r"\d+(?:\.\d+)*", str(raw or ""))
    return tuple(int(part) for part in found.group(0).split(".")) if found else None


def version_text(parts):
    return "v" + ".".join(str(part) for part in parts)


def local_bin_claude(bin_dir=None):
    """~/.local/bin 底下那个 claude 的路径，没有返回 None。

    装完就是"在硬盘上但 PATH 里还没有"的那种：find_claude 会认它（所以装完当场
    就能用），这里专门问一遍是为了在体检表上把这句话摆出来。位置跟 find_claude
    认的是同一组，别各写一份。
    """
    bin_dir = bin_dir or LOCAL_BIN
    for name in LOCAL_NAMES:
        candidate = os.path.join(bin_dir, name)
        if os.path.exists(candidate):
            return candidate
    return None


def _route(name, why, needs, ready, argv, show):
    return {"name": name, "why": why, "needs": needs, "ready": ready,
            "argv": argv, "show": show}


def routes(platform=None, which=shutil.which, node=UNSET):
    """这台机器能走的装法，一条一项。

    platform / which / node 传进来就是"照这个模拟"，不传就照本机真实情况。
    node 传 None 表示"没装"，传 UNSET（默认）表示自己去问机器。

    每一项：
      name  装法叫什么        why   一句人话说明
      needs 前提（说明白了灰着也是有用的信息）
      ready 能不能走          argv  真要跑的命令
      show  摆给用户看/复制的那一行（跟 argv 是同一件事的两种形态）

    第一条永远是原生脚本——官方推荐它，而且它不需要任何前提。顺序就是面板上
    从上到下的顺序。
    """
    platform = platform or sys.platform
    if node is UNSET:
        node = node_version(which)
    found = []

    if platform == "win32":
        # irm 是 PowerShell 自带的，Windows 上这条不需要前提。
        found.append(_route(
            "官方原生脚本",
            "官方推荐的那条，装完它自己会更新，不用你管。",
            "不需要装别的东西",
            True,
            ["powershell", "-NoProfile", "-Command",
             "irm {} | iex".format(NATIVE_PS1)],
            "irm {} | iex".format(NATIVE_PS1)))
    else:
        # install.sh 自己就是 curl 拉下来的，所以 curl 是这条唯一的前提。
        have_curl = which("curl") is not None
        found.append(_route(
            "官方原生脚本",
            "官方推荐的那条，装完它自己会更新，不用你管。",
            "不需要装别的东西" if have_curl else "没找到 curl，这条走不了",
            have_curl,
            ["bash", "-c", "curl -fsSL {} | bash".format(NATIVE_SH)],
            "curl -fsSL {} | bash".format(NATIVE_SH)))

    if platform == "win32":
        have_winget = which("winget") is not None
        found.append(_route(
            "winget",
            "Windows 自带的包管理器。装完更新得靠 winget upgrade，"
            "它跟 claude 自己的自动更新不是一条路。",
            "这台机器有 winget" if have_winget else "这台机器上没有 winget",
            have_winget,
            ["winget", "install", "--id", WINGET_ID,
             "--accept-package-agreements", "--accept-source-agreements"],
            "winget install --id {} --accept-package-agreements "
            "--accept-source-agreements".format(WINGET_ID)))

    if platform == "darwin":
        have_brew = which("brew") is not None
        found.append(_route(
            "Homebrew",
            "macOS 上最顺手的一条。装完更新走 brew upgrade。",
            "这台机器有 brew" if have_brew else "这台机器上没有 brew",
            have_brew,
            ["brew", "install", "--cask", BREW_CASK],
            "brew install --cask {}".format(BREW_CASK)))

    # npm 三个平台都摆——Linux 上它往往是唯一一条现成的路。
    have_npm = which("npm") is not None
    if not have_npm:
        node_note, node_ready = "没装 Node/npm，这条走不了", False
    elif node is None:
        # npm 在、node 问不出来：少见（nvm 的 shim 有时这样），当作能用，
        # 别为一句读不到的话把唯一一条路堵死。
        node_note, node_ready = "有 npm", True
    elif node < MIN_NODE:
        node_note = "Node 太旧（{}），要 18 以上".format(version_text(node))
        node_ready = False
    else:
        node_note, node_ready = "Node {}".format(version_text(node)), True
    found.append(_route(
        "npm",
        "要有 Node 才行。装完更新走 npm，跟 claude 自己的自动更新不是一条路。",
        node_note,
        node_ready,
        ["npm", "install", "-g", NPM_PACKAGE],
        "npm install -g {}".format(NPM_PACKAGE)))

    return found


def checkup(platform=None, which=shutil.which, node=UNSET, claude_path=None,
            bin_dir=None):
    """面板顶上那张体检表，一行一项：(项目, 结果, 这一项算不算好)。

    只报"这台机器上是什么"，不动手也不拦着。哪条路走不了，是上面 routes() 那
    几项自己的事，这里只是把理由摆在明处。
    """
    platform = platform or sys.platform
    if node is UNSET:
        node = node_version(which)
    rows = [("系统", platform_name(platform), True)]

    if claude_path:
        rows.append(("claude", claude_path, True))
    else:
        rows.append(("claude", "还没找到", False))
        # 装到 PATH 外面那种：硬盘上有、`claude` 这五个字母敲不出来。这一条
        # 正是启动器能帮上忙的地方，得单独说。
        came_out = local_bin_claude(bin_dir)
        if came_out:
            rows.append(("~/.local/bin", "有 claude，只是 PATH 里还没有", True))

    if platform == "win32":
        rows.append(("winget", "有" if which("winget") else "没有",
                     bool(which("winget"))))
    if platform == "darwin":
        rows.append(("brew", "有" if which("brew") else "没有", bool(which("brew"))))
    rows.append(("npm", "有" if which("npm") else "没有", bool(which("npm"))))
    rows.append(("Node", version_text(node) if node else "没装", node is not None))
    return rows


def decode(raw):
    """子进程吐出来那行字节 -> 给人看的字符串。

    先按 UTF-8 解，解不动换 cp936（这台机器控制台那套编码），再不行才 replace。
    安装脚本是别人写的，它按什么编码吐中文我们管不了，能认出中文就够了。

    别改成让 subprocess 自己 text=True：那按 locale 解，这台机器上是 cp936，
    而 npm / powershell 那些多半吐 UTF-8，一对不上就是一片乱码。
    """
    raw = raw.rstrip(b"\r")
    for encoding in ("utf-8", "cp936"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run_stream(argv, on_line, timeout=INSTALL_TIMEOUT):
    """跑一条安装命令，每出一行就叫一次 on_line，返回退出码。

    拉不起来（没有这个程序、权限不够）返回 None，并且已经把原因交给 on_line 了。

    读数用 os.read 而不是 proc.stdout.read(n)：后者会一直等到凑满 n 个字节或者
    进程结束，安装脚本慢吞吞吐一行的时候界面上什么都看不见。os.read 是有多少
    拿多少，这才叫流式。

    **别在这层碰界面**：调用方把它丢进一个线程里，on_line 只管往队列里塞，
    取和画都在主线程（Tk 的东西不能跨线程碰）。
    """
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
    except OSError as error:
        on_line("拉不起来：{}".format(error))
        return None

    # 挂死的安装总得有个头：到点掐掉，下面那次 read 会立刻返回空、循环就出去了。
    killer = threading.Timer(timeout, proc.terminate)
    killer.start()
    pending = b""
    try:
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                on_line(decode(line))
        if pending:
            on_line(decode(pending))
    except OSError:
        # 掐进程那一下可能把管道也带走了，这时候 read 会抛。装完的账照记，
        # 不为了收尾这点噪音把整个安装判成失败。
        pass
    finally:
        killer.cancel()
        proc.stdout.close()
    return proc.wait()
