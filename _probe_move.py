"""界面这一层的搬迁：按钮、对话框、进度区、改配置、丢回收站。

mover 那一层已经在 _probe_mover.py 里单独验过了，这里验的是接线：该拦的拦没
拦住（会话开着、交接文档正写着、目标已存在），该走的走没走通（进度区摆出来、
复制完改配置、旧的真进了回收站），以及几条容易写错的边角——名字跟不跟着改、
配置写不成时旧的东西动不动。

    python _probe_move.py [出图.png]
"""
import ctypes
import os
import shutil
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("move")
WORKSPACE = os.path.join(sandbox("move"), "工作区甲")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_move_shot.png")

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

from claude_tool import config as C               # noqa: E402
from claude_tool import mover                     # noqa: E402
from claude_tool.ui import launcher as L          # noqa: E402
from _probe_common import rebind  # noqa: E402

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


bad = []
MESSAGES = []


def check(label, ok, detail=""):
    print("  {} {}{}".format("ok  " if ok else "FAIL", label,
                             "  " + str(detail) if detail else ""))
    if not ok:
        bad.append(label)


def detail_actions(app):
    """右栏那两排按钮的字。

    0.4 起「改名/搬迁/↑↓/移除」不在工作区行上了，搬到右栏（见 ui/detail.py）：
    上排日常（新会话/接着上次/打开目录）、下排管理（改名/搬迁/↑/↓/移除）。
    第 6 节原来数的是行上的动作，现在改数右栏的——要盯的事没变。

    判"摆没摆"用 winfo_manager()：离屏跑的时候 winfo_ismapped() 对一个明明
    pack 着的按钮也会报 0（Task 8 实测），当判据会得出反的结论。
    """
    labels = [b._text for b in app._detail_daily + app._detail_manage]
    if app._handoff_btn.winfo_manager() == "pack":
        labels.append("删交接文档")
    return labels


def bin_entries():
    """各盘的回收站里都有哪些条目，返回 {(盘符, 名字)}；一个都读不到就是 None。

    得挨个盘看：删下去的东西进的是它自己那块盘的回收站——沙箱搁在 D: 上，
    条目就落进 D:\\$Recycle.Bin，只盯着系统盘（早先按 tempdir 推出 C:）会
    一直数不出变化，看着像"没进回收站"。
    """
    found = set()
    for letter in "CDEFGH":
        root = letter + ":\\$Recycle.Bin"
        try:
            sids = os.listdir(root)
        except OSError:
            continue
        for sid in sids:
            try:
                names = os.listdir(os.path.join(root, sid))
            except OSError:
                continue
            for name in names:
                found.add((letter, name))
    return found or None


FILES = {"readme.md": 40, "src/main.py": 900, "src/深一层/数据.bin": 51200}


def make_tree(root, files):
    for rel, size in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"x" * size)


def make_workspace(path, name="甲"):
    make_tree(path, FILES)
    project = C.project_dir(path)
    os.makedirs(os.path.join(project, "memory"), exist_ok=True)
    with open(os.path.join(project, "sess-001.jsonl"), "w",
              encoding="utf-8") as f:
        f.write('{"聊过": 1}\n')
    with open(os.path.join(project, "memory", "记着的.md"), "w",
              encoding="utf-8") as f:
        f.write("记住")
    return {"name": name, "path": path, "permission": "acceptEdits"}


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

    def grab(self, path, window=None):
        app = window or self.app
        frame = user32.GetParent(app.winfo_id())
        user32.SetWindowPos(frame, -1, 30, 30, 0, 0, 0x0001 | 0x0002)
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
        self.pump(0.3)


def setup_app():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(WORKSPACE, exist_ok=True)
    os.makedirs(os.path.join(PROFILE, ".claude"), exist_ok=True)
    app = L.Launcher()
    app.update_idletasks()
    return app


def record(title, msg, **kwargs):
    MESSAGES.append((title, msg))
    print("  [框] {} / {}".format(title, msg.splitlines()[0]))


L.messagebox.showerror = lambda title, msg, **k: record(title, msg)
L.messagebox.showinfo = lambda title, msg, **k: record(title, msg)
L.messagebox.askyesno = lambda *a, **k: True


# ── 1. 走通一遍 ───────────────────────────────────────────────────────────

def happy_path(app, drive):
    print("== 1. 真搬一遍 ==")
    # 名字故意取成跟文件夹同名：这一档才该跟着新文件夹名改（见 _commit_move）。
    item = make_workspace(WORKSPACE, name="工作区甲")
    app.config_data["workspaces"] = [item]
    app.config_data["workplace"] = WORKSPACE
    app.refresh_workspaces()

    # mover.check 还回来的是 os.path.abspath 过的新路径，所以这边也要 abspath
    # 一下才能逐字比——不然 "C:/x\y" 和 "C:\x\y" 看着一样、比着不等。
    target = os.path.abspath(os.path.join(PROFILE, "新地方", "工作区乙"))
    os.makedirs(os.path.dirname(target))
    project_from = C.project_dir(WORKSPACE)
    project_to = C.project_dir(target)
    want = mover._manifest(WORKSPACE)
    before = bin_entries()

    app.move_workspace_to(item, target)
    check("进度区摆出来了", app._task_visible)
    check("标题是「正在搬工作区」",
          app.task_head_var.get() == "正在搬工作区", app.task_head_var.get())
    check("名字摆在右边", app.task_name_var.get() == "工作区甲",
          app.task_name_var.get())
    check("这一步的说明是复制", "复制" in app.task_step_var.get(),
          app.task_step_var.get())

    done = drive.wait(lambda: not app._move_running, limit=60)
    check("搬完了", done)
    check("进度区收回去了", not app._task_visible, app._task_visible)
    check("新位置整棵树跟老的对得上", mover._manifest(target) == want)
    check("配置指到新位置了", item["path"] == target, item["path"])
    check("名字跟着新文件夹名改了", item["name"] == "工作区乙", item["name"])
    check("旧的目录从硬盘上没了", not os.path.exists(WORKSPACE))
    check("旧的会话记录也没了", not os.path.exists(project_from))
    check("会话记录搬到新位置了", os.path.isfile(
        os.path.join(project_to, "sess-001.jsonl")))
    check("memory 子目录也在", os.path.isfile(
        os.path.join(project_to, "memory", "记着的.md")))
    after = bin_entries()
    if before is None or after is None:
        print("  （读不到回收站目录，跳过「进没进回收站」这一条）")
    else:
        check("旧的真进了回收站", len(after) > len(before),
              "{} -> {}".format(len(before), len(after)))
    check("反馈行说了搬哪儿去了", target in app.feedback_var.get(),
          app.feedback_var.get())
    # 列表刷新过之后，那行的副标题（也就是屏幕上显示的路径）得是新路径。
    rows = app.ws_list.inner.winfo_children()
    check("列表里那行指着新路径了", rows and rows[0].subtitle == target,
          rows[0].subtitle if rows else "没有行")
    return item, target


# ── 2. 名字没被改过的才跟着走 ─────────────────────────────────────────────

def name_rule(app, drive):
    print("== 2. 用户自己起的名字不跟着文件夹跑 ==")
    src = os.path.join(PROFILE, "名字那档")
    make_workspace(src, name="我自己起的")
    item = app.config_data["workspaces"][0]
    item["name"], item["path"] = "我自己起的", src
    target = os.path.abspath(os.path.join(PROFILE, "名字那档-新"))
    app.move_workspace_to(item, target)
    drive.wait(lambda: not app._move_running, limit=60)
    check("名字留住了", item["name"] == "我自己起的", item["name"])
    check("路径改过去了", item["path"] == target, item["path"])


# ── 3. 该拦的 ─────────────────────────────────────────────────────────────

def refusals(app, drive):
    print("== 3. 开工前该拦的 ==")
    src = os.path.join(PROFILE, "拦一拦")
    make_workspace(src, name="栏")
    item = app.config_data["workspaces"][0]
    item["name"], item["path"] = "栏", src

    # 会话还开着
    app.running = [{"name": "栏", "path": src, "proc": None, "hwnd": None}]
    del MESSAGES[:]
    app.move_workspace_to(item, os.path.join(PROFILE, "哪儿"))
    check("会话开着 -> 拦住", MESSAGES and "还开着" in MESSAGES[-1][0],
          MESSAGES[-1][0] if MESSAGES else "没弹框")
    check("拦住了就没开进度区", not app._task_visible)
    check("拦住了目录没动", os.path.isdir(src))
    app.running = []

    # 手动整理交接文档正在写：那个 claude 摆着同一块进度区（写的是不是这个目录
    # 都拦——进度区只有一块）
    class _Busy:
        @staticmethod
        def poll():
            return None

    app._handoff_ctx = (item, os.path.join(src, "handoff.md"), 0.0)
    app._handoff_proc = _Busy()
    del MESSAGES[:]
    app.feedback_var.set("")
    app.move_workspace_to(item, os.path.join(PROFILE, "哪儿"))
    check("交接文档正写着 -> 走反馈行不搬",
          not MESSAGES and "交接文档" in app.feedback_var.get(),
          app.feedback_var.get())
    check("拦住了目录没动", os.path.isdir(src))
    app._handoff_ctx = None
    app._handoff_proc = None

    # 上一轮活还没干完
    del MESSAGES[:]
    app.feedback_var.set("")
    app._move_running = True
    app.move_workspace_to(item, os.path.join(PROFILE, "哪儿"))
    check("手上有活 -> 走反馈行不弹框", not MESSAGES and "手上有活"
          in app.feedback_var.get(), app.feedback_var.get())
    app._move_running = False

    # 目标已经存在（mover.check 那一层的话，得原样摆出来）
    exists = os.path.join(PROFILE, "早就有了")
    os.makedirs(exists)
    del MESSAGES[:]
    app.move_workspace_to(item, exists)
    check("目标已存在 -> 拦住", MESSAGES and "已经存在" in MESSAGES[-1][1],
          MESSAGES[-1][0] if MESSAGES else "没弹框")
    check("拦住了也没开进度区", not app._task_visible)

    # 目录不存在
    item["path"] = os.path.join(PROFILE, "压根没有")
    del MESSAGES[:]
    app.move_workspace_to(item, os.path.join(PROFILE, "哪儿"))
    check("目录不存在 -> 拦住", MESSAGES and "找不到目录" in MESSAGES[-1][1],
          MESSAGES[-1][0] if MESSAGES else "没弹框")
    item["path"] = src


# ── 4. 复制那一层报错：旧的必须一个不动 ───────────────────────────────────

def copy_failure(app, drive):
    print("== 4. 复制炸了 -> 旧的没动，话摆到反馈行 ==")
    # 名字带上 s4：project_dir 是"非字母数字一律换短横"，不带标记的纯中文名字
    # 很容易跟别的小节撞成同一个目录名（撞了就不是在测这里想测的东西了）。
    src = os.path.join(PROFILE, "s4复制炸了")
    make_workspace(src, name="炸")
    item = app.config_data["workspaces"][0]
    item["name"], item["path"] = "炸", src
    target = os.path.abspath(os.path.join(PROFILE, "s4炸开的新家"))
    want = mover._manifest(src)

    app.feedback_var.set("")
    # 0.4：`mover` 不再从 launcher 的命名空间转手（它本来就只是 launcher 的
    # import）。反正 mover 是同一个模块对象，补在它自己身上，谁都跑不掉，
    # 也不用管谁把 copy_tree 复制到了自己那块命名空间。
    real = mover.copy_tree
    mover.copy_tree = lambda *a, **k: (_ for _ in ()).throw(
        mover.MoveError("复制的探针故意炸了一下。"))
    try:
        app.move_workspace_to(item, target)
        drive.wait(lambda: not app._move_running, limit=30)
    finally:
        mover.copy_tree = real

    check("反馈行说了没搬成", "没搬成" in app.feedback_var.get(),
          app.feedback_var.get())
    check("话里带着原因", "故意炸" in app.feedback_var.get(),
          app.feedback_var.get())
    check("旧的目录还在", os.path.isdir(src))
    check("旧的树一个字节没动", mover._manifest(src) == want)
    check("配置还指着旧的", item["path"] == src, item["path"])
    check("进度区收回去了", not app._task_visible)


# ── 5. 配置写不成：不许先把旧的丢了 ───────────────────────────────────────

def config_failure(app, drive):
    print("== 5. 配置写不成 -> 旧的必须还在（不能先把旧的丢了）==")
    src = os.path.join(PROFILE, "s5配置写不了")
    make_workspace(src, name="配")
    item = app.config_data["workspaces"][0]
    item["name"], item["path"] = "配", src
    target = os.path.abspath(os.path.join(PROFILE, "s5配置新家"))

    app.feedback_var.set("")
    real = L.save_config
    # 0.4：move_workspace_to 搬去了 ui/workspaces.py，它调的是那边自己那份
    # save_config。只改 L.save_config 是个哑补丁——「盘满」那一路就验不到了。
    rebind("save_config", lambda cfg: (_ for _ in ()).throw(OSError("盘满")))
    try:
        app.move_workspace_to(item, target)
        drive.wait(lambda: not app._move_running, limit=30)
    finally:
        rebind("save_config", real)

    check("旧的目录还在", os.path.isdir(src))
    check("配置里的路径退回去了", item["path"] == src, item["path"])
    check("配置里的名字也退回去了", item["name"] == "配", item["name"])
    check("新位置那份留着（让用户自己删）", os.path.isdir(target))
    check("反馈行说了怎么回事", "配置写不成" in app.feedback_var.get(),
          app.feedback_var.get())


# ── 6. 对话框 ─────────────────────────────────────────────────────────────

def dialog(app, drive):
    print("== 6. 对话框 ==")
    src = os.path.join(PROFILE, "对话框那档")
    make_workspace(src, name="框")
    item = app.config_data["workspaces"][0]
    item["name"], item["path"] = "框", src
    app.config_data["workspaces"] = [item]
    app.refresh_workspaces()

    before = len(app.ws_list.inner.winfo_children())
    rows = [w for w in app.ws_list.inner.winfo_children()]
    check("工作区那行摆出来了", rows, "{} 个子控件".format(before))
    app.update()
    labels = detail_actions(app)
    check("右栏有「搬迁」", "搬迁" in labels, labels)
    check("原有那几个动作都还在",
          {"移除", "改名", "↓", "↑", "打开目录"} <= set(labels), labels)

    app.open_move_dialog(item)
    drive.pump(0.6)
    tops = [w for w in app.winfo_children() if isinstance(w, L.tk.Toplevel)]
    check("对话框开出来了", len(tops) == 1, len(tops))
    if tops and OUT:
        drive.grab(OUT, window=tops[0])
    for w in tops:
        w.destroy()
    drive.pump(0.3)
    if OUT:
        drive.grab(OUT.replace(".png", "_main.png"))


def main():
    app = setup_app()
    drive = Drive(app)
    try:
        happy_path(app, drive)
        name_rule(app, drive)
        refusals(app, drive)
        copy_failure(app, drive)
        config_failure(app, drive)
        dialog(app, drive)
    finally:
        app.destroy()
        shutil.rmtree(PROFILE, ignore_errors=True)
    print()
    if bad:
        print("没过：{}".format(bad))
        sys.exit(1)
    print("全过")


main()
