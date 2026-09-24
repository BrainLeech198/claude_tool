"""冻结版冒烟：跑 dist 里那个 exe，确认窗口起得来、底栏还是完整的一排。

冻结版最容易死在"某个模块没被打进去"上——claude_tool.selfupdate 是这版新加的，
真漏了的话 launcher 顶上那句 import 就会炸，窗口根本不会出现。所以"窗口起来了"
本身就等于"新模块打进去了、能被 import"。

再抓一张主界面的图，人眼看一眼底栏：0.4 起「自动查启动器新版」那个勾已经去掉，
底下应当只剩内嵌终端、自动继续、自动查 claude 新版三个勾，且版式没塌。

沙箱 USERPROFILE，不碰用户真实的 ~/.claude_tool。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(ROOT, "dist", "claude_tool", "claude_tool.exe")
PROFILE = "D:/Desktop/tmp/frz_selfup"
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


def main():
    if not os.path.exists(EXE):
        print("没有", EXE)
        return 1
    os.environ["USERPROFILE"] = PROFILE          # 给 exe 用，子进程继承
    os.environ["HOME"] = PROFILE
    env = dict(os.environ, USERPROFILE=PROFILE, HOME=PROFILE)
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
              "x", rect.bottom - rect.top)
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
