"""冻结版冒烟：跑 dist 里那个 exe，确认窗口起得来、底栏还是完整的一排。

冻结版最容易死在"某个模块没被打进去"上——claude_tool.selfupdate 是这版新加的，
真漏了的话 launcher 顶上那句 import 就会炸，窗口根本不会出现。所以"窗口起来了"
本身就等于"新模块打进去了、能被 import"。

再抓一张主界面的图，人眼看一眼底栏：0.4 起「自动查启动器新版」那个勾已经去掉，
底下应当只剩内嵌终端、自动继续、自动查 claude 新版三个勾，且版式没塌。

沙箱 USERPROFILE，不碰用户真实的 ~/.claude_tool；窗口位置也改成屏幕外（见
park_profile），全程不上屏。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(ROOT, "dist", "claude_tool", "claude_tool.exe")
from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("frz_selfup")
OUT = os.path.join(ROOT, "_frozen_selfup.png")
INSET = 8

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
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
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def capture(hwnd, width, height):
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
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    from PIL import Image
    if not ok:
        return None
    return Image.frombuffer("RGB", (width, height), buf.raw, "raw", "BGRX", 0, 1)


def park_profile(profile):
    """把这份沙箱配置里的窗口位置挪到屏幕外。

    exe 是独立进程，探针那套"把窗口摆到 +30000"的手法够不着它（那只作用于本
    进程的 tk 窗口）；但它在启动时会按沙箱 launcher.json 里的 window 摆自己
    （见 ui/launcher.py 的 _restore_geometry → host.place_window）。所以先把
    那份配置改掉，窗口一出生就在屏幕外——全程不上屏。

    **四键必须齐全**。config.load_config 判"这份 window 有效吗"是这么写的：

        all(isinstance(window.get(k), int) and abs(window[k]) < 32768
            for k in ("x", "y", "w", "h")) and window["w"] >= 300 and window["h"] >= 300

    缺任何一个键就把**整份**丢掉、当没存过，窗口于是开在默认位置（屏幕正中）。
    第一版就只写了 x/y，结果窗口照样开在 (32,32)——那趟冒烟等于没离屏。

    **别把这段删了**：删掉之后窗口会按默认位置开在屏幕正中，虽然只闪一两秒就
    被 kill，那也算是"打扰用户"。
    """
    path = os.path.join(profile, ".claude_tool", "launcher.json")
    if not os.path.exists(path):
        print("  警告：沙箱里没有 launcher.json，窗口位置没法定，可能上屏")
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # 注意是 get 不是 setdefault：这份配置里 window 这个键**在**，但值是 null，
    # setdefault 见键在就原样返回 null，后面赋值当场 TypeError。
    win = data.get("window") or {}
    data["window"] = win
    win["x"], win["y"] = 32000, 32000
    # 缺 w/h 就补个兜底值——判定要求它们都是 >= 300 的 int（见上面 docstring）。
    if not (isinstance(win.get("w"), int) and win["w"] >= 300):
        win["w"] = 900
    if not (isinstance(win.get("h"), int) and win["h"] >= 300):
        win["h"] = 950
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("  窗口位置 -> 32000,32000（屏幕外）")


def main():
    if not os.path.exists(EXE):
        print("没有", EXE)
        return 1
    os.environ["USERPROFILE"] = PROFILE          # 给 exe 用，子进程继承
    os.environ["HOME"] = PROFILE
    env = dict(os.environ, USERPROFILE=PROFILE, HOME=PROFILE)
    park_profile(PROFILE)
    proc = subprocess.Popen([EXE], env=env)
    print("起了 exe，PID =", proc.pid)

    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = []

    def visit(hwnd, _lp):
        pid = ctypes.c_uint()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != proc.pid or not user32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 200 and r.bottom - r.top > 200:
            found.append((title.value, hwnd, r))
        return True

    for _ in range(60):
        found.clear()
        user32.EnumWindows(PROC(visit), 0)
        if found:
            break
        time.sleep(0.5)

    code = 0
    if not found:
        print("FAIL 窗口没起来（exe returncode = {}）".format(proc.poll()))
        code = 1
    else:
        title, hwnd, rect = found[0]
        print("ok   窗口起来了:", repr(title), rect.right - rect.left,
              "x", rect.bottom - rect.top,
              "位置", rect.left, rect.top)
        # 自证离屏：park_profile 要是被跳过（沙箱缺配置）或配置被判无效，窗口
        # 就会开在屏幕里。这里把坐标报出来、落在屏幕内就吱一声——省得下次又
        # 靠"尺寸看着差不多"去推断有没有上屏。
        if not (rect.left >= 30000 or rect.top >= 30000):
            print("     警告：窗口不在屏幕外（{} {}），这趟可能上了屏".format(
                rect.left, rect.top))
        time.sleep(0.8)
        image = capture(hwnd, rect.right - rect.left, rect.bottom - rect.top)
        if image is None:
            print("FAIL PrintWindow 没抓到")
            code = 1
        else:
            image.crop((INSET, INSET, image.width - INSET,
                        image.height - INSET)).save(OUT)
            print("     图 ->", os.path.basename(OUT))
    proc.kill()
    proc.wait()
    print("exe 已杀")
    return code


if __name__ == "__main__":
    sys.exit(main())
