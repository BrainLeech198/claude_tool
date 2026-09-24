"""内嵌那个 conhost 现在贴得住吗——owner、样式、矩形、跟着挪。

按 _probe_embed_focus.py 的老套路：假 conhost 冒充 claude，启动器关在沙箱里，
全程只读地拿 Win32 问系统，**不点鼠标、不合成键鼠、不碰用户桌面**。

    python -X utf8 _probe_embed_glue.py
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import tempfile
import time


def _reconfigure():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_reconfigure()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _probe_common import rebind                       # noqa: E402

from claude_tool import claude as C  # noqa: E402
from claude_tool import winhost as W  # noqa: E402
from claude_tool.ui import launcher as L  # noqa: E402

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u.GetWindow.restype = ctypes.c_void_p
u.GetParent.argtypes = [ctypes.c_void_p]
u.GetParent.restype = ctypes.c_void_p
u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u.GetWindowLongW.restype = ctypes.c_long
u.WindowFromPoint.argtypes = [wt.POINT]
u.WindowFromPoint.restype = ctypes.c_void_p
u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u.GetAncestor.restype = ctypes.c_void_p

GW_OWNER = 4
GWLP_HWNDPARENT = -8
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_CHILD, WS_POPUP = 0x00C00000, 0x40000000, 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

FAILED = []


def check(label, got, want=True):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("  [{}] {}: {!r}（期望 {!r}）".format(
        "OK" if ok else "!!", label, got, want), flush=True)
    return ok


def cls(hwnd):
    if not hwnd:
        return "(空)"
    buf = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(ctypes.c_void_p(hwnd), buf, 128)
    return buf.value


def title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256)
    return buf.value


def rect_of(hwnd):
    r = wt.RECT()
    u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def holder_rect(app):
    h = app.term_holder
    return (h.winfo_rootx(), h.winfo_rooty(),
            h.winfo_rootx() + h.winfo_width(),
            h.winfo_rooty() + h.winfo_height())


def sweep_fakes():
    """收掉上一趟留下的假控制台。只认标题里同时带 ping 和 127.0.0.1 的，
    绝不按 TITLE_TAG 捞（真内嵌会话的标题也带那个标记）。"""
    from claude_tool.host import close_window
    n = 0
    for hwnd, text in W.console_windows():
        if "ping" in text and "127.0.0.1" in text:
            try:
                close_window(hwnd)
                n += 1
            except Exception:
                pass
    return n


n = sweep_fakes()
if n:
    print("（先收掉上一趟留下的 {} 个假控制台）".format(n), flush=True)
    time.sleep(1.5)

SANDBOX = tempfile.mkdtemp(prefix="embedglue_")
print("沙箱：", SANDBOX, flush=True)
os.environ["USERPROFILE"] = SANDBOX
os.environ["HOME"] = SANDBOX


def fake_spawn(workdir, cont=False, prompt=None, settings=None, permission=None):
    known = {hwnd for hwnd, _ in W.console_windows()}
    cmd = "title {} & echo fakeprocess & ping -n 600 127.0.0.1 > nul".format(
        W.TITLE_TAG)
    proc = subprocess.Popen(["conhost.exe", "cmd", "/k", cmd], cwd=workdir,
                            creationflags=C.CREATE_NEW_CONSOLE)
    return proc, known


rebind("spawn_console", fake_spawn)
app = L.Launcher()
app.update()

print()
print("== 嵌一个假的进来 ==")
item = app.config_data["workspaces"][0]
app.embed_workspace(item)
end = time.time() + 10
while app.embedded is None and time.time() < end:
    app.update()
    time.sleep(0.02)
if app.embedded is None:
    print("  嵌不进去，反馈栏说：", app.feedback_var.get())
    app.destroy()
    sys.exit(1)

hwnd = app.embedded.hwnd
root_hwnd = app.winfo_id()
frame_hwnd = u.GetParent(ctypes.c_void_p(root_hwnd))
app.update()
print("  控制台 :", hex(hwnd), title(hwnd)[:40], "[" + cls(hwnd) + "]")
print("  顶栏句柄 :", hex(frame_hwnd), "[" + cls(frame_hwnd) + "]")

print()
print("== 1. 它现在是独立窗口，不是子窗口 ==")
# 注意 GetParent 在这儿答的是 owner：对 WS_POPUP 的顶层窗口，GetParent 和
# GetWindow(GW_OWNER) 是同一件事。是不是子窗口只能看 WS_CHILD 那一位。
check("GetParent 不是 Tk 客户区（没被当子窗口）",
      u.GetParent(ctypes.c_void_p(hwnd)) == root_hwnd, False)
check("owner 就是启动器那扇真窗口",
      u.GetWindow(ctypes.c_void_p(hwnd), GW_OWNER), frame_hwnd)
style = u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_STYLE)
ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
check("WS_CHILD 没置上", bool(style & WS_CHILD), False)
check("WS_POPUP 置上了", bool(style & WS_POPUP), True)
check("标题栏没掉了", bool(style & WS_CAPTION), False)
check("WS_EX_TOOLWINDOW 置上了（任务栏/Alt+Tab 里不出现）",
      bool(ex & WS_EX_TOOLWINDOW), True)
check("WS_EX_APPWINDOW 清掉了（留着它就还是占任务栏）",
      bool(ex & WS_EX_APPWINDOW), False)
# 这条是"能不能拿到焦点"最接近的自动检查：带 WS_EX_NOACTIVATE 的窗口点它永远
# 不激活、也就永远没焦点。真去点一下没法试（那要动用户的桌面），所以守这一条。
check("带没带 WS_EX_NOACTIVATE（带了就永远激活不了）",
      bool(ex & WS_EX_NOACTIVATE), False)

print()
print("== 2. 贴得严不严 ==")
hr = holder_rect(app)
check("控制台矩形 == 终端栏矩形", rect_of(hwnd), hr)
cx, cy = (hr[0] + hr[2]) // 2, (hr[1] + hr[3]) // 2
hit = u.WindowFromPoint(wt.POINT(cx, cy))
top = u.GetAncestor(ctypes.c_void_p(hit), 2) if hit else 0
if hit == hwnd:
    print("  [OK] 那个点上系统认的是控制台", flush=True)
elif top and top not in (frame_hwnd, hwnd):
    # 上面压着别人的窗口（跑着全屏游戏、或者用户把别的窗口挪过来了），命中的
    # 自然是人家——这跟贴没贴住无关，跳过，别拿它当失败。
    print("  [--] 上面压着别的窗口，命中是它：{} {} [{}]，这条跳过".format(
        hex(hit), repr(title(hit)[:30]), cls(hit)), flush=True)
else:
    check("那个点上系统认的是控制台", hit, hwnd)
    print("     那儿其实是 :", hex(hit), repr(title(hit)[:40]),
          "[" + cls(hit) + "]", rect_of(hit))

print()
print("== 3. 挪窗口：终端栏的 <Configure> 发不发，贴得住吗 ==")
moved = []
app.term_holder.bind("<Configure>", lambda e: moved.append(1), add="+")
x0, y0 = W.window_position(app)
W.place_window(app, x0 + 140, y0 + 90)
app.update()
time.sleep(0.25)
app.update()
print("  挪窗口期间 holder 收到的 Configure 次数 :", len(moved))
hr2 = holder_rect(app)
check("挪完之后终端栏确实挪了", hr2[0], hr[0] + 140)
check("控制台跟着挪了", rect_of(hwnd), hr2)

print()
print("== 4. 缩窗口 ==")
app.geometry("{}x{}".format(app.winfo_width() + 120, app.winfo_height() + 80))
app.update()
time.sleep(0.25)
app.update()
check("缩完之后还贴得住", rect_of(hwnd), holder_rect(app))

print()
print("== 5. 「放到独立窗口」要把 owner 摘干净 ==")
app.detach_terminal()
app.update()
time.sleep(0.3)
app.update()
style = u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_STYLE)
ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
check("窗口还活着", bool(u.IsWindow(ctypes.c_void_p(hwnd))))
check("标题栏回来了", bool(style & WS_CAPTION), True)
# ctypes 的 c_void_p 把 NULL 交回来是 None，不是 0。
check("owner 摘掉了", bool(u.GetWindow(ctypes.c_void_p(hwnd), GW_OWNER)), False)
check("WS_EX_TOOLWINDOW 掉了", bool(ex & WS_EX_TOOLWINDOW), False)
check("启动器这边已经放手（进程没被关）", app.embedded, None)

app.destroy()
from claude_tool.host import close_window  # noqa: E402
try:
    close_window(hwnd)
except Exception:
    pass
time.sleep(1.0)
shutil.rmtree(SANDBOX, ignore_errors=True)
print()
print("剩下的假控制台：", sweep_fakes())
print()
print("失败项：", FAILED if FAILED else "没有")
