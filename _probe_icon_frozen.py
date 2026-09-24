"""打包版的窗口图标：跑 dist 里的 exe，抓它标题栏看那只螃蟹在不在。

exe 是按 PID 找窗口的，不按标题——用户自己那份启动器标题一模一样。

抓图走 PrintWindow（离屏渲染），**不用** SetWindowPos(TOPMOST) +
SetForegroundWindow + ImageGrab：那三样是"把窗口拽到屏幕最前、再抓整块屏幕"，
窗口会直接怼到用户眼前。窗口位置也提前写到屏幕外（见 park_profile），
全程不上屏——用户在电脑前也看不到任何东西弹出来。

沙箱 USERPROFILE，不碰用户真实的 ~/.claude_tool。
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
PROFILE = "D:/Desktop/tmp/icon_frz"
OUT = os.path.join(ROOT, "_icon_frozen_zoom.png")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
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
    """离屏渲染整窗（含标题栏那块非客户区）成一张图。"""
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
    只写 x/y 就是想当然——实测窗口照样开在 (32,32)，白忙一场。x/y 也别顶到
    32767 以上，同样会被丢。

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
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 200 and r.bottom - r.top > 200:
            found.append((cls.value, title.value, hwnd, r))
        return True

    for _ in range(60):
        found.clear()
        user32.EnumWindows(PROC(visit), 0)
        if found:
            break
        time.sleep(0.5)

    code = 0
    if not found:
        print("FAIL 没找到窗口，exe 是不是起不来？returncode =", proc.poll())
        code = 1
    else:
        cls, title, hwnd, rect = found[0]
        print("ok   窗口类名:", cls, "标题:", repr(title))
        print("     矩形:", rect.left, rect.top, rect.right, rect.bottom)
        time.sleep(0.8)
        image = capture(hwnd, rect.right - rect.left, rect.bottom - rect.top)
        if image is None:
            print("FAIL PrintWindow 没抓到")
            code = 1
        else:
            from PIL import Image
            zoom = image.crop((0, 0, 150, 40)).resize((150 * 4, 40 * 4),
                                                      Image.NEAREST)
            zoom.save(OUT)
            print("     存了", os.path.basename(OUT))
            orange = sum(1 for p in zoom.getdata()
                         if p[0] > 180 and p[1] < 160 and p[2] < 130)
            print("ok   橙色像素数:", orange, "（螃蟹是橙的，>0 就说明图标画上去了）")
            if orange <= 0:
                print("FAIL 一个橙色像素都没数到，图标可能没打进去")
                code = 1
    proc.kill()
    proc.wait()
    print("exe 已杀")
    return code


if __name__ == "__main__":
    sys.exit(main())
