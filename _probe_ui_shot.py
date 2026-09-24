"""新手向那几条改动的验收探针：空状态引导、导入当前配置、表单的人话备注。

    python _probe_ui_shot.py <出图前缀> [empty|import]

empty  —— 干净沙箱：截空状态主界面 +「添加模型」对话框（看新备注）。
import —— 沙箱里先摆一份完整的 settings.json：截空状态，点「导入当前」，
          看弹框、看名字预填得对不对，触发保存，再截导入之后的列表。

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude 和 ~/.claude_tool。
"""
import ctypes
import json
import os
import shutil
import sys
import time
from ctypes import wintypes

PROFILE = "D:/Desktop/tmp/ui_shot"
INSET = 8

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE
shutil.rmtree(PROFILE, ignore_errors=True)
os.makedirs(PROFILE, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk                                       # noqa: E402
from PIL import Image                                      # noqa: E402
from claude_tool.paths import CLAUDE_DIR, PRESET_DIR, SETTINGS_FILE   # noqa: E402

MODE = sys.argv[2] if len(sys.argv) > 2 else "empty"

# 一份"已经在别的窗口用着 claude"的配置，用来试导入
CURRENT = {
    "env": {
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "sk-probe-not-a-real-key",
        "ANTHROPIC_MODEL": "deepseek-chat",
    },
    "language": "简体中文",
}
if MODE == "import":
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(CURRENT, f, ensure_ascii=False, indent=2)

from claude_tool.ui.launcher import Launcher               # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
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
    """PrintWindow 把窗口自己的绘制内容抓下来。

    不用前台窗口那套：Windows 的前台锁会直接拒掉 SetForegroundWindow，抓到的
    就成了当时屏幕上真正盖在上面别的东西（第一次跑就把用户的微信截下来了）。
    PrintWindow 拿的是窗口自己的 DC，被遮住也照样是它，也不会碰用户的桌面。
    """
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

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<34} got={!r} want={!r}".format("ok  " if ok else "FAIL",
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
            image.crop((INSET, INSET,
                        image.width - INSET, image.height - INSET)).save(out)
            print("  图 ->", os.path.basename(out))
            return True
    print("  没找到窗口:", want)
    FAILED.append("截图 " + want)
    return False


def all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from all_widgets(child)


def top_level(app, prefix):
    # 得递归找：按钮上挂的那个提示窗口，master 是按钮自己，不是主窗口的直属子级
    for w in all_widgets(app):
        if isinstance(w, tk.Toplevel) and w.title().startswith(prefix):
            return w
    return None


def find_entries(widget):
    out = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Entry):
            out.append(child)
        out.extend(find_entries(child))
    return out


def find_pill(widget, text):
    """按标签找一个胶囊按钮，不 import PillButton——探针只认它那个 _text。"""
    for child in widget.winfo_children():
        if type(child).__name__ == "PillButton" and child._text == text:
            return child
        hit = find_pill(child, text)
        if hit is not None:
            return hit
    return None


def main():
    prefix = sys.argv[1]
    app = Launcher()
    pid = os.getpid()
    preset_file = os.path.join(PRESET_DIR, "DeepSeek.json")

    def step1():
        grab(pid, prefix + "_main.png", "Claude 启动器")

        # 0.4 新布局：左导航 + 右详情。下面这几条盯的是"新布局真的装上了"，
        # 不是像素——图必然跟重构前不一样；只问控件在不在、选中态有没有真切换。
        check("左栏导航区建出来了", getattr(app, "nav_area", None) is not None, True)
        check("右栏详情区建出来了", getattr(app, "detail_area", None) is not None, True)
        # 还没实现的时候 select_workspace 不存在，别让 AttributeError 把整个探针
        # 打断——那样只剩一段 traceback，看不出到底是哪几条断言没过。
        if not hasattr(app, "select_workspace"):
            check("有 select_workspace 这个方法", False, True)
        elif app.ws_view:
            first = app.ws_view[0]
            app.select_workspace(first["path"])
            check("选中态落到 selected_path 上", app.selected_path, first["path"])
            check("右栏跟着换了",
                  first["name"] in app.detail_title_var.get(), True)

        if MODE == "import":
            app._import_current()
            app.after(1000, step2)
        elif MODE == "tip":
            app.after(200, step_tip)
        elif MODE == "wswipe":
            # 不走 remove_workspace()——它要弹确认框，会把探针卡住
            app.config_data["workspaces"] = []
            app.refresh_workspaces()
            app.after(600, step_ws_empty)
        elif MODE == "edge":
            app.after(200, step_edge)
        else:
            app._open_model_dialog()
            app.after(1000, step_empty_dialog)

    def step_edge():
        """三条边界：旧配置没有新键、工作区列表空、选中的目录已经不在了。"""
        # 1. 旧配置：把 selected_workspace 抹掉，模拟 0.3.1 留下的 launcher.json
        app.config_data.pop("selected_workspace", None)
        app.select_workspace(None)          # 不崩就行
        check("旧配置（没有 selected_workspace）不崩", True, True)

        # 2. 工作区列表空：右栏该出空状态，不能是白板、更不能报错
        app.config_data["workspaces"] = []
        app.refresh_workspaces()
        check("列表空时右栏有引导语",
              "还没选工作区" in app.detail_title_var.get(), True)

        # 3. 选中的目录被删了/改名了：退到第一条，不是显示一个不存在的路径
        app.config_data["workspaces"] = [{"name": "甲", "path": "D:/nope/甲"}]
        app.config_data["selected_workspace"] = "D:/nope/已经不在了"
        app.refresh_workspaces()
        check("选中那本没了就退到第一条",
              app.selected_path, "D:/nope/甲")
        app.after(200, finish)

    def step_ws_empty():
        grab(pid, prefix + "_wswipe.png", "Claude 启动器")
        app.after(200, finish)

    # 悬停提示是独立的小窗口，主窗口的截图里看不见，得单独抓。认它靠身高——
    # 主窗口八百多像素高，提示条三十几像素，Tk 那边的 Toplevel 树不靠谱
    # （窗口是挂在按钮底下的，标题还跟主窗口一样）。
    def tips_of(pid):
        return [(t, h, r) for t, h, r in windows_of(pid)
                if r.bottom - r.top < 80]

    def step_tip():
        button = find_pill(app, "打开配置目录")
        check("顶栏那个按钮找得到", button is not None, True)
        if button is None:
            app.after(200, finish)
            return
        check("没动鼠标时提示不冒头", len(tips_of(pid)), 0)
        button.event_generate("<Enter>", x=4, y=4)
        app.after(900, step_tip_shot)

    def step_tip_shot():
        tips = tips_of(pid)
        check("停一会儿提示弹出来了", len(tips), 1)
        if tips:
            _text, hwnd, rect = tips[0]
            image = capture(hwnd, rect.right - rect.left, rect.bottom - rect.top)
            if image is None:
                FAILED.append("截图 悬停提示")
            else:
                image.save(prefix + "_tip.png")
                print("  图 ->", os.path.basename(prefix + "_tip.png"))
        button = find_pill(app, "打开配置目录")
        if button is not None:
            button.event_generate("<Leave>")
        app.after(500, step_tip_closed)

    def step_tip_closed():
        check("鼠标移开就收了", len(tips_of(pid)), 0)
        app.after(200, finish)

    def step_empty_dialog():
        grab(pid, prefix + "_dialog.png", "添加模型")
        app.after(200, finish)

    def step2():
        dialog = top_level(app, "导入当前配置")
        check("导入对话框弹出来了", dialog is not None, True)
        if dialog is None:
            app.after(200, finish)
            return
        grab(pid, prefix + "_import.png", "导入当前配置")
        entries = find_entries(dialog)
        check("名字预填成 DeepSeek",
              entries[0].get() if entries else None, "DeepSeek")
        check("保存前还没写文件", os.path.exists(preset_file), False)
        dialog.focus_force()
        dialog.event_generate("<Return>")          # 等价于点「保存」
        app.after(900, step3)

    def step3():
        grab(pid, prefix + "_after.png", "Claude 启动器")
        check("预设文件写出来了", os.path.exists(preset_file), True)
        try:
            with open(preset_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            saved = None
        check("存的就是当前那份配置（整份照抄）", saved, CURRENT)
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            check("settings.json 没被动过", json.load(f), CURRENT)
        app.after(200, finish)

    def finish():
        print()
        app.destroy()

    app.after(2600, step1)
    app.mainloop()

    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
