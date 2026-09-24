"""claude 版本这件事：官网记下的那一格、启动器这边查更新那条路。

四段：
  A. 纯函数    —— parse / number / newest_claude（拿仓库里真的 docs/releases.js 试）
  B. check()   —— npm 优先、官网兜底、两个都问不到、本机读不出来
  C. 界面      —— 默认关不联网；勾上查一次并冒胶囊；手动查压过勾；查不到时自动
                  闭嘴手动说话；全程没起过任何进程
  D. 打包脚本  —— 新条目带上 claude、重打包不覆盖已写的、空的时候补上

claude 那个子进程和两个联网的源全被拦下来了，只记调用、不真跑。
沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude 和 ~/.claude_tool。

    python _probe_version.py <出图前缀>
"""
import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

PROFILE = "D:/Desktop/tmp/version"
INSET = 8

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE
shutil.rmtree(PROFILE, ignore_errors=True)
os.makedirs(PROFILE, exist_ok=True)
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from _probe_common import rebind                       # noqa: E402

import tkinter as tk                                       # noqa: E402
from PIL import Image                                      # noqa: E402

from claude_tool.paths import CONFIG_FILE                  # noqa: E402
from claude_tool import versions                           # noqa: E402

os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
with open(CONFIG_FILE, "w", encoding="utf-8") as f:
    json.dump({"workplace": PROFILE, "roots": [PROFILE], "workspaces": [],
               "window": None}, f, ensure_ascii=False, indent=2)

from claude_tool.ui.launcher import Launcher               # noqa: E402
from claude_tool.ui import launcher as L                   # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
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


def capture(hwnd, width, height):
    """PrintWindow 抓窗口自己的绘制内容，不碰桌面、不抢前台。"""
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem, bitmap)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(info), 0)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return Image.frombuffer("RGB", (width, height), buf.raw, "raw", "BGRX", 0, 1)


FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<42} got={!r} want={!r}".format("ok  " if ok else "FAIL",
                                                label, got, want))


def contains(label, haystack, needle):
    ok = needle in (haystack or "")
    if not ok:
        FAILED.append(label)
    print("{} {:<42} {} in {!r}".format("ok  " if ok else "FAIL", label,
                                        needle, haystack))


def windows_of(pid):
    found = []

    def visit(hwnd, _lparam):
        owner = ctypes.c_uint()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        text = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, text, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append((text.value, hwnd, r))
        return True

    user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def grab(pid, out, want):
    for text, hwnd, rect in windows_of(pid):
        if want in text:
            time.sleep(0.3)
            image = capture(hwnd, rect.right - rect.left, rect.bottom - rect.top)
            if image is None:
                print("  !! PrintWindow 失败:", want)
                FAILED.append("截图 " + want)
                return False
            image.crop((INSET, INSET, image.width - INSET,
                        image.height - INSET)).save(out)
            print("  图 ->", os.path.basename(out))
            return True
    print("  没找到窗口:", want)
    FAILED.append("截图 " + want)
    return False


def pump(app, seconds):
    """跑一会儿事件循环，让 after 里的心跳真的跳起来。"""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def pure():
    print("\n=== A. 纯函数 ===")
    check("裸版本号", versions.parse("2.1.150"), (2, 1, 150))
    check("claude 前缀", versions.parse("claude 2.1.150"), (2, 1, 150))
    check("带尾巴", versions.parse("2.1.150 (Claude Code)"), (2, 1, 150))
    check("启动那行的原文", versions.parse("claude 2.1.150 (Claude Code)"),
          (2, 1, 150))
    check("没数字", versions.parse("读不到版本号"), None)
    check("空", versions.parse(""), None)
    check("None", versions.parse(None), None)
    check("两段也能读", versions.parse("2.1"), (2, 1))
    check("写回去", versions.number((2, 1, 150)), "2.1.150")

    with open(os.path.join(ROOT, "docs", "releases.js"), encoding="utf-8") as f:
        text = f.read()
    check("真的 releases.js 里读得出 claude 那格",
          versions.newest_claude(text), "2.1.150")


def check_logic():
    print("\n=== B. check() 的取源顺序 ===")
    answers = {"npm": (2, 1, 278), "site": (2, 1, 200), "calls": []}
    real_npm, real_site = versions.npm_version, versions.site_version
    versions.npm_version = lambda: (answers["calls"].append("npm"),
                                    answers["npm"])[1]
    versions.site_version = lambda: (answers["calls"].append("site"),
                                     answers["site"])[1]

    answers["npm"], answers["site"] = (2, 1, 278), (2, 1, 200)
    answers["calls"] = []
    check("npm 说了就不问官网", versions.check("claude 2.1.150"),
          ("2.1.278", "npm", True))
    check("没多问一次", answers["calls"], ["npm"])

    answers["npm"] = None
    answers["calls"] = []
    check("npm 哑了就问官网", versions.check("claude 2.1.150"),
          ("2.1.200", "官网", True))
    check("两个都问过", answers["calls"], ["npm", "site"])

    answers["npm"], answers["site"] = None, None
    check("两个都问不到", versions.check("claude 2.1.150"), None)

    answers["npm"] = (2, 1, 150)
    check("同版不算落后", versions.check("claude 2.1.150"),
          ("2.1.150", "npm", False))
    answers["npm"] = (2, 1, 9)
    check("网上更旧也不算落后", versions.check("claude 2.1.150"),
          ("2.1.9", "npm", False))
    answers["npm"] = (2, 1, 0)
    check("2.1 和 2.1.0 算同版（补零）", versions.check("claude 2.1"),
          ("2.1.0", "npm", False))
    answers["npm"] = (2, 1, 278)
    check("本机读不出来就不比", versions.check("没找到 claude"),
          ("2.1.278", "npm", False))
    check("本机空串也不比", versions.check(""),
          ("2.1.278", "npm", False))

    versions.npm_version, versions.site_version = real_npm, real_site


def ui(prefix):
    print("\n=== C. 界面 ===")
    real_run = subprocess.run
    real_popen = subprocess.Popen
    real_find = L.find_claude
    calls = {"run": [], "popen": []}
    answers = {"npm": (2, 1, 278), "site": None, "calls": []}

    class FakeResult:
        def __init__(self, out):
            self.stdout, self.stderr = out, b""

    def fake_run(argv, **kwargs):
        calls["run"].append(argv)
        return FakeResult(b"2.1.150 (Claude Code)\n")

    def fake_popen(argv, **kwargs):
        calls["popen"].append(argv)
        raise AssertionError("查版本不该起任何进程：{}".format(argv))

    subprocess.run = fake_run
    subprocess.Popen = fake_popen
    rebind("find_claude", lambda: r"C:\fake\claude.cmd")
    versions.npm_version = lambda: (answers["calls"].append("npm"),
                                    answers["npm"])[1]
    versions.site_version = lambda: (answers["calls"].append("site"),
                                     answers["site"])[1]

    app = Launcher()
    pid = os.getpid()
    pump(app, 1.6)

    check("版本号问过了", calls["run"][-1][-1], "--version")
    contains("顶栏贴上了版本号", app.version_var.get(), "claude 2.1.150")
    check("默认关着：一次网都没联", answers["calls"], [])
    check("默认关着：没冒胶囊", app._update_pill, None)

    # ── 勾上：当场查一次，本机落后就冒胶囊 ──
    app.auto_version_var.set(True)
    app._on_version_check_toggle()
    pump(app, 1.2)
    check("勾上就问了一次 npm", answers["calls"], ["npm"])
    check("勾的状态落盘了", app.config_data["auto_version_check"], True)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        check("配置里也写着", json.load(f)["auto_version_check"], True)
    check("冒了胶囊", app._update_pill is not None, True)
    check("胶囊上写的是新版号", app._update_pill._text, "有新版 2.1.278")
    contains("反馈栏说清了本机和新版", app.feedback_var.get(), "2.1.278")
    contains("反馈栏说点它可以升级", app.feedback_var.get(), "帮你升级")
    grab(pid, prefix + "_update.png", "Claude 启动器")

    # ── 那颗胶囊点开的是升级面板，不是浏览器里的官网说明页 ──
    # 0.2.x 上点它只是拉浏览器开官方说明页；0.3.0 起开的是安装面板的升级模式，
    # 把它认出来的那条升级命令填好、预选上。这儿的 routes 换成一份手艺活——
    # 这台机器的 PATH 上没有 npm，真 routes 里那两条都不可走，撞不出"预选"这一
    # 支。认装法那件事由 _probe_install.py 拿八种路径挨个撞，不在这儿重复。
    import claude_tool.install as I
    import claude_tool.ui.dialogs as D
    real_routes = I.routes
    I.routes = lambda *a, **k: [
        {"name": "官方原生脚本", "why": "再跑一遍就是升级。", "needs": "不用别的",
         "ready": True, "argv": ["x"], "show": "irm 官方那条", "key": "native",
         "timeout": None, "pick": False, "note": "不是这条装的，选它会再装一份。"},
        {"name": "npm", "why": "npm 装的走 npm。", "needs": "有 Node",
         "ready": True, "argv": ["x"], "show": "npm install -g 假包", "key": "npm",
         "timeout": None, "pick": True, "note": "你现在这份就是这条装的。"},
    ]
    app._update_pill._command()
    pump(app, 0.4)

    def tree(widget):
        yield widget
        for child in widget.winfo_children():
            yield from tree(child)

    panel = next((w for w in app.winfo_children()
                  if getattr(w, "title", lambda: "")() == "帮你升级 claude"), None)
    check("点开的是「帮你升级 claude」面板", panel is not None, True)
    if panel is not None:
        entries = [w for w in tree(panel) if w.winfo_class() == "Entry"]
        radios = [w for w in tree(panel) if w.winfo_class() == "Radiobutton"]
        texts = [str(w.cget("text")) for w in tree(panel)
                 if w.winfo_class() == "Label"]
        command = entries[0].get() if entries else ""
        contains("默认摆的是标了 pick 那条的命令（不是第一条）",
                 command, "npm install -g")
        check("那条上标着「推荐」",
              any("推荐" in str(r.cget("text")) and "npm" in str(r.cget("text"))
                  for r in radios), True)
        check("按钮写的是「开始升级」，不是「开始装」",
              any(getattr(w, "_text", "") == "开始升级" for w in tree(panel)), True)
        check("认出来了就点明「你现在这份就是这条装的」",
              any("你现在这份" in t for t in texts), True)
        panel.destroy()
        pump(app, 0.2)
    I.routes = real_routes

    # ── 手动查：本机已经是最新，胶囊该收回去 ──
    answers["npm"] = (2, 1, 150)
    app.check_claude_update()
    pump(app, 1.2)
    check("不落后了：胶囊收回去", app._update_pill, None)
    contains("反馈栏说不用更新", app.feedback_var.get(), "不用更新")

    # ── 官网兜底：npm 哑了 ──
    answers["npm"], answers["site"] = None, (2, 1, 500)
    app.check_claude_update()
    pump(app, 1.2)
    check("官网兜底也冒胶囊", app._update_pill._text, "有新版 2.1.500")
    contains("反馈里点名是官网说的", app.feedback_var.get(), "官网")

    # ── 两个源都问不到：自动那次闭嘴，手动那次说话 ──
    answers["npm"], answers["site"] = None, None
    app.feedback_var.set("SENTINEL")
    app._start_version_check()
    pump(app, 1.2)
    check("自动那次查不到就不吭声", app.feedback_var.get(), "SENTINEL")
    app.check_claude_update()
    pump(app, 1.2)
    contains("手动那次查不到要说一声", app.feedback_var.get(), "没查到")

    # ── 本机版本没读出来 ──
    app._local_version = "没找到 claude"
    app.check_claude_update()
    pump(app, 1.2)
    contains("本机版本读不出来时明说", app.feedback_var.get(), "比不了")
    app._local_version = ""
    app.feedback_var.set("SENTINEL")
    app.check_claude_update()
    check("还没读到版本号就点，先不动", app.feedback_var.get() != "SENTINEL", True)
    contains("提示等一会儿", app.feedback_var.get(), "过一两秒")

    # ── 手动查不受那个勾管 ──
    app.auto_version_var.set(False)
    app._on_version_check_toggle()
    app._local_version = "claude 2.1.150"
    answers["npm"], answers["site"] = (2, 1, 278), None
    answers["calls"] = []
    app.check_claude_update()
    pump(app, 1.2)
    check("勾关着也能手动查", answers["calls"], ["npm"])
    check("起点进程那栏一直是空的", calls["popen"], [])

    subprocess.run = real_run
    subprocess.Popen = real_popen
    rebind("find_claude", real_find)
    app.destroy()


def packaging():
    print("\n=== D. 打包脚本写下的那一格 ===")
    spec = importlib.util.spec_from_file_location(
        "update_releases", os.path.join(ROOT, "build", "update_releases.py"))
    U = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(U)

    tmp = os.path.join(PROFILE, "rel")
    os.makedirs(tmp, exist_ok=True)
    U.OUTPUT = tmp
    U.DATA = os.path.join(tmp, "releases.js")
    version = U.read_version()
    with open(os.path.join(tmp, "ClaudeLauncher-{}-Setup.exe".format(version)),
              "wb") as f:
        f.write(b"x" * 4096)

    def write_rel(entries):
        with open(U.DATA, "w", encoding="utf-8") as f:
            f.write("// 头\nwindow.RELEASES = "
                    + json.dumps(entries, ensure_ascii=False, indent=2) + ";\n")

    def read_rel():
        with open(U.DATA, encoding="utf-8") as f:
            return versions.newest_claude(f.read())

    def write_version_iss():
        """跑一遍打包脚本的 main()。

        update_releases 自己读 sys.argv[1] 当平台，而本探针的 argv[1] 是出图前缀
        ——不钉死的话它拿到的是个路径，直接"不认识这个平台"退出（早先靠"前缀恰好
        也叫 windows"蒙过去的）。
        """
        real_argv = sys.argv
        sys.argv = ["update_releases.py", "windows"]
        try:
            U.main()
        finally:
            sys.argv = real_argv

    # 读本机版本号这两下
    real_find, real_run = U.find_claude, U.subprocess.run

    class FakeResult:
        def __init__(self, out):
            self.stdout, self.stderr = out, b""

    U.find_claude = lambda: "claude"
    U.subprocess.run = lambda argv, **kw: FakeResult(b"2.1.150 (Claude Code)\n")
    check("从 claude --version 里读出号", U.local_claude(), "2.1.150")
    U.subprocess.run = lambda argv, **kw: FakeResult(b"not a version\n")
    check("读不出来就是空串", U.local_claude(), "")
    U.find_claude = lambda: None
    check("没装 claude 也是空串", U.local_claude(), "")
    U.find_claude, U.subprocess.run = real_find, real_run

    U.local_claude = lambda: "2.1.150"
    write_rel([])
    write_version_iss()
    check("新条目带上了 claude", read_rel(), "2.1.150")

    # 重打同一版：已经写了的不许改
    U.local_claude = lambda: "2.1.300"
    write_rel([{"version": version, "date": "2026-01-01", "notes": "手写的",
                "claude": "2.1.150", "windows": None, "linux": None,
                "macos": None}])
    write_version_iss()
    check("重打包不动已写的那格", read_rel(), "2.1.150")
    with open(U.DATA, encoding="utf-8") as f:
        entries = json.loads(f.read().split("window.RELEASES = ")[1].rstrip(";\n"))
    check("手写的说明还在", entries[0]["notes"], "手写的")
    check("安装包那格也刷新了", bool(entries[0]["windows"]), True)

    # 上次打包时没装 claude，留了个空的：这次该补上
    write_rel([{"version": version, "date": "2026-01-01", "notes": "",
                "claude": "", "windows": None, "linux": None, "macos": None}])
    write_version_iss()
    check("空的那格会补上", read_rel(), "2.1.300")


def main():
    prefix = sys.argv[1]
    pure()
    check_logic()
    ui(prefix)
    packaging()

    print()
    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
