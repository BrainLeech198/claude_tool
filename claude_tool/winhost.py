"""Windows 窗口层面的操作：找控制台窗口、把它贴到我们的窗口上、摆位置。

内嵌用的是老 conhost.exe，不用默认的 Windows Terminal：WT 一扇窗口上挂着好几个
标签页，我们没法只挪其中一个过来，挪整扇等于把别的会话一起带过来。代价：conhost
没有字体回退（它默认用新宋体），claude 界面里少数符号会显示成方框，而 WT 里不会。
"""
import ctypes
import os
import subprocess

from claude_tool import agents
from claude_tool.agent import (CREATE_NEW_CONSOLE, agent_args, agent_command,
                               spawn_prefix)
from claude_tool.geometry import fit_rect


# ── 内嵌终端 ──────────────────────────────────────────────────────────────
# 内嵌是"贴上去"：拉一扇 conhost 窗口，认启动器当 owner 贴在终端栏那块矩形上。
# 为什么不用默认的 Windows Terminal：WT 一扇窗口上挂着好几个标签页，我们没法
# 只把其中一个挪过来，挪整扇等于把别的会话一起带过来。代价：conhost 没有字体
# 回退（它默认用新宋体），claude 界面里少数符号会显示成方框，而 WT 里不会。

GWL_STYLE = -16
GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WM_CLOSE = 0x0010
SW_HIDE = 0
SW_RESTORE = 9
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
WS_VISIBLE, WS_POPUP = 0x10000000, 0x80000000
WS_BORDER, WS_CAPTION, WS_THICKFRAME = 0x00800000, 0x00C00000, 0x00040000
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
CONSOLE_CLASS = "ConsoleWindowClass"
# 默认终端是 Windows Terminal 的时候，cmd /k 开出来的是这个类名的窗口，
# 上面那个 ConsoleWindowClass 一个都找不到。两个都得认。
TERMINAL_CLASSES = (CONSOLE_CLASS, "CASCADIA_HOSTING_WINDOW_CLASS")

# 内嵌控制台的标题标记。claude 那条值是 "claude-embed"——探针拿它认窗，不能改。
# 用 title_tag() 按档位取，别直接用这个常量（codebuddy 的是 "codebuddy-embed"）。
TITLE_TAG = "claude-embed"


def title_tag(agent_id=None):
    """内嵌控制台的标题标记，用来认出自己开的那扇窗口。claude → "claude-embed"。"""
    return "{}-embed".format(agent_id or "claude")

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

# 句柄是 64 位的指针，不声明 argtypes 的话 ctypes 会按 32 位 int 截断——进出
# 两头都得声明，只声明一头等于另一头照截。
_user32 = ctypes.windll.user32
_user32.GetParent.argtypes = [ctypes.c_void_p]
_user32.GetParent.restype = ctypes.c_void_p
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
# 认 owner 走的是 GWLP_HWNDPARENT，值是个句柄——得用 Ptr 那版，Long 那版只吃
# 32 位，高半截会被砍掉。
_user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                      ctypes.c_void_p]
_user32.SetWindowLongPtrW.restype = ctypes.c_void_p
_user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p]
_user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
_user32.SetForegroundWindow.restype = ctypes.c_bool
_user32.IsWindow.argtypes = [ctypes.c_void_p]
_user32.IsWindow.restype = ctypes.c_bool


def _windows_of(classes):
    """桌面上类名落在这几个里的顶层窗口：[(句柄, 类名, 标题)]。"""
    found = []

    def visit(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, name, 64)
        if name.value in classes:
            title = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, title, 256)
            found.append((hwnd, name.value, title.value))
        return True

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def console_windows():
    """当前桌面上所有老式控制台窗口的 (句柄, 标题)。内嵌那条路只认 conhost。"""
    return [(hwnd, title) for hwnd, _cls, title in _windows_of((CONSOLE_CLASS,))]


# ── 独立窗口 ──────────────────────────────────────────────────────────────
# 独立会话的宿主是 Windows Terminal（WinUI3 应用，没法当子窗口塞进别的窗口，
# 也没法改它的字体内核，所以只能"根据句柄控制"：移位置、压顶层、递个关闭请
# 求）。窗口是异步建出来的，只能拿启动前拍的那份名单去比对，才知道哪扇是新的。

def terminal_windows():
    """所有终端窗口的句柄集合。拍快照、做差用，只要键不要别的。"""
    return {hwnd for hwnd, _cls, _title in _windows_of(TERMINAL_CLASSES)}


def fresh_terminal(known):
    """known 之外新冒出来的那扇终端窗口的句柄；没有就返回 None。

    一次只可能等到一扇：启动是串行的，每开一个会话前都重拍一次快照。真碰上前
    一扇还没建完的极端情况，多出来的那扇下一次心跳也会被认走，不会丢。
    """
    for hwnd, _cls, _title in _windows_of(TERMINAL_CLASSES):
        if hwnd not in known:
            return hwnd
    return None


def spawn_terminal(workdir, cont=False, prompt=None, settings=None,
                   permission=None, beside=None, profile=None,
                   append_system_prompt=None, resume_id=None, new_session_id=None):
    """在新控制台窗口里跑 claude，返回那个进程对象。

    直接把 argv 列表交给 Popen，不套 cmd /k：cmd 那层的引号规则会把
    "C:\\Program Files\\..." 劈成 C:\\Program（见 agent.agent_command 的说明），
    路径带空格就跑不起来。argv 列表交给 Popen 时 Python 走 CreateProcess
    的多参数通道，没有引号问题。

    beside 是"把窗口摆到启动器旁边"的坐标提示，非 Windows 上要靠终端的
    -geometry 参数实现；这边本来就有一套按句柄挪窗口的办法（bring_next_to），
    不用它。profile 是 agents.Agent 档位；不传走默认（claude）。它带的
    launch_env / config_dir_env 一并注进子进程环境（国际档 codebuddy 靠这个
    分开账号）。

    resume_id / new_session_id 见 agent.agent_args：前者续指定会话，后者给新
    会话预先钉死 id。

    那个进程对象调用方留着轮询 poll()，就知道这个会话还开没开着。

    argv 过一遍 spawn_prefix：npm 装的 claude / codebuddy 落在 PATH 上的是
    .cmd，CreateProcess 拉不起来，会话会当场报错。
    """
    profile = profile or agents.get(agents.DEFAULT_AGENT)
    return subprocess.Popen(
        spawn_prefix(agent_args(
            profile, cont=cont, prompt=prompt, settings=settings,
            permission=permission, append_system_prompt=append_system_prompt,
            resume_id=resume_id, new_session_id=new_session_id)),
        cwd=workdir,
        creationflags=CREATE_NEW_CONSOLE,
        env=profile.spawn_environ(),
    )


def window_alive(handle):
    """那个句柄还作不作数。用户已经关掉那扇窗口的话就是 False。"""
    if not handle:
        return False
    return bool(_user32.IsWindow(handle))


def bring_next_to(owner_window, target, gap=16):
    """把 target 挪到 owner 旁边——优先右边，右边放不下就放左边。

    挪完抬到最前，不然窗口动是动了，还压在启动器底下，用户以为没反应。
    """
    if not _user32.IsWindow(target):
        return False
    rect = _RECT()
    _user32.GetWindowRect(_frame_handle(owner_window), ctypes.byref(rect))
    target_rect = _RECT()
    _user32.GetWindowRect(target, ctypes.byref(target_rect))
    width = target_rect.right - target_rect.left
    height = target_rect.bottom - target_rect.top

    left, top, right, bottom = screen_bounds(owner_window)

    x = rect.right + gap
    if x + width > right:
        x = rect.left - gap - width
    y = rect.top
    # 屏幕装不下就贴着边缘，别把窗口推到看不见的地方去。
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    _user32.SetWindowPos(target, 0, x, y, 0, 0,
                         SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    _user32.SetForegroundWindow(target)
    return True


def focus_window(target):
    """把一扇已经开着的窗口提到前台；最小化过就先还原。

    跟 bring_next_to 的区别：那个会顺手把窗口挪到启动器旁边（"移过来"那个
    动作用的），这个只动焦点，窗口原来摆哪儿还摆哪儿。
    """
    if not target or not _user32.IsWindow(target):
        return False
    if _user32.IsIconic(target):
        _user32.ShowWindow(target, SW_RESTORE)
    _user32.SetForegroundWindow(target)
    return True


def toggle_topmost(target):
    """压顶层开关，返回压完之后的状态：True 是已经置顶。窗口没了返回 None。"""
    if not _user32.IsWindow(target):
        return None
    style = _user32.GetWindowLongW(target, GWL_EXSTYLE)
    pinned = not (style & WS_EX_TOPMOST)
    _user32.SetWindowPos(
        target, HWND_TOPMOST if pinned else HWND_NOTOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    return pinned


def close_window(target):
    """等同点它标题栏的 X：发 WM_CLOSE，让对面自己收尾。"""
    if not _user32.IsWindow(target):
        return False
    _user32.PostMessageW(target, WM_CLOSE, 0, 0)
    return True


def spawn_console(workdir, cont=False, prompt=None, settings=None,
                  permission=None, profile=None, append_system_prompt=None,
                  resume_id=None, new_session_id=None):
    """开一个老式 conhost 跑 claude。

    返回 (进程, 启动前就存在的窗口句柄集合)——窗口是控制台那边异步建的，
    调用方得拿这份旧名单去比对，才知道哪个是新冒出来的。

    环境变量走 set 前缀：cmd /k 那条路没有 env= 可用，profile 带来的覆盖项
    （CODEBUDDY_CONFIG_DIR 之类）拼成 `set "K=V" && ` 打头。引号包着写是
    cmd 认的写法，值里带空格也不会断；这里注入的都是配置家路径和环境名，
    不带 % 和 &，不必再折腾延迟展开。
    """
    profile = profile or agents.get(agents.DEFAULT_AGENT)
    known = {hwnd for hwnd, _ in console_windows()}
    command = "title {} & ".format(title_tag(profile.id))
    for key, value in sorted(profile.launch_overrides().items()):
        command += 'set "{}={}" & '.format(key, value)
    command += agent_command(profile, cont=cont, prompt=prompt,
                             settings=settings, permission=permission,
                             append_system_prompt=append_system_prompt,
                             resume_id=resume_id, new_session_id=new_session_id)
    # 同样得整条给字符串：塞进列表 Python 会重包一遍引号，开场白就断在空格上。
    process = subprocess.Popen(
        "conhost.exe cmd /k " + command,
        cwd=workdir,
        creationflags=CREATE_NEW_CONSOLE,
    )
    return process, known


def fresh_console(known, tag=TITLE_TAG):
    """在 known 之外找新冒出来的控制台窗口；优先认标题打着自己标记的那个。"""
    new = [item for item in console_windows() if item[0] not in known]
    if not new:
        return None
    tagged = [hwnd for hwnd, title in new if tag in title]
    return tagged[0] if tagged else new[0][0]


class EmbeddedConsole:
    """一个贴在启动器窗口上的 conhost。

    是"贴上去"不是"塞进去"：它仍然是一扇独立窗口，只是认启动器当 owner、去掉
    了标题栏、按终端栏那块矩形摆着，看着像长在窗口里。为什么不真塞，见 _attach。
    """

    def __init__(self, process, hwnd, holder):
        self.process = process
        self.hwnd = hwnd
        self.holder = holder
        self._attach()

    def _attach(self):
        """去标题栏、认启动器当 owner，然后贴到终端栏那块矩形上。

        **不能 SetParent 把它变成我们的子窗口**——那样键盘焦点永远拿不到。焦点
        是按线程的输入队列分的，不按窗口层级走：子窗口归 conhost 那条队列，我们
        的线程进不去，给它 SetFocus 一律 ERROR_ACCESS_DENIED(5)，于是打字进不去、
        点了也像没反应（鼠标倒是通的，点到哪儿系统认的就是它）。正规解法是
        AttachThreadInput 把两条队列挂一起，而它对 conhost 挂不上：本机别的 GUI
        进程一挂就成、conhost 的线程 id 也有效（OpenThread 拿得到句柄）、正着挂
        反着挂都回 ERROR_INVALID_PARAMETER(87)，是系统不让挂。

        做成 owner 窗口就没这回事：它自己那条队列自己吃焦点，键鼠全是原生的。
        owner 还是白拿的——跟着本窗口一起最小化、本窗口一销毁它跟着销毁；再加
        一个 WS_EX_TOOLWINDOW，任务栏和 Alt+Tab 里都不露面。
        """
        u = _user32
        style = u.GetWindowLongW(self.hwnd, GWL_STYLE)
        style &= ~(WS_BORDER | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU |
                   WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        u.SetWindowLongW(self.hwnd, GWL_STYLE, style | WS_POPUP | WS_VISIBLE)
        # APPWINDOW 那一位得顺手清掉：留着它，就算认了 owner 也照样占一个任务栏
        # 按钮（那一位的优先级比 TOOLWINDOW 高）。
        u.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                         (u.GetWindowLongW(self.hwnd, GWL_EXSTYLE) |
                          WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        # owner 得是顶层窗口，不能拿控件句柄去顶——用 _frame_handle 问出来的是
        # 带边框的那扇真窗口。
        u.SetWindowLongPtrW(self.hwnd, GWLP_HWNDPARENT,
                            _frame_handle(self.holder.winfo_toplevel()))
        # 窗口早就显出来了、样式又是后改的：藏一下再显，任务栏那个按钮才会真掉。
        u.ShowWindow(self.hwnd, SW_HIDE)
        self.place(SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED |
                   SWP_SHOWWINDOW)

    def place(self, flags=SWP_NOZORDER | SWP_NOACTIVATE):
        """把窗口贴到终端栏那块矩形上，坐标直接取屏幕坐标。

        早先当子窗口时坐标得相对父窗口客户区算（跟顶层窗口的屏幕坐标做差），
        独立窗口不用绕这一道。
        """
        self.holder.update_idletasks()
        _user32.SetWindowPos(
            self.hwnd, 0,
            self.holder.winfo_rootx(), self.holder.winfo_rooty(),
            self.holder.winfo_width(), self.holder.winfo_height(), flags)

    def close(self):
        """等同点它标题栏的 X：发 WM_CLOSE 让 claude 有机会收尾再退。

        只管把话递到，退不退是它的事——调用方自己去 poll 进程，别在这儿等。
        """
        close_window(self.hwnd)

    def detach(self):
        """放出去，变回一个普通的独立窗口；进程一律不动，不关 claude。"""
        u = _user32
        style = u.GetWindowLongW(self.hwnd, GWL_STYLE)
        u.SetWindowLongW(self.hwnd, GWL_STYLE,
                         (style & ~WS_POPUP) | WS_CAPTION | WS_THICKFRAME |
                         WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        u.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                         u.GetWindowLongW(self.hwnd, GWL_EXSTYLE) &
                         ~WS_EX_TOOLWINDOW)
        # owner 得摘干净：挂着的时候它跟着启动器一起最小化、任务栏里也不露面，
        # 放出去了就该是一扇平常的窗口。
        u.SetWindowLongPtrW(self.hwnd, GWLP_HWNDPARENT, None)
        x, y = window_position(self.holder.winfo_toplevel())
        u.SetWindowPos(self.hwnd, 0, x + 48, y + 48, 900, 600,
                       SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED |
                       SWP_SHOWWINDOW)


# ── 窗口位置 ──────────────────────────────────────────────────────────────
# Tk 的 geometry 把 "-N" 读成"距屏幕右边缘 N 像素"，副屏的负坐标会落到主屏上，
# 而且每存读一轮还会漂十几像素，所以位置这块直接问 Win32。


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_uint)]


class _WINDOWPLACEMENT(ctypes.Structure):
    """GetWindowPlacement 的结构。窗口最小化时它记着"还原后会摆在哪"。"""
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("showCmd", ctypes.c_uint), ("ptMinPosition", _POINT),
                ("ptMaxPosition", _POINT), ("rcNormalPosition", _RECT)]


_user32.MonitorFromPoint.argtypes = [_POINT, ctypes.c_uint]
_user32.MonitorFromPoint.restype = ctypes.c_void_p
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p,
                                    ctypes.POINTER(_MONITORINFO)]
_user32.GetWindowPlacement.argtypes = [ctypes.c_void_p,
                                       ctypes.POINTER(_WINDOWPLACEMENT)]
_user32.GetWindowPlacement.restype = ctypes.c_bool
_user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
_user32.GetCursorPos.restype = ctypes.c_bool
_user32.EnumWindows.argtypes = [_WNDENUMPROC, ctypes.c_void_p]
_user32.EnumWindows.restype = ctypes.c_bool
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.IsWindowVisible.restype = ctypes.c_bool
_user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
_user32.GetWindowTextLengthW.restype = ctypes.c_int


def _frame_handle(window):
    """winfo_id 给的是客户区句柄，带边框的顶层窗口是它的父窗口。"""
    return ctypes.windll.user32.GetParent(window.winfo_id())


def window_rect(window):
    """顶层窗口的 (x, y, 宽, 高)。

    最小化的时候 GetWindowRect 给的是 `-32000` 一带的哨兵坐标——照那个存下来，
    下次开窗就在屏幕外了。所以先问 GetWindowPlacement 的 rcNormalPosition：
    那是"还原之后会摆在哪"，不管现在是最小化还是正常显示都对。
    """
    hwnd = _frame_handle(window)
    if _user32.IsIconic(hwnd):
        placement = _WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
        if _user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            rect = placement.rcNormalPosition
            return (rect.left, rect.top,
                    rect.right - rect.left, rect.bottom - rect.top)
    rect = _RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def window_position(window):
    """顶层窗口左上角在桌面上的坐标，副屏上是负的。最小化时给还原后的位置。"""
    x, y, _w, _h = window_rect(window)
    return x, y


def work_area(window, x=None, y=None):
    """离 (x, y) 最近那块屏幕的工作区（去掉任务栏），多屏时坐标可能是负的。

    不用 screen_bounds 的虚拟桌面：那块矩形把任务栏也算进去了，按它夹位置会
    把窗口底边塞到任务栏底下。给了 x/y 就找最近的屏，这样"上次存在副屏、副屏
    已经拔了"也能落到还剩的那块屏上。
    """
    if x is None or y is None:
        x, y = window_position(window)
    point = _POINT(int(x), int(y))
    monitor = _user32.MonitorFromPoint(point, 2)  # MONITOR_DEFAULTTONEAREST
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if monitor and _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        rect = info.rcWork
        return rect.left, rect.top, rect.right, rect.bottom
    return screen_bounds(window)


def place_window(window, x, y):
    ctypes.windll.user32.SetWindowPos(
        _frame_handle(window), 0, x, y, 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def screen_bounds(window):
    """虚拟桌面的边界（左, 上, 右, 下）。多屏拼起来的那一整块，副屏在主屏左边时
    左边界是负的。window 只是为了跟 nixhost 那份对上签名——那边得问 Tk。"""
    left = _user32.GetSystemMetrics(76)
    top = _user32.GetSystemMetrics(77)
    return (left, top, left + _user32.GetSystemMetrics(78),
            top + _user32.GetSystemMetrics(79))


# ── 单实例 ────────────────────────────────────────────────────────────────
# 启动器只允许开一扇：第二次双击不再新开窗口，而是把已有那扇挪到鼠标所在的那块
# 屏幕上、提到前台。认领走命名互斥体——进程被杀系统会自己收回，不会像锁文件那样
# 留下一个谁都不敢动的孤儿。找窗按主窗口标题（startup.LAUNCHER_TITLE）。

_SINGLETON_HANDLES = []


def claim_singleton(name="claude_tool_launcher"):
    """认领这个互斥体名。认到返回 True，被别的实例占着返回 False。

    句柄得攥住到进程结束：放掉（或被 GC 掉）就等于没占过，第二个能再认到。
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    _SINGLETON_HANDLES.append(handle)
    return not already


def find_window(title):
    """按标题找一扇可见的顶层窗口；找不到返回 None。"""
    found = []

    def each(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == title:
            found.append(hwnd)
            return False  # 找到就停，别再扫
        return True

    _user32.EnumWindows(_WNDENUMPROC(each), 0)
    return found[0] if found else None


def cursor_position():
    """鼠标指针在桌面上的坐标；问不到就返回 None。"""
    point = _POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        return None
    return point.x, point.y


def focus_window_at(target, x, y):
    """把 target 挪到 (x, y) 所在那块屏幕的中央，还原后提到前台。

    "挪"是本条的本分：用户在第二块屏幕上双击，多半就是想把窗口叫到眼前；只提
    焦点不动位置的话，窗口可能还缩在另一块屏上，他照样看不到。
    """
    if not target or not _user32.IsWindow(target):
        return False
    rect = _RECT()
    _user32.GetWindowRect(target, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    area = work_area(None, x, y)
    placed = fit_rect(area[0] + (area[2] - area[0] - w) // 2,
                      area[1] + (area[3] - area[1] - h) // 2,
                      w, h, area)
    if _user32.IsIconic(target):
        _user32.ShowWindow(target, SW_RESTORE)
    _user32.SetWindowPos(target, 0, placed.x, placed.y, 0, 0,
                         SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW)
    _user32.SetForegroundWindow(target)
    return True


# ── 交给系统的默认程序 ────────────────────────────────────────────────────
# startfile 就是双击那件事：目录交给资源管理器，网址交给默认浏览器。它在
# Windows 上只认 Unicode 参数，正好——路径里有中文也不会乱码。


def open_path(path):
    """用资源管理器打开一个目录（或文件）。"""
    os.startfile(path)


def open_url(url):
    """用默认浏览器打开一个网址。"""
    os.startfile(url)


# ── 丢回收站 ──────────────────────────────────────────────────────────────
# 系统里没有"移到回收站"这种调用，只有 shell 的"删除、顺便允许撤销"——带
# FOF_ALLOWUNDO 就是进回收站，不带就是永久删（两种都实测过，差的就是这一位）。
# 用的是 shell32 的普通导出，不是 COM，所以不吃这台机器上"裸 ctypes 调 COM
# 一律 0x80040154"那个坑。

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMMKDIR = 0x0200
FOF_NOERRORUI = 0x0400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    # 字段顺序和宽度照 shellapi.h 抄。fFlags 是 WORD 而不是 DWORD，写成 DWORD
    # 后面几个字段全错位——错位了还是照样返回 0，只是删的是别的东西，最难查。
    _fields_ = [("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p)]


def trash_path(path):
    """把 path 丢进回收站，返回成没成。

    这个 API 要的是一串以 NUL 分隔、末尾再来一个 NUL 收尾的路径列表，所以喂
    进去的字符串自己带一个 NUL——ctypes 转 c_wchar_p 时还会再加一个，正好两个。

    盘上没有回收站（网络盘、可移动盘之类）时它会退化成永久删还照样返回 0，
    没法从返回值上看出来。所以调用方那边一旦失败就要如实报出来，别当成删干净了。
    """
    if not path:
        return False
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"
    op.pTo = None
    op.fFlags = (FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI |
                 FOF_NOCONFIRMMKDIR | FOF_ALLOWUNDO)
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0


# ── 绘制零件 ──────────────────────────────────────────────────────────────
