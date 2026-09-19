"""Linux / macOS 这半边的系统层操作。

跟 winhost 最大的不同是这边没有 HWND。X11 底下窗口 id 是有的、Wayland 底下拿
不到、macOS 上又是另一套，所以这边不去"操作那扇窗口"，而是管我们自己起出去的
那个进程——记下 PID，它还在不在、要不要让它退。

窗口位置这块是例外：那是启动器自己的窗口，Tk 本来就知道它在哪儿，不用问系统。
"""

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

# ── 会话窗口：还没写 ─────────────────────────────────────────────────────
# Windows 上这些收的是 HWND；这边会改成收一个我们自己发的、带 PID 的会话句柄。
bring_next_to = _unfinished("bring_next_to", "要改成按进程找终端窗口")
close_window = _unfinished("close_window", "要改成按进程结束会话")
fresh_terminal = _unfinished("fresh_terminal", "要改成按进程找终端窗口")
terminal_windows = _unfinished("terminal_windows", "要改成按进程找终端窗口")
toggle_topmost = _unfinished("toggle_topmost", "要改成按进程找终端窗口")


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
