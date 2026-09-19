"""Linux / macOS 这半边的系统层操作。

跟 winhost 最大的不同是这边没有 HWND。X11 底下窗口 id 是有的、Wayland 底下拿
不到、macOS 上又是另一套，所以这边不去"操作那扇窗口"，而是管我们自己起出去的
那个进程——记下它的 pid，它还在不在、要不要让它退。launcher 里"先拍快照、启
动、做差认领"那套流程一行没改，只是句柄的含义从 HWND 换成了 pid。

窗口位置这块是例外：那是启动器自己的窗口，Tk 本来就知道它在哪儿，不用问系统。
"""
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile

from claude_tool.claude import claude_args, claude_exe

# 内嵌那条路在非 Windows 上不存在，理由见 host.py。这里留着同名占位，好让
# host.py 的名字表两个平台完全一致。
_UNFINISHED = "{} 在 Linux/macOS 上还没做"


def _unfinished(name, why):
    def call(*_args, **_kwargs):
        raise NotImplementedError("{}：{}".format(
            _UNFINISHED.format(name), why))
    call.__name__ = name
    return call


# ── 内嵌终端：不做 ────────────────────────────────────────────────────────
EmbeddedConsole = _unfinished("EmbeddedConsole", "内嵌终端只留在 Windows")
spawn_console = _unfinished("spawn_console", "内嵌终端只留在 Windows")
fresh_console = _unfinished("fresh_console", "内嵌终端只留在 Windows")


# ── 挑一个终端模拟器 ──────────────────────────────────────────────────────
# "把一条命令交给终端窗口"这件事各家写法不一样，而且不是加个后缀的区别：
#   ("-e",)  后面跟 argv（xterm 这一系的公共约定）
#   ("--",)  后面跟 argv（gnome-terminal 这类：我自己的参数到此为止）
#   ()       直接跟 argv（kitty）
# 第三格是"能不能指定开在哪儿"，能的话用哪个开关。X 的 geometry 允许只给坐标
# 不给大小（"-geometry +100+200"），正合适——窗口开多大交给终端自己定。
#
# 只给 xterm 这一格递 -geometry：那套 Xt 的参数解析各家都一样、肯定不会认错。
# gnome-terminal 那一系的 --geometry 同样支持这个写法，但要是哪台机器上解析
# 出了岔子，代价是整个会话开不起来——为省用户一下手动挪窗口不值当。
#
# x-terminal-emulator 是 Debian 系备选方案的名字，指向哪个终端得看这台机器装了
# 什么，所以它排在表头但排在 $TERMINAL 后面。
TERMINALS = [
    ("x-terminal-emulator", ("-e",), None),
    ("gnome-terminal", ("--",), None),
    ("konsole", ("-e",), None),
    ("xfce4-terminal", ("-e",), None),
    ("mate-terminal", ("-e",), None),
    ("tilix", ("-e",), None),
    ("alacritty", ("-e",), None),
    ("kitty", (), None),
    ("xterm", ("-e",), "-geometry"),
]


def _terminal_style(name):
    """这个名字的终端该怎么递命令。表里没有的就按 -e 这一系当。"""
    for known, flags, geometry in TERMINALS:
        if name == known:
            return flags, geometry
    return ("-e",), None


def _pick_terminal():
    """挑一个装得上的终端：返回 (路径, 递命令用的参数, 摆位置用的开关)。

    先看 $TERMINAL——那是用户自己指定的话，他最清楚这台机器上有什么；名字在表
    里就照表里的写法走，它自己带的参数（比如 "kitty --single-instance"）原样
    留着，递命令那几个参数缀在后面。
    """
    candidates = []
    wanted = os.environ.get("TERMINAL", "").strip()
    if wanted:
        parts = shlex.split(wanted)
        flags, geometry = _terminal_style(os.path.basename(parts[0]))
        candidates.append((parts[0], parts[1:] + list(flags), geometry))
    for name, flags, geometry in TERMINALS:
        candidates.append((name, list(flags), geometry))
    for name, extra, geometry in candidates:
        path = shutil.which(name)
        if path:
            return path, extra, geometry
    return None, None, None


# ── 会话窗口 ──────────────────────────────────────────────────────────────
# 我们自己起出去的会话：句柄 -> 那个进程对象。Windows 那边靠"桌面窗口名单做差"
# 认领新窗口；这边进程就是我们起的，pid 当场就有，所以这份表本身就当窗口名单用，
# terminal_windows() / fresh_terminal() 读的都是它。
#
# macOS 的键是 pid 文件的路径而不是 pid：那边起窗口的是 Terminal.app 自己，进程
# 不归我们起，只能让脚本把自己的 pid 写出来（见 _spawn_macos）。
_SESSIONS = {}


def _script(argv):
    """交给终端跑的那条 shell 脚本：先 claude，退了再留个登录 shell。

    非 Windows 上没有 cmd /k 那种"跑完留着窗口"的开关，所以最后 exec 一个登录
    shell 停在那儿——用户能翻看刚才的输出，跟 Windows 上的行为对齐。
    """
    return "{}; exec {}".format(
        " ".join(shlex.quote(item) for item in argv),
        shlex.quote(os.environ.get("SHELL") or "sh"))


def _geometry_arg(geometry, x, y):
    """把 (x, y) 拼成这个终端认的 geometry 参数。

    X 的坐标只认正数：x 写成负的会被当成"距右边缘多少"，"+ -100 + 50" 这种拼法
    终端直接不认、整条命令都起不来。所以副屏那种负坐标（主屏在右边、副屏在左边）
    干脆不给坐标，让窗口管理器随便摆——摆错地方是小事，起不来是大事。
    """
    if x < 0 or y < 0:
        return None
    return "{} +{}+{}".format(geometry, int(x), int(y))


def _spawn_x11(argv, workdir, beside):
    exe, extra, geometry = _pick_terminal()
    if exe is None:
        raise RuntimeError(
            "这台机器上没找到能用的终端模拟器。装一个（xterm、gnome-terminal、"
            "konsole 都行）再启动，或者用 $TERMINAL 指定一个。")
    command = [exe] + extra
    if beside and geometry:
        arg = _geometry_arg(geometry, beside[0], beside[1])
        if arg:
            command.append(arg)
    command += ["sh", "-c", _script(argv)]
    # start_new_session：让终端独占一个进程组，关窗口时整组一起走（见 close_window）。
    process = subprocess.Popen(command, cwd=workdir, start_new_session=True)
    _SESSIONS[process.pid] = process
    return process


def _spawn_macos(argv, workdir):
    """macOS：Terminal.app 要的是一个脚本文件，不吃"命令加参数"那一套。

    写一份临时脚本交给它，脚本第一件事就是把 $$ 写出来——Terminal.app 开的那扇
    窗口不属于任何我们能拿到的进程，"这扇窗口还在不在"只能靠脚本记下的 pid 来
    回答（见 _pid_of）。

    这段没在真 Mac 上跑过，手上没有 Mac；照 Terminal.app 的公开用法写的。
    """
    holder = tempfile.mkdtemp(prefix="claude_tool_")
    pidfile = os.path.join(holder, "session.pid")
    script = os.path.join(holder, "launch.command")
    with open(script, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\necho $$ > {}\nexec {}\n".format(
            shlex.quote(pidfile),
            " ".join(shlex.quote(item) for item in argv)))
    os.chmod(script, 0o700)
    process = subprocess.Popen(["open", "-a", "Terminal", script], cwd=workdir)
    _SESSIONS[pidfile] = process
    return process


def spawn_terminal(workdir, cont=False, prompt=None, settings=None,
                   permission=None, beside=None):
    """在一个新终端窗口里跑 claude，返回那个进程对象。

    命令用 claude 的绝对路径（find_claude 刚找到的那个），argv 一项一项过
    shlex.quote：开场白里带空格、引号、中文都不怕，这一层不做任何"猜哪里断开"
    的事。

    beside 是"摆到启动器旁边"的坐标，只有认 -geometry 的终端吃这一口，别的照旧
    由窗口管理器随便摆——这边没有 Windows 那种事后再挪的办法。
    """
    argv = claude_args(cont, prompt, settings, permission, exe=claude_exe())
    if sys.platform == "darwin":
        return _spawn_macos(argv, workdir)
    return _spawn_x11(argv, workdir, beside)


def _pid_of(handle):
    """句柄换算成 pid。

    Linux 上句柄本身就是 pid；macOS 上是个 pid 文件（理由见 _spawn_macos），
    脚本还没跑起来的时候那文件还不存在，读不到就返回 None。
    """
    if isinstance(handle, int):
        return handle
    try:
        with open(handle, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _alive(pid):
    """这个进程还在不在。信号 0 不真发信号，只做存在性检查。"""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程在，只是不归我们管（换过用户之类），那也算在。
        return True
    return True


def window_alive(handle):
    """那个句柄还作不作数。用户自己把那扇窗口关了的话就是 False。"""
    if not handle:
        return False
    if isinstance(handle, int):
        # 进程退了、但还没人收尸（zombie）的时候，os.kill(pid, 0) 照样成功，会把
        # 一个已经关掉的会话一直报成开着的。Popen.poll() 一叫就把它收掉，那边
        # 才问得出真话。只认 pid 这条路——macOS 的 Popen 到的是 open，它交完活
        # 就退，拿它当判据会把活着的会话当场判死（见 terminal_windows）。
        proc = _SESSIONS.get(handle)
        if proc is not None and proc.poll() is not None:
            return False
    return _alive(_pid_of(handle))


def terminal_windows():
    """所有还开着的会话的句柄集合。跟 Windows 那份一样：拍快照、做差用。

    顺手把退干净的清出去。判据是"终端进程没了、pid 也查不着"——只认其中一个
    的话，macOS 那边（Popen 到的是 open，它交完活就退）会当场把活的会话清掉。
    """
    for handle in [h for h, proc in _SESSIONS.items()
                   if proc.poll() is not None and not window_alive(h)]:
        _SESSIONS.pop(handle, None)
    return set(_SESSIONS)


def fresh_terminal(known):
    """known 之外新冒出来的那个会话的句柄；没有就返回 None。

    一次只可能等到一个：启动是串行的，每开一个会话前都重拍一次快照。挑"最后
    登记的那个"就行——新的一定向后长，字典记的就是登记的次序。
    """
    fresh = [handle for handle in _SESSIONS if handle not in known]
    return fresh[-1] if fresh else None


def close_window(handle):
    """关掉一扇会话窗：给那个进程组发 SIGTERM。

    这边没有"请那扇窗口自己关掉"这种说法（Windows 上是 WM_CLOSE），只能照进程
    树来。我们自己起的那些是 start_new_session 起的、pid 就是组长，一锅端：终端、
    里面的 shell、claude 一起走。macOS 那种不归我们起的（pid 不是组长）就只杀它
    自己——对着别人的进程组开火，会连累不知道什么别的东西。
    """
    pid = _pid_of(handle)
    if pid is None or not _alive(pid):
        return False
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def bring_next_to(owner_window, target, gap=16):
    """把 target 挪到启动器旁边——这边做不到，返回 False。

    "按句柄把别人的窗口挪个位置"在 X11 上要另装 xdotool / wmctrl，Wayland 底下
    压根没这回事（谁也不能去指挥别人的窗口）。所以非 Windows 上界面上不摆这个
    按钮（见 host.WINDOW_CONTROL），这个函数留着只是为了让 host.py 的名字表
    两个平台一样。真想要的话有半个办法：起终端时用 -geometry 摆好，见 spawn_terminal。
    """
    return False


def toggle_topmost(target):
    """压顶层——同上，做不到，返回 None（界面上也不摆这个按钮）。"""
    return None


# ── 窗口位置 ──────────────────────────────────────────────────────────────
# Windows 那份问的是 Win32，因为 Tk 的 geometry 把 "-N" 读成"距屏幕右边缘 N
# 像素"，副屏的负坐标会被它摆到主屏上去。这边反过来：位置问 Tk 就够了，代价
# 是得自己把负坐标翻译成 Tk 认的那种写法。


def window_position(window):
    """窗口左上角在屏幕上的坐标，副屏上是负的。

    拿的是客户区在根窗口坐标系里的位置。有窗口管理器加边框时，它会比含边框
    的左上角差几个像素——存下来再摆回去，偏差是稳定的，不会一轮一轮漂。
    """
    return window.winfo_rootx(), window.winfo_rooty()


def place_window(window, x, y):
    """把窗口挪到 (x, y)。"""
    if x < 0 or y < 0:
        # Tk 的 "-N" 是"距屏幕右边/下边 N 像素"。想把窗口摆到 x=-100，就得写成
        # 距右边 屏宽+100。不这么翻的话，负坐标会被摆到屏幕另一头去。
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        x_part = "-{}".format(screen_w - x) if x < 0 else "+{}".format(x)
        y_part = "-{}".format(screen_h - y) if y < 0 else "+{}".format(y)
    else:
        x_part, y_part = "+{}".format(x), "+{}".format(y)
    window.geometry("{}{}".format(x_part, y_part))


def screen_bounds(window):
    """虚拟桌面边界（左, 上, 右, 下）。多屏拼起来那一整块。

    问 Tk 的虚拟根窗口：这几个数是屏幕本身的尺寸，跟问哪个窗口没关系。
    """
    x = window.winfo_vrootx()
    y = window.winfo_vrooty()
    return (x, y, x + window.winfo_vrootwidth(), y + window.winfo_vrootheight())


# ── 交给系统的默认程序 ────────────────────────────────────────────────────
# xdg-open 是桌面这一套的标准入口；精简的发行版（WSL 那个就是）常常没装，装了
# GNOME 那一套的话 gio 一定在。两个都试，都试不着就报一条能看懂的上去——界面
# 那边会把它摆到反馈行里。


def _openers():
    if sys.platform == "darwin":
        return [["open"]]
    return [["xdg-open"], ["gio", "open"]]


def _open(target):
    for opener in _openers():
        if shutil.which(opener[0]):
            subprocess.Popen(opener + [target])
            return
    raise OSError("这台机器上没有能打开它的程序（试过 xdg-open 和 gio）")


def open_path(path):
    """用文件管理器打开一个目录（或文件）。"""
    _open(path)


def open_url(url):
    """用默认浏览器打开一个网址。"""
    _open(url)


# ── 丢回收站 ──────────────────────────────────────────────────────────────
# Linux 这边没有统一的"回收站"调用，各家的实现是一套独立的东西（FreeDesktop
# 定了个规范，gio 和 trash-cli 都照它做）。挑一个装得上就用。


def _trashers():
    for cmd in (["gio", "trash"], ["trash-put"]):
        if shutil.which(cmd[0]):
            return cmd
    return []


def _trash_macos(path):
    """macOS：挪进 ~/.Trash，重名就缀一个 -2、-3。

    Finder 的回收站没有命令行入口，只能自己挪。没在真 Mac 上跑过（手上没有
    Mac），照 ~/.Trash 这个约定写的。
    """
    trash = os.path.join(os.path.expanduser("~"), ".Trash")
    try:
        os.makedirs(trash, exist_ok=True)
        name = os.path.basename(os.path.abspath(path))
        target = os.path.join(trash, name)
        n = 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(trash, "{}-{}".format(name, n))
        shutil.move(path, target)
    except OSError:
        return False
    return True


def trash_path(path):
    """把 path 丢进回收站，返回成没成。

    两个工具都找不着就【不删】——返回 False 让上面把话说明白，绝不用 rm -rf
    兜底：那是永久删，跟这里的要求正相反。宁可留着让用户自己删。
    """
    if not path or not os.path.exists(path):
        return False
    if sys.platform == "darwin":
        return _trash_macos(path)
    cmd = _trashers()
    if not cmd:
        return False
    try:
        done = subprocess.run(cmd + [path], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    except OSError:
        return False
    # 有些实现失败了也返回 0，所以两头都看：退出码是 0、而且文件真的不在了。
    return done.returncode == 0 and not os.path.exists(path)
