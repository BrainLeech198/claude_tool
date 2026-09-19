"""Windows 窗口层面的操作：找控制台窗口、把它塞进我们的窗口里、摆位置。

Windows Terminal 是 WinUI3 应用，窗口没法当子窗口塞进别的窗口，所以内嵌这
条路只能拉老 conhost.exe。代价：conhost 没有字体回退（它默认用新宋体），
claude 界面里少数符号会显示成方框，而 WT 里不会。
"""
import ctypes
import subprocess

from claude_tool.claude import CREATE_NEW_CONSOLE, claude_command


# ── 内嵌终端 ──────────────────────────────────────────────────────────────
# Windows Terminal 是 WinUI3 应用，窗口没法当子窗口塞进别的窗口，所以内嵌这
# 条路只能拉老 conhost.exe。代价：conhost 没有字体回退（它默认用新宋体），
# claude 界面里少数符号会显示成方框，而 WT 里不会。

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WM_CLOSE = 0x0010
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
WS_CHILD, WS_VISIBLE, WS_POPUP = 0x40000000, 0x10000000, 0x80000000
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
CONSOLE_CLASS = "ConsoleWindowClass"
# 默认终端是 Windows Terminal 的时候，cmd /k 开出来的是这个类名的窗口，
# 上面那个 ConsoleWindowClass 一个都找不到。两个都得认。
TERMINAL_CLASSES = (CONSOLE_CLASS, "CASCADIA_HOSTING_WINDOW_CLASS")
TITLE_TAG = "claude-embed"

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

# 句柄是 64 位的指针，不声明 argtypes 的话 ctypes 会按 32 位 int 截断。
_user32 = ctypes.windll.user32
_user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
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

    left = _user32.GetSystemMetrics(76)
    right = left + _user32.GetSystemMetrics(78)
    top = _user32.GetSystemMetrics(77)
    bottom = top + _user32.GetSystemMetrics(79)

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


def spawn_console(workdir, cont=False, prompt=None, settings=None, permission=None):
    """开一个老式 conhost 跑 claude。

    返回 (进程, 启动前就存在的窗口句柄集合)——窗口是控制台那边异步建的，
    调用方得拿这份旧名单去比对，才知道哪个是新冒出来的。
    """
    known = {hwnd for hwnd, _ in console_windows()}
    command = "title {} & {}".format(
        TITLE_TAG, claude_command(cont, prompt, settings, permission))
    # 同样得整条给字符串：塞进列表 Python 会重包一遍引号，开场白就断在空格上。
    process = subprocess.Popen(
        "conhost.exe cmd /k " + command,
        cwd=workdir,
        creationflags=CREATE_NEW_CONSOLE,
    )
    return process, known


def fresh_console(known):
    """在 known 之外找新冒出来的控制台窗口；优先认标题打着自己标记的那个。"""
    new = [item for item in console_windows() if item[0] not in known]
    if not new:
        return None
    tagged = [hwnd for hwnd, title in new if TITLE_TAG in title]
    return tagged[0] if tagged else new[0][0]


class EmbeddedConsole:
    """一个被塞进启动器窗口里的 conhost。"""

    def __init__(self, process, hwnd, holder):
        self.process = process
        self.hwnd = hwnd
        self.holder = holder
        self._attach()

    def _attach(self):
        u = ctypes.windll.user32
        root = self.holder.winfo_toplevel()
        u.SetParent(self.hwnd, root.winfo_id())
        style = u.GetWindowLongW(self.hwnd, GWL_STYLE)
        style &= ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU |
                   WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        u.SetWindowLongW(self.hwnd, GWL_STYLE, style | WS_CHILD | WS_VISIBLE)
        self.place()

    def place(self):
        """子窗口的坐标是相对父窗口客户区的，所以拿 Tk 的屏幕坐标做差。"""
        self.holder.update_idletasks()
        root = self.holder.winfo_toplevel()
        ctypes.windll.user32.SetWindowPos(
            self.hwnd, 0,
            self.holder.winfo_rootx() - root.winfo_rootx(),
            self.holder.winfo_rooty() - root.winfo_rooty(),
            self.holder.winfo_width(), self.holder.winfo_height(),
            SWP_NOZORDER | SWP_NOACTIVATE)

    def close(self):
        """等同点它标题栏的 X：发 WM_CLOSE 让 claude 有机会收尾再退。

        只管把话递到，退不退是它的事——调用方自己去 poll 进程，别在这儿等。
        """
        close_window(self.hwnd)

    def detach(self):
        """放出去，变回一个普通的独立窗口；进程一律不动，不关 claude。"""
        u = ctypes.windll.user32
        style = u.GetWindowLongW(self.hwnd, GWL_STYLE)
        u.SetWindowLongW(self.hwnd, GWL_STYLE,
                         (style & ~WS_CHILD) | WS_CAPTION | WS_THICKFRAME |
                         WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        u.SetParent(self.hwnd, 0)
        root = self.holder.winfo_toplevel()
        x, y = window_position(root)
        u.SetWindowPos(self.hwnd, 0, x + 48, y + 48, 900, 600,
                       SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED |
                       SWP_SHOWWINDOW)


# ── 窗口位置 ──────────────────────────────────────────────────────────────
# Tk 的 geometry 把 "-N" 读成"距屏幕右边缘 N 像素"，副屏的负坐标会落到主屏上，
# 而且每存读一轮还会漂十几像素，所以位置这块直接问 Win32。


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _frame_handle(window):
    """winfo_id 给的是客户区句柄，带边框的顶层窗口是它的父窗口。"""
    return ctypes.windll.user32.GetParent(window.winfo_id())


def window_position(window):
    """顶层窗口左上角在桌面上的坐标，副屏上是负的。"""
    rect = _RECT()
    ctypes.windll.user32.GetWindowRect(_frame_handle(window), ctypes.byref(rect))
    return rect.left, rect.top


def place_window(window, x, y):
    ctypes.windll.user32.SetWindowPos(
        _frame_handle(window), 0, x, y, 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


# ── 绘制零件 ──────────────────────────────────────────────────────────────
