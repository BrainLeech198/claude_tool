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
WS_CHILD, WS_VISIBLE, WS_POPUP = 0x40000000, 0x10000000, 0x80000000
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
CONSOLE_CLASS = "ConsoleWindowClass"
TITLE_TAG = "claude-embed"

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

# 句柄是 64 位的指针，不声明 argtypes 的话 ctypes 会按 32 位 int 截断。
_user32 = ctypes.windll.user32
_user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def console_windows():
    """当前桌面上所有老式控制台窗口的 (句柄, 标题)。"""
    found = []

    def visit(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(hwnd, name, 64)
        if name.value == CONSOLE_CLASS:
            title = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
            found.append((hwnd, title.value))
        return True

    ctypes.windll.user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def spawn_console(workdir, cont=False, prompt=None, settings=None, permission=None):
    """开一个老式 conhost 跑 claude。

    返回 (进程, 启动前就存在的窗口句柄集合)——窗口是控制台那边异步建的，
    调用方得拿这份旧名单去比对，才知道哪个是新冒出来的。
    """
    known = {hwnd for hwnd, _ in console_windows()}
    command = "title {} & {}".format(
        TITLE_TAG, claude_command(cont, prompt, settings, permission))
    process = subprocess.Popen(
        ["conhost.exe", "cmd", "/k", command],
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

SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010


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
