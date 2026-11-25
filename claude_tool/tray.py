"""Windows 系统托盘图标：纯 ctypes 调 shell32 / user32，不引第三方库。

为什么不用 pystray：启动器是免安装、零第三方依赖的 exe，多一个库就多一份要跟着
打包、要在 spec 里交代、升级时还得跟着升的依赖。托盘这一小块只用到
RegisterClass / CreateWindowEx / Shell_NotifyIcon / TrackPopupMenu，自己写反而
干净，也跟 winhost.py 那套 ctypes 做法一致。

消息怎么进 Tk：窗口就建在 Tk 那条线程上，每 50ms 用 PeekMessage 把本窗口的消息
取出来喂给 DispatchMessage，托盘的点击就落到 _wndproc 里。不另起线程——菜单、
Tk 窗口、托盘图标全在一条线程上，不用考虑跨线程同步。

回调（on_show / on_menu / on_command）都在主线程上跑。菜单开着的时候 Tk 的定时
器会停一拍：TrackPopupMenu 自己是个模态循环，这是正常的。
"""
import collections
import ctypes
import sys


SUPPORTED = sys.platform == "win32"

# 窗口消息
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1

# 菜单
MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_DISABLED = 0x0002
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800
TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

# Shell_NotifyIcon
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x01

# LoadImage
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

PM_REMOVE = 0x0001

# 隐藏宿主窗口的类名。第二次启动找它、给它发唤醒消息（见 wake_launcher），
# 不能改——改名等于两个版本之间认不出对方。
TRAY_CLASS = "ClaudeToolTrayHost"
TRAY_TITLE = "Claude 启动器托盘"
WAKE_MESSAGE_NAME = "ClaudeToolShowLauncher"
RESET_MESSAGE_NAME = "ClaudeToolResetLauncherWindow"
TASKBAR_CREATED_NAME = "TaskbarCreated"

WINDOW_ID = 1


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    # lPrivate 在多数示例里不写，但微软现在的 SDK 里确实有；结构体比真实的小
    # 会写坏内存，宁可多留一格。
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint), ("pt", _POINT),
                ("lPrivate", ctypes.c_uint)]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.c_void_p)]


class _NOTIFYICONDATAW(ctypes.Structure):
    # 字段顺序和宽度照 shellapi.h 抄（Vista 之后那一版）。
    _fields_ = [("cbSize", ctypes.c_uint),
                ("hWnd", ctypes.c_void_p),
                ("uID", ctypes.c_uint),
                ("uFlags", ctypes.c_uint),
                ("uCallbackMessage", ctypes.c_uint),
                ("hIcon", ctypes.c_void_p),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", ctypes.c_uint),
                ("dwStateMask", ctypes.c_uint),
                ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", ctypes.c_uint),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.c_uint),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", ctypes.c_void_p)]


if SUPPORTED:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _shell32 = ctypes.windll.shell32

    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
        ctypes.c_size_t, ctypes.c_ssize_t)

    # 句柄是 64 位的指针，不声明 argtypes 的话 ctypes 会按 32 位 int 截断。
    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
    _user32.RegisterClassExW.restype = ctypes.c_ushort
    _user32.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    _user32.UnregisterClassW.restype = ctypes.c_bool
    _user32.CreateWindowExW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _user32.CreateWindowExW.restype = ctypes.c_void_p
    _user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_size_t, ctypes.c_ssize_t]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    _user32.DestroyWindow.restype = ctypes.c_bool
    _user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                     ctypes.c_size_t, ctypes.c_ssize_t]
    _user32.PostMessageW.restype = ctypes.c_bool
    _kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    _kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    _user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
    _user32.RegisterWindowMessageW.restype = ctypes.c_uint
    _user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    _user32.FindWindowW.restype = ctypes.c_void_p
    _user32.PeekMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p,
                                     ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    _user32.PeekMessageW.restype = ctypes.c_bool
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
    _user32.DispatchMessageW.restype = ctypes.c_ssize_t
    _user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                   ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint]
    _user32.LoadImageW.restype = ctypes.c_void_p
    _user32.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _user32.LoadIconW.restype = ctypes.c_void_p
    _user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    _user32.CreatePopupMenu.argtypes = []
    _user32.CreatePopupMenu.restype = ctypes.c_void_p
    _user32.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_size_t, ctypes.c_wchar_p]
    _user32.AppendMenuW.restype = ctypes.c_bool
    _user32.SetMenuDefaultItem.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_uint]
    _user32.TrackPopupMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_void_p, ctypes.c_void_p]
    _user32.TrackPopupMenu.restype = ctypes.c_int
    _user32.DestroyMenu.argtypes = [ctypes.c_void_p]
    _user32.DestroyMenu.restype = ctypes.c_bool
    _user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _user32.SetForegroundWindow.restype = ctypes.c_bool
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
    _user32.GetCursorPos.restype = ctypes.c_bool
    _shell32.Shell_NotifyIconW.argtypes = [ctypes.c_uint,
                                          ctypes.POINTER(_NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = ctypes.c_bool
else:  # pragma: no cover - 非 Windows 上这个模块不会被实例化
    _user32 = _kernel32 = _shell32 = None
    _WNDPROC = None


def item(cmd, text, checked=False, enabled=True, default=False):
    """菜单里的一条命令。cmd 是选中后回给 on_command 的字符串。"""
    return {"kind": "item", "cmd": cmd, "text": text, "checked": checked,
            "enabled": enabled, "default": default}


def separator():
    return {"kind": "sep"}


def submenu(text, children, enabled=True):
    return {"kind": "sub", "text": text, "children": list(children),
            "enabled": enabled}


class TrayIcon:
    """托盘上一个图标 + 一个看不见的宿主窗口。

    icon_file 给 .ico（LoadImage 只认这个，不认 png）。
    on_menu 返回 item()/separator()/submenu() 拼出来的列表；用户点了哪一项，
    就把那项的 cmd 交给 on_command。这些东西都在 Tk 主线程上调用。
    """

    def __init__(self, root, icon_file, tooltip, on_show, on_menu, on_command,
                 on_reset):
        if not SUPPORTED:
            raise RuntimeError("系统托盘只在 Windows 上可用")
        self.root = root
        self.on_show = on_show
        self.on_reset = on_reset
        self.on_menu = on_menu
        self.on_command = on_command
        self.hwnd = None
        self.alive = False
        self._tooltip = tooltip or "Claude 启动器"
        self._hicon = None
        self._own_icon = False
        self._after = None
        self._retry = 0
        self._retry_after = None
        self._destroying = False
        self._nid = None
        self._actions = {}
        self._submenus = []
        # WndProc 里**只记事件、绝不动 Tk**。Tcl 自己的消息泵也会把我们这个隐藏
        # 窗口的消息派发进来，那会儿 _tkinter 正把 GIL 放着；在 WndProc 里回手调
        # Tk（deiconify / after 之类）会撞上 "PyEval_RestoreThread: GIL released"
        # 直接崩进程（探针里踩过）。所以消息先入队，由 _pump 这个正经 Tk 回调
        # 在 DispatchMessage 返回之后统一处理。
        self._events = collections.deque()
        self._proc = _WNDPROC(self._wndproc)

        self._hinst = _kernel32.GetModuleHandleW(None)
        self._taskbar_created = _user32.RegisterWindowMessageW(
            TASKBAR_CREATED_NAME)
        self._wake_message = _user32.RegisterWindowMessageW(WAKE_MESSAGE_NAME)
        self._reset_message = _user32.RegisterWindowMessageW(RESET_MESSAGE_NAME)

        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = self._hinst
        wc.lpszClassName = TRAY_CLASS
        # 注册失败不在这儿报：同一个进程里前一个实例还没 UnregisterClass 的话会
        # 撞上 1410，那不是错，类本来就在。让下面的 CreateWindowEx 来判——类真
        # 没注册上的话它也会失败，那时候再抛才准。
        _user32.RegisterClassExW(ctypes.byref(wc))
        # 没有 WS_VISIBLE：窗口是给 Shell 回调消息用的，不露面也不占任务栏。
        # 但它是顶层窗口（不是 message-only），EnumWindows / FindWindow 找得到——
        # 第二次启动就是靠这个把它叫醒的。
        self.hwnd = _user32.CreateWindowExW(
            0, TRAY_CLASS, TRAY_TITLE, 0, 0, 0, 0, 0,
            None, None, self._hinst, None)
        if not self.hwnd:
            raise OSError("创建托盘宿主窗口失败")

        self._load_icon(icon_file)
        self._add_icon()
        self._schedule()

    # ── 图标 ──

    def _load_icon(self, icon_file):
        if icon_file:
            self._hicon = _user32.LoadImageW(
                None, icon_file, IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE)
            self._own_icon = bool(self._hicon)
        if not self._hicon:
            self._hicon = _user32.LoadIconW(None, IDI_APPLICATION)

    def _add_icon(self):
        """把图标交给任务栏。Explorer 刚起来还没准备好时会失败，隔两秒再试。"""
        if self._destroying:
            return
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = WINDOW_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon
        nid.szTip = self._tooltip[:127]
        if _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self._nid = nid
            self.alive = True
            return
        self._retry += 1
        if self._retry <= 15:
            self._retry_after = self.root.after(2000, self._add_icon)

    def set_tooltip(self, text):
        """更新悬停提示。文字超过 127 个字会被截断（结构体只给这么大）。"""
        self._tooltip = (text or "")[:127]
        if not self.alive or self._nid is None:
            return
        nid = self._nid
        nid.uFlags = NIF_TIP
        nid.szTip = self._tooltip
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def notify(self, title, text):
        """弹一个系统通知气泡（只在真有话要说的时候用，别拿它刷存在感）。"""
        if not self.alive:
            return
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = WINDOW_ID
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = (title or "")[:63]
        nid.szInfo = (text or "")[:255]
        nid.uVersion = 5000
        nid.dwInfoFlags = NIIF_INFO
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # ── 消息 ──

    def _schedule(self):
        if not self._destroying:
            self._after = self.root.after(50, self._pump)

    def _pump(self):
        """把本窗口攒着的消息取干净。Tk 的 after 每 50ms 叫一次。"""
        self._after = None
        if self._destroying or not self.hwnd:
            return
        msg = _MSG()
        while _user32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0,
                                   PM_REMOVE):
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
            if self._destroying:
                return
        while self._events and not self._destroying:
            event = self._events.popleft()
            try:
                if event == "show":
                    self.on_show()
                elif event == "reset":
                    self.on_reset()
                elif event == "menu":
                    self._show_menu()
                elif event == "readd":
                    self.alive = False
                    self._retry = 0
                    self._add_icon()
            except Exception:
                pass
        self._schedule()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """只记事件，不碰 Tk（原因见 __init__ 里 _events 那段）。"""
        try:
            if msg == WM_TRAYICON:
                if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._events.append("show")
                elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._events.append("menu")
                return 0
            if msg == self._wake_message:
                # 第二次启动的人在找我们：把窗口叫回来。
                self._events.append("show")
                return 0
            if self._reset_message and msg == self._reset_message:
                self._events.append("reset")
                return 0
            if msg == self._taskbar_created:
                # Explorer 重启过，图标被系统丢了，重新挂一个。
                self._events.append("readd")
                return 0
            if msg == WM_DESTROY:
                return 0
        except Exception:
            # WndProc 里抛出去会变成 ctypes 的 "Exception in callback" 打印，
            # 而且返回 0 打断系统默认处理。这里咽掉，按默认往下走。
            pass
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ── 菜单 ──

    def _show_menu(self):
        try:
            entries = self.on_menu() or []
        except Exception:
            # 菜单内容算炸了也得留条出路：至少能把窗口叫回来、能退出。
            entries = [item("show", "打开启动器", default=True),
                       separator(), item("quit", "退出")]
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        self._actions = {}
        self._submenus = []
        self._next_id = 0
        try:
            self._fill(menu, entries)
            # 不先抢一次前台，点菜单外面时菜单不会关（MSDN 里写明的老毛病）。
            _user32.SetForegroundWindow(self.hwnd)
            point = _POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            chosen = _user32.TrackPopupMenu(
                menu, TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD |
                TPM_NONOTIFY, point.x, point.y, 0, self.hwnd, None)
            _user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            cmd = self._actions.get(chosen)
            if cmd is not None and not self._destroying:
                self.on_command(cmd)
        finally:
            for sub in self._submenus:
                _user32.DestroyMenu(sub)
            _user32.DestroyMenu(menu)

    def _fill(self, menu, entries):
        for entry in entries:
            # 菜单文本里的 & 是助记符前缀（显示成下划线），名字里带 & 的工作区
            # 会少一个字符。想显示成字面的 & 得写两个。
            text = (entry.get("text") or "").replace("&", "&&")
            kind = entry.get("kind")
            if kind == "sep":
                _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            elif kind == "sub":
                sub = _user32.CreatePopupMenu()
                if not sub:
                    continue
                self._submenus.append(sub)
                self._fill(sub, entry.get("children") or [])
                flags = MF_POPUP
                if not entry.get("enabled", True):
                    flags |= MF_GRAYED | MF_DISABLED
                _user32.AppendMenuW(menu, flags, ctypes.c_size_t(sub), text)
            else:
                self._next_id = getattr(self, "_next_id", 0) + 1
                menu_id = self._next_id
                flags = MF_STRING
                if not entry.get("enabled", True):
                    flags |= MF_GRAYED | MF_DISABLED
                if entry.get("checked"):
                    flags |= MF_CHECKED
                _user32.AppendMenuW(menu, flags, menu_id, text)
                self._actions[menu_id] = entry.get("cmd")
                if entry.get("default"):
                    _user32.SetMenuDefaultItem(menu, menu_id, 0)

    # ── 收尾 ──

    def destroy(self):
        """摘掉图标、销毁宿主窗口。进程退出前一定要走一遍，否则图标会留到
        下一次鼠标扫过托盘才消失（看着像没死干净）。"""
        if self._destroying:
            return
        self._destroying = True
        for handle in (self._after, self._retry_after):
            if handle is not None:
                try:
                    self.root.after_cancel(handle)
                except Exception:
                    pass
        self._after = self._retry_after = None
        if self.alive:
            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = WINDOW_ID
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.alive = False
        if self.hwnd:
            _user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        try:
            _user32.UnregisterClassW(TRAY_CLASS, self._hinst)
        except Exception:
            pass
        if self._hicon and self._own_icon:
            _user32.DestroyIcon(self._hicon)
        self._hicon = None


def wake_launcher(reset_window=False):
    """通过托盘宿主叫回启动器，或通知它重置主窗的位置和尺寸。

    不论主窗是否可见，托盘宿主窗口都可以按类名找到。返回 False 表示消息没发出，
    例如宿主还没建完，调用方自己决定等不等。
    """
    if not SUPPORTED:
        return False
    hwnd = _user32.FindWindowW(TRAY_CLASS, None)
    if not hwnd:
        return False
    name = RESET_MESSAGE_NAME if reset_window else WAKE_MESSAGE_NAME
    message = _user32.RegisterWindowMessageW(name)
    return bool(message and _user32.PostMessageW(hwnd, message, 0, 0))
