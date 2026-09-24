"""工作区那行上的「删交接文档」：什么时候摆出来、按下去删的是什么、删完什么样。

两件事分开看：按钮只在目录里真有 handoff.md 时才挂上去（没有就不该给一颗按下去
只得到"没有"的按钮）；按下去问一句、进回收站、行上「有交接文档」那行小字跟着消失。

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude 和 ~/.claude_tool。

    python _probe_rmhandoff.py [出图.png]
"""
import ctypes
import json
import os
import shutil
import sys
from ctypes import wintypes

PROFILE = "D:/Desktop/tmp/rmhandoff"
WORKSPACE = "D:/Desktop/tmp/rmhandoff/工作区甲"
OUT = sys.argv[1] if len(sys.argv) > 1 else None

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.paths import CONFIG_FILE, TOOL_DIR        # noqa: E402
from claude_tool.ui import launcher as L                   # noqa: E402
# 0.4：Row 不再从 launcher 的命名空间转手了（它本来只是 launcher 的 import），
# 直接找定义它的地方拿。
from claude_tool.widgets import Row                        # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

PW_RENDERFULLCONTENT = 0x00000002

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

BAD = []


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
    from PIL import Image
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


def check(label, passed, detail=""):
    if not passed:
        BAD.append(label)
    print("{} {} {}".format("ok  " if passed else "FAIL", label, detail))


def rows(app):
    """工作区那几行（Row 是 Canvas，标题在 .title 上）。"""
    return [w for w in app.ws_list.inner.winfo_children()
            if isinstance(w, Row)]


# 0.4 起「改名/搬迁/↑↓/移除」不在行上了，搬到右栏那两排（见 ui/detail.py）：
# 上排日常（新会话/接着上次/打开目录）、下排管理（改名/搬迁/↑/↓/移除）。
# 所以这个探针盯的对象从"行上的动作"换成"右栏的动作"——要盯的事没变。
#
# 判"摆没摆"用 winfo_manager()，不用 winfo_ismapped()：离屏跑的时候后者对一个
# 明明 pack 着的按钮也会报 0（Task 8 实测），拿它当判据会得出反的结论。
def detail_actions(app):
    labels = [b._text for b in app._detail_daily + app._detail_manage]
    if app._handoff_btn.winfo_manager() == "pack":
        labels.append("删交接文档")
    return labels


def detail_meta(app):
    """右栏 meta 那行。原来这行字挂在行右侧（row.warn），0.4 挪右栏了。"""
    return app.detail_meta_var.get()


def bin_count():
    """这台机器上那个盘回收站里的条目总数。

    不按名字找：丢进回收站的文件会被改名成 $R<一串>，原名已经没了——所以只能
    看"删之前删之后多出来一个"（_probe_move.py 也是这么比的）。
    """
    root = os.path.join(os.path.splitdrive(PROFILE)[0] + "\\", "$Recycle.Bin")
    total = 0
    for sid in (os.listdir(root) if os.path.isdir(root) else []):
        try:
            total += len(os.listdir(os.path.join(root, sid)))
        except OSError:
            continue
    return total


def grab(app, path):
    frame = user32.GetParent(app.winfo_id())
    user32.SetWindowPos(frame, -1, 40, 40, 0, 0, 0x0001 | 0x0002)
    for _ in range(40):
        app.update()
    r = wintypes.RECT()
    user32.GetWindowRect(frame, ctypes.byref(r))
    image = capture(frame, r.right - r.left, r.bottom - r.top)
    if image is None:
        print("  !! PrintWindow 失败，这次没出图")
    else:
        image.save(path)
        print("  图 ->", path)
    user32.SetWindowPos(frame, -2, 0, 0, 0, 0, 0x0001 | 0x0002)


def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    shutil.rmtree(TOOL_DIR, ignore_errors=True)
    os.makedirs(WORKSPACE, exist_ok=True)
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"workplace": PROFILE, "roots": [PROFILE],
                   "workspaces": [{"name": "甲", "path": WORKSPACE}],
                   "window": None}, f, ensure_ascii=False, indent=2)

    handoff = os.path.join(WORKSPACE, "handoff.md")
    asked = []
    L.messagebox.askyesno = lambda title, msg, **k: asked.append(
        (title, msg)) or True

    app = L.Launcher()
    app.geometry("860x700+40+40")
    app.update()
    for _ in range(30):
        app.update()

    # ── 1. 没有文档：不给那颗按钮 ──
    print("--- 目录里没有 handoff.md ---")
    row = rows(app)[0]
    check("行摆出来了", row is not None)
    check("没摆「删交接文档」", "删交接文档" not in detail_actions(app),
          "{}".format(detail_actions(app)))
    check("旁边那几颗照旧都在",
          set(["移除", "改名", "↑", "↓", "打开目录", "搬迁"])
          <= set(detail_actions(app)),
          "{}".format(detail_actions(app)))
    check("右边那格没写「有交接文档」", "有交接文档" not in detail_meta(app),
          detail_meta(app))
    if OUT:
        grab(app, OUT)

    # 就算绕过界面直接叫它，也只是说一句没有、不动手
    item = app.config_data["workspaces"][0]
    app.feedback_var.set("")
    app.remove_handoff(item)
    check("直接叫它去删也只是说一句没有", "没有交接文档" in app.feedback_var.get(),
          app.feedback_var.get())
    check("没有任何弹框", not asked, "{}".format(asked))

    # ── 2. 有文档：按钮出现，右边那格亮字 ──
    print("--- 目录里放一份 handoff.md ---")
    with open(handoff, "w", encoding="utf-8") as f:
        f.write("# 交接\n这是一份探针造出来的交接文档。\n")
    app.refresh_workspaces()
    app.update()
    row = rows(app)[0]
    check("摆出了「删交接文档」", "删交接文档" in detail_actions(app),
          "{}".format(detail_actions(app)))
    check("它挂在这一串的最后（别的按钮一个没挪窝）",
          detail_actions(app)[-1] == "删交接文档",
          "{}".format(detail_actions(app)))
    check("右边那格写了「有交接文档」", "有交接文档" in detail_meta(app),
          detail_meta(app))
    if OUT:
        grab(app, OUT)

    # ── 3. 按下去：先问一句 ──
    print("--- 按下去 ---")
    del asked[:]
    before = bin_count()
    app.remove_handoff(item)
    check("问了用户一句", len(asked) == 1, "{}".format(asked))
    check("问的话里点了名", "handoff.md" in asked[0][1]
          and "「甲」" in asked[0][1], asked[0][1].replace("\n", " ")[:80])
    check("说清了还能找回来", "回收站" in asked[0][1], "")
    check("文件从目录里没了", not os.path.isfile(handoff))
    # 进回收站而不是直接 unlink：回收站里多出来一份
    after = bin_count()
    check("真进了回收站（不是永久删）", after > before,
          "{} -> {}".format(before, after))

    # ── 4. 删完：按钮收回去了，右边那格也不提了 ──
    print("--- 删完之后 ---")
    app.update()
    row = rows(app)[0]
    check("「删交接文档」收回去了", "删交接文档" not in detail_actions(app),
          "{}".format(detail_actions(app)))
    check("右边那格不提交接文档了", "有交接文档" not in detail_meta(app),
          detail_meta(app))
    check("反馈行说了结果", "回收站" in app.feedback_var.get(),
          app.feedback_var.get())
    check("工作区本身没被挪走", os.path.isdir(WORKSPACE))

    # ── 5. 用户在框里点「不」：什么都不该发生 ──
    print("--- 点「不」 ---")
    with open(handoff, "w", encoding="utf-8") as f:
        f.write("又一份。\n")
    app.refresh_workspaces()
    app.update()
    L.messagebox.askyesno = lambda title, msg, **k: False
    del asked[:]
    app.remove_handoff(item)
    check("文件还在", os.path.isfile(handoff))
    check("右栏还摆着那颗按钮", "删交接文档" in detail_actions(app),
          "{}".format(detail_actions(app)))

    app.destroy()
    print()
    print("结果:", "全过" if not BAD else "没过：" + str(BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
