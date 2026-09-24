"""交接文档那个 git 选项：弹不弹框、落不落盘、拼给 claude 的提示词对不对。

三条路各验一遍：
  非 git 目录      —— 不弹框，提示词里没有 gitignore 那段
  git 目录 + 不勾  —— 弹框，照旧什么都不说
  git 目录 + 勾上  —— 弹框，提示词里带上 gitignore 那段，勾选记回工作区

claude 的 Popen 被拦下来了，只记 argv，不真起会话。
沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude 和 ~/.claude_tool。

    python _probe_gitopt.py <出图前缀>
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

PROFILE = "D:/Desktop/tmp/gitopt"
INSET = 8

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE
shutil.rmtree(PROFILE, ignore_errors=True)
os.makedirs(PROFILE, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk                                       # noqa: E402
from PIL import Image                                      # noqa: E402

REPO_WS = os.path.join(PROFILE, "仓库")
PLAIN_WS = os.path.join(PROFILE, "普通目录")

from claude_tool.paths import CONFIG_FILE                  # noqa: E402
from claude_tool.handoff import (                          # noqa: E402
    GITIGNORE_PROMPT,
    HANDOFF_FILE,
)

os.makedirs(os.path.join(REPO_WS, ".git"), exist_ok=True)
os.makedirs(PLAIN_WS, exist_ok=True)
os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
with open(CONFIG_FILE, "w", encoding="utf-8") as f:
    json.dump({"workplace": PROFILE,
               "roots": [PROFILE],
               "workspaces": [{"name": "仓库", "path": REPO_WS},
                              {"name": "普通", "path": PLAIN_WS}],
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
    """PrintWindow 抓窗口自己的绘制内容，不碰桌面、不抢前台（见 _probe_ui_shot）。"""
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
    print("{} {:<38} got={!r} want={!r}".format("ok  " if ok else "FAIL",
                                                label, got, want))


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


def all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from all_widgets(child)


def dialog_of(app, prefix):
    for w in all_widgets(app):
        if isinstance(w, tk.Toplevel) and w.title().startswith(prefix):
            return w
    return None


def find_pill(widget, text):
    for child in all_widgets(widget):
        if type(child).__name__ == "PillButton" and child._text == text:
            return child
    return None


def find_check(widget):
    for child in all_widgets(widget):
        if isinstance(child, tk.Checkbutton):
            return child
    return None


def find_ws(app, path):
    for item in app.config_data["workspaces"]:
        if item["path"] == path:
            return item
    return None


def saved_flag():
    """配置里那个工作区条目现在是什么状态；没写进去就是 None。"""
    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for item in data.get("workspaces") or []:
        if item.get("path") == REPO_WS:
            return item.get("handoff_ignore_git")
    return "找不到这个工作区"


def main():
    prefix = sys.argv[1]
    app = Launcher()
    pid = os.getpid()

    # 拦 Popen：只记 argv，不真起 claude。打的是 subprocess 模块本身的属性——
    # 调用处写的是 `subprocess.Popen`，改实例上碰不到。
    #
    # 0.4 Task 4 之前这儿读的是 `L.subprocess`（那时 launcher import 了它）。交接
    # 文档那套搬去 ui/sessions.py 之后，launcher 的命名空间里就没有这个名字了，
    # 改成直接用 stdlib 的模块对象——两边本来就是同一个，打在它身上谁都跑不掉。
    real_popen = subprocess.Popen
    spy = {}

    class FakeProc:
        def __init__(self):
            self.returncode = 0

        def poll(self):
            return 0

    def fake_popen(argv, **kwargs):
        spy["argv"] = argv
        return FakeProc()

    subprocess.Popen = fake_popen

    def prompt():
        for arg in spy.get("argv") or []:
            if "交接文档" in arg:
                return arg
        return ""

    def run(**kwargs):
        """走一遍 write_handoff，把它起的那个"进程"立即收尾。"""
        spy.pop("argv", None)
        app.write_handoff(**kwargs)
        app.update()

    repo = find_ws(app, REPO_WS)
    plain = find_ws(app, PLAIN_WS)
    check("两个工作区都在", (repo is not None, plain is not None), (True, True))

    # ── 1. 非 git 目录：不弹框，提示词里没有那段 ──
    run(item=plain)
    check("非 git 目录不弹框", dialog_of(app, "整理交接文档"), None)
    check("非 git 目录也起了进程", spy.get("argv") is not None, True)
    check("提示词里没有 gitignore 那段", GITIGNORE_PROMPT in prompt(), False)
    app._poll_handoff()

    # ── 2. git 目录 + 不勾：弹框，但还是什么都不说 ──
    run(item=repo)
    dlg = dialog_of(app, "整理交接文档")
    check("git 目录弹出了框", dlg is not None, True)
    box = find_check(dlg) if dlg else None
    check("框里那个勾找得到", box is not None, True)
    check("默认没勾（不主动动仓库）",
          bool(int(dlg.getvar(box.cget("variable")))) if box else None, False)
    check("弹框这会儿还没起进程", spy.get("argv"), None)
    grab(pid, prefix + "_ask.png", "整理交接文档")
    find_pill(dlg, "开始整理")._command()
    app.update()
    check("点了开始才起进程", spy.get("argv") is not None, True)
    check("没勾就还是不说 git 的事", GITIGNORE_PROMPT in prompt(), False)
    check("没勾就没往配置里写", saved_flag(), None)
    app._poll_handoff()

    # ── 3. git 目录 + 勾上：带上那段，勾选记回工作区 ──
    run(item=repo)
    dlg = dialog_of(app, "整理交接文档")
    box = find_check(dlg) if dlg else None
    box.invoke()
    app.update()
    grab(pid, prefix + "_ask_ticked.png", "整理交接文档")
    find_pill(dlg, "开始整理")._command()
    app.update()
    check("勾上之后提示词带上了那段", GITIGNORE_PROMPT in prompt(), True)
    check("提示词里还留着正文", "整份覆盖，不要追加" in prompt(), True)
    check("勾选记回了工作区条目", saved_flag(), True)
    app._poll_handoff()

    # ── 4. 再把勾去掉，验能改回去 ──
    app.write_handoff(repo)
    app.update()
    dlg = dialog_of(app, "整理交接文档")
    find_check(dlg).invoke()
    find_pill(dlg, "开始整理")._command()
    app.update()
    check("能把勾去掉", saved_flag(), False)
    check("去掉之后提示词也干净了", GITIGNORE_PROMPT in prompt(), False)
    app._poll_handoff()

    subprocess.Popen = real_popen
    print()
    app.destroy()

    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
