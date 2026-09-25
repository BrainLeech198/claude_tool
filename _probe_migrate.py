"""换模型时那条流水线：关旧窗口 → 用新模型接着上次的上下文重开，带进度区。

流水线本身不再写交接文档了（接着聊靠 claude 自己的 --continue），那份文档只剩
手动那颗按钮那条路，两件事分开验。

claude 那个进程换成了假的（一个写 handoff.md 就退的 .bat），窗口换成了
`cmd /k ping`——验的是启动器这边的编排，不是 claude 本身。独立窗口和内嵌两条
路各跑一遍。

    python _probe_migrate.py [出图.png]
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from _probe_common import rebind, sandbox             # noqa: E402

PROFILE = sandbox("migrate")
WORKSPACE = os.path.join(sandbox("migrate"), "工作区甲")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_migrate_shot.png")
CREATE_NEW_CONSOLE = 0x00000010

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

from claude_tool import winhost as W              # noqa: E402
from claude_tool.ui import launcher as L          # noqa: E402
from claude_tool.paths import PRESET_DIR              # noqa: E402
from claude_tool.presets import preset_path           # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

PW_RENDERFULLCONTENT = 0x00000002


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# 句柄在 64 位上装不进默认的 c_int，不声明的话 SetWindowPos / PrintWindow 拿到的是
# 被截断的半个句柄——截在哪儿就看运气了。
user32.GetParent.restype = ctypes.c_void_p
user32.GetParent.argtypes = [ctypes.c_void_p]
user32.GetWindowDC.restype = ctypes.c_void_p
user32.GetWindowDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


def capture(hwnd, width, height):
    """PrintWindow 抓窗口自己的绘制内容，不碰桌面、不抢前台（见 _probe_ui_shot）。

    别换回 SetForegroundWindow + ImageGrab 那套：前台锁会直接拒掉
    SetForegroundWindow，抓到的就成了当时真正盖在上面别的东西。
    """
    from PIL import Image
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem, bitmap)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height          # 负 = 自上而下，省得再翻一遍
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0           # BI_RGB
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(info), 0)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return Image.frombuffer("RGB", (width, height), buf.raw, "raw", "BGRX", 0, 1)


OK = [True]


def check(label, passed, detail=""):
    OK[0] = OK[0] and passed
    print("{} {} {}".format("OK  " if passed else "FAIL", label, detail))


FAKE = os.path.join(PROFILE, "fake_claude.bat")

# 探针放出去的每个假窗口都记一笔。收尾时按进程树杀，不能只杀那个 cmd.exe：
# 挂在它底下的 ping 还在跑，终端窗口散不掉，那个窗口压着的 cwd 就成了"设备
# 或资源忙"，整个沙箱目录删不了。
SPAWNED = []


def _track(proc):
    SPAWNED.append(proc)
    return proc


def sweep_spawned():
    """把探针开过的假窗口连子进程一起收掉，返回收掉几个。"""
    killed = 0
    for proc in SPAWNED:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            killed += 1
    return killed


def fake_launch(workdir, cont=False, prompt=None, settings=None, permission=None,
                beside=None):
    """替掉 winhost.spawn_terminal：开一扇真终端窗口，进程能 poll，句柄能被认出来。"""
    return _track(subprocess.Popen(
        ["cmd", "/k", "title claude-probe-mig & ping -n 600 127.0.0.1 > nul"],
        cwd=workdir, creationflags=CREATE_NEW_CONSOLE))


def fake_spawn(workdir, cont=False, prompt=None, settings=None, permission=None):
    """替掉 winhost.spawn_console：内嵌那条路要的是 conhost 的窗口。"""
    known = {hwnd for hwnd, _ in W.console_windows()}
    proc = _track(subprocess.Popen(
        ["conhost.exe", "cmd", "/k",
         "title {} & ping -n 600 127.0.0.1 > nul".format(W.TITLE_TAG)],
        cwd=workdir, creationflags=CREATE_NEW_CONSOLE))
    return proc, known


def setup():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(WORKSPACE, exist_ok=True)
    # switch_model 是往 ~/.claude/settings.json 覆盖一份，那个目录得先在
    os.makedirs(os.path.join(PROFILE, ".claude"), exist_ok=True)
    with open(FAKE, "w", encoding="ascii") as f:
        f.write("@echo off\r\necho fake handoff for the probe > handoff.md\r\n")
    os.makedirs(PRESET_DIR, exist_ok=True)
    with open(preset_path("探针模型"), "w", encoding="utf-8") as f:
        f.write('{"env": {"ANTHROPIC_BASE_URL": "https://example.invalid",'
                ' "ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_MODEL": "probe"}}')
    rebind("claude_exe", lambda: FAKE)
    rebind("spawn_terminal", fake_launch)
    rebind("spawn_console", fake_spawn)
    L.messagebox.showerror = lambda title, msg, **k: print("  [错误框]", title, msg)
    return preset_path("探针模型")


class Drive:
    def __init__(self, app):
        self.app = app

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.02)

    def wait(self, predicate, limit=40):
        end = time.time() + limit
        while time.time() < end:
            if predicate():
                return True
            self.pump(0.25)
        return False

    def grab(self, path):
        app = self.app
        frame = user32.GetParent(app.winfo_id())
        user32.SetWindowPos(frame, -1, 40, 40, 0, 0, 0x0001 | 0x0002)
        self.pump(0.8)
        r = wintypes.RECT()
        user32.GetWindowRect(frame, ctypes.byref(r))
        image = capture(frame, r.right - r.left, r.bottom - r.top)
        if image is None:
            print("  !! PrintWindow 失败，这次没出图")
        else:
            image.save(path)
            print("  saved", path)
        user32.SetWindowPos(frame, -2, 0, 0, 0, 0, 0x0001 | 0x0002)
        self.pump(0.4)



def manual_handoff(app, drive, sess):
    """手动点「整理交接文档」：也该走那块进度区，写完收回去。"""
    target = os.path.join(WORKSPACE, "handoff.md")
    if os.path.isfile(target):
        os.remove(target)
    app.write_handoff(sess)
    # 假 claude 秒退，_poll_handoff 一醒就把进度区收回去了，所以趁热看
    check("手动写文档时进度区摆出来了", app._task_visible)
    check("进度区标题不是「正在换模型」", app.task_head_var.get() != "正在换模型",
          app.task_head_var.get())
    check("写着要生成哪个文件", "handoff.md" in app.task_step_var.get(),
          app.task_step_var.get())
    got = drive.wait(lambda: os.path.isfile(target), limit=20)
    check("文档写出来了", got)
    drive.wait(lambda: not app._task_visible, limit=10)
    check("写完之后进度区收回去了", not app._task_visible)
    return os.path.getmtime(target) if got else 0.0


def scenario(app, drive, embed, preset, tag):
    print("--- {} ---".format(tag))
    app.embed_var.set(embed)
    sess = app.config_data["workspaces"][0]
    app.launch_workspace(sess)
    drive.pump(3.0)
    check("开出了一个会话", len(app.running) == 1, "{}".format(len(app.running)))
    old = app.running[0]
    old_proc = old["proc"]
    old_hwnd = old.get("hwnd")
    if embed:
        check("是内嵌的", app.embedded is not None
              and app.embedded.process is old_proc)
    else:
        check("拿到了独立窗口句柄", bool(old_hwnd), "hwnd={}".format(old_hwnd))

    before = manual_handoff(app, drive, old)

    asked = []
    L.messagebox.askyesno = lambda title, msg, **k: asked.append(msg) or True
    time.sleep(1.1)
    app.switch_model("探针模型", preset)

    check("换完弹出询问", len(asked) == 1, "{} 个".format(len(asked)))
    check("问的话里说清了会关掉旧会话", "关掉它" in asked[0], asked[0].splitlines()[0])
    check("进度区摆出来了", app._task_visible)
    check("标题是「正在换模型」", app.task_head_var.get() == "正在换模型",
          app.task_head_var.get())
    check("带上了第几个", app.task_count_var.get() == "第 1/1 个",
          app.task_count_var.get())
    # 0.4：原来是跟「模型区」比高低的，可模型区搬进了设置窗——那是另一个 Toplevel，
    # 两边没法比。改成跟正文那栏比：进度区在正文上面，这条要盯的事没变。
    # 用 winfo_rooty 而不是 winfo_y：两者现在挂在不同父控件下，y 的基准不一样。
    check("进度区在正文上面",
          app.task_frame.winfo_rooty() < app.detail_area.winfo_rooty(),
          "task y={} 正文 y={}".format(app.task_frame.winfo_rooty(),
                                       app.detail_area.winfo_rooty()))

    # 流水线跑着的时候，手动点「整理交接文档」得被打回去：两边会抢同一块进度区。
    app.write_handoff(old)
    check("流水线跑着时手动按钮被打回", "手上有活" in app.feedback_var.get(),
          app.feedback_var.get())

    # 假 claude 是秒退的，几步之间的间隔只有那几百毫秒的 after，具体停在哪一步
    # 取决于这一眼看得多快，所以只认"落在流水线的某一步上"。
    STEPS = ("先把", "旧的关掉了", "等旧窗口关掉", "等旧的那个会话退出去",
             "用新模型重开")
    drive.pump(1.0)
    step = app.task_step_var.get()
    check("步骤行跟着流水线走", any(s in step for s in STEPS), step)
    check("秒数在往上加", "已用" in step and step.rstrip().endswith("秒"), step)
    drive.grab(OUT if not embed else os.path.join(ROOT, "_migrate_embed_shot.png"))

    done = drive.wait(lambda: not app._task_running and not app._task_visible, limit=60)
    check("流水线跑完了", done)
    # 换模型这条路不再碰交接文档：上面手动写出来的那份 mtime 得原封不动。
    check("流水线没去动那份文档",
          os.path.getmtime(os.path.join(WORKSPACE, "handoff.md")) == before)
    if embed:
        check("旧的 conhost 关了", old_proc.poll() is not None)
        check("换成了新的内嵌会话", app.embedded is not None
              and app.embedded.process is not old_proc)
    else:
        check("旧的窗口关了", not user32.IsWindow(old_hwnd))
        check("旧的进程退了", old_proc.poll() is not None)

    drive.pump(3.0)
    # 不查"旧的那一行没了"：同一路径重开时 track_running 是就地改那一行，那个条目
    # 本来就会被复用掉；真正要查的是名单里没多出东西、而且指着的是新进程。
    check("名单里就一行、指着新进程",
          len(app.running) == 1 and app.running[0]["path"] == WORKSPACE
          and app.running[0]["proc"] is not old_proc,
          "{}".format([i["name"] for i in app.running]))
    print("  状态行:", app.feedback_var.get())
    return old_proc


def main():
    preset = setup()
    app = L.Launcher()
    app.config_data["workspaces"] = [
        {"name": "甲", "path": WORKSPACE, "permission": "acceptEdits"}]
    app.config_data["workplace"] = WORKSPACE
    app.refresh_workspaces()
    app.geometry("760x880+40+40")
    app.update()
    drive = Drive(app)

    old_a = scenario(app, drive, False, preset, "独立窗口")
    old_b = scenario(app, drive, True, preset, "内嵌终端")

    # 收尾：别把探针开出来的窗口留在用户桌面上
    for proc in (old_a, old_b):
        if proc.poll() is None:
            proc.kill()
    if app.embedded is not None:
        app.embedded.close()
    for item in app.running:
        hwnd = item.get("hwnd")
        if hwnd and user32.IsWindow(hwnd):
            W.close_window(hwnd)
    drive.pump(2.0)
    app.destroy()
    print("结果:", "全过" if OK[0] else "有失败")
    return 0 if OK[0] else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        # main 半路炸了也得收，不然那些假窗口就留在桌面上了。
        left = sweep_spawned()
        if left:
            print("  收掉了 {} 个漏下的假窗口".format(left))
    sys.exit(code)
