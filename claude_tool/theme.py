"""配色、字体、间距，以及量文字宽度的那两个工具。

颜色不是一张常量表，而是一组调色板（light / dark）。这么写是为了让主题能在
运行时换：`apply()` 把本模块里这批名字换成新调色板的值，**同时**把已经
`from claude_tool.theme import PAGE_BG` 的那些模块里的同名全局也换掉——界面
模块都是这么导入的，不换它们，换完主题只有之后新画的控件跟着变，已经摆在
屏幕上的还是旧色。

换完色由 `recolor_tree()` 把已经建出来的控件扫一遍，按"旧颜色 → 新颜色"的
对照表重设 bg/fg 这些选项；自绘控件（PillButton / Row）各自 `_draw()` 一次，
它们画画用的是模块级颜色，重绑之后自然就是新的。

对照表按用途分三张（背景 / 前景 / 边框），不能混成一张：浅色的 `PANEL_BG`
和按钮上的白字都是 #ffffff，一张表会把白字也当成背景色换掉。分开之后，
"当前是背景色" 和 "当前是前景色" 各查各的表，互不串门。
"""
import sys
import tkinter as tk
from tkinter import font as tkfont


# ── 调色板 ────────────────────────────────────────────────────────────────
#
# 两个调色板必须同键同名，apply() 按 SLOTS 逐个搬。同一张调色板里背景那组
# 颜色不能重复，跨调色板也不行——recolor_tree 是拿颜色值反查槽位的，两个槽
# 同色就分不清该往哪儿换。前景那组同理。

LIGHT = {
    "PAGE_BG": "#f3f4f6",
    "PANEL_BG": "#ffffff",
    "HOVER_BG": "#eef0f4",
    "BORDER": "#e3e6eb",
    "TEXT": "#1e2329",
    "MUTED": "#7d8590",
    "ACCENT": "#4a6cf7",
    "ACCENT_HOVER": "#3b5ce0",
    "ACCENT_SOFT": "#e9eeff",
    "WARN": "#d05a56",
    "WARN_HOVER": "#b94a46",
    "OK": "#2f9e6b",
    "OK_HOVER": "#268557",
    "ALERT_BG": "#fdf3f2",
    "PRIMARY_FG": "#ffffff",
    "ROW_ACTION_HOVER": "#e6e9ef",
    "SCROLL_THUMB": "#cbd1da",
    "SCROLL_THUMB_HOVER": "#aab3c0",
    "TERM_BG": "#1c1c1c",
    # 两个 agent 的招牌色。claude 是那家的陶土橙，codebuddy 是蓝。
    "AGENT_CLAUDE": "#b5542f",
    "AGENT_CLAUDE_BG": "#f7e7df",
    "AGENT_CODEBUDDY": "#2563d9",
    "AGENT_CODEBUDDY_BG": "#e3ecff",
}

DARK = {
    "PAGE_BG": "#14161a",
    "PANEL_BG": "#1c1f25",
    "HOVER_BG": "#242832",
    "BORDER": "#2e333d",
    "TEXT": "#e8ebf0",
    "MUTED": "#8a93a2",
    "ACCENT": "#6b87ff",
    "ACCENT_HOVER": "#8299ff",
    "ACCENT_SOFT": "#232a3f",
    "WARN": "#e57373",
    "WARN_HOVER": "#f08a84",
    "OK": "#4cbf8b",
    "OK_HOVER": "#63cf9c",
    "ALERT_BG": "#2b1d1f",
    "PRIMARY_FG": "#ffffff",
    "ROW_ACTION_HOVER": "#2f3542",
    "SCROLL_THUMB": "#3a4150",
    "SCROLL_THUMB_HOVER": "#4b5466",
    "TERM_BG": "#0e0f12",
    "AGENT_CLAUDE": "#e08a6d",
    "AGENT_CLAUDE_BG": "#3a2a24",
    "AGENT_CODEBUDDY": "#6ea2ff",
    "AGENT_CODEBUDDY_BG": "#22304a",
}

PALETTES = {"light": LIGHT, "dark": DARK}
SLOTS = tuple(LIGHT)

# 换色时按用途查不同的对照表（理由见文件开头）。
_BG_SLOTS = ("PAGE_BG", "PANEL_BG", "HOVER_BG", "ACCENT_SOFT", "ALERT_BG",
             "ROW_ACTION_HOVER", "SCROLL_THUMB", "SCROLL_THUMB_HOVER",
             "TERM_BG", "AGENT_CLAUDE_BG", "AGENT_CODEBUDDY_BG")
_FG_SLOTS = ("TEXT", "MUTED", "ACCENT", "ACCENT_HOVER", "WARN", "WARN_HOVER",
             "OK", "OK_HOVER", "AGENT_CLAUDE", "AGENT_CODEBUDDY")
_BORDER_SLOTS = ("BORDER",)

_BG_MAP = {}
_FG_MAP = {}
_BORDER_MAP = {}
_current = "light"

# 初始把人名先绑成浅色——模块 import 完、apply() 还没调的那一小段，
# 界面模块里的 `from ... import PAGE_BG` 拿到的得是个能用的颜色。
globals().update(LIGHT)


def current():
    return _current


def _translation(target, slots):
    """任意调色板里的颜色 → target 里同一个槽的颜色。"""
    table = {}
    for slot in slots:
        for palette in PALETTES.values():
            value = palette[slot]
            if value != target[slot]:
                table.setdefault(value, target[slot])
    return table


def apply(name):
    """换一套调色板。name 认不出来就当 light。"""
    global _current
    palette = PALETTES.get(name) or LIGHT
    _current = name if name in PALETTES else "light"

    _BG_MAP.clear()
    _BG_MAP.update(_translation(palette, _BG_SLOTS))
    _FG_MAP.clear()
    _FG_MAP.update(_translation(palette, _FG_SLOTS))
    _BORDER_MAP.clear()
    _BORDER_MAP.update(_translation(palette, _BORDER_SLOTS))

    globals().update(palette)
    # 已经 import 过这批名字的模块，它们的全局也得跟着换（见文件开头）。
    for module in list(sys.modules.values()):
        if module is None or module.__name__ == __name__:
            continue
        if not module.__name__.startswith("claude_tool"):
            continue
        namespace = getattr(module, "__dict__", None)
        if not namespace:
            continue
        for slot in SLOTS:
            if slot in namespace:
                namespace[slot] = palette[slot]


def resolve(value):
    """把"某个调色板里的颜色"翻成当前调色板里对应的那个。

    Row 的备注颜色是建行时传进来的（可能是上一个主题的 WARN），画的时候
    得按当前主题翻一下，不然换完主题那条备注还挂着旧色。
    """
    if not value:
        return value
    for slots in (_BG_SLOTS, _FG_SLOTS, _BORDER_SLOTS):
        for slot in slots:
            for palette in PALETTES.values():
                if palette[slot] == value:
                    return globals()[slot]
    return value


def recolor_tree(widget):
    """把已经建出来的控件按当前调色板重上一遍色。

    bg / fg 这类选项只在"当前值正好等于某个旧调色板颜色"时才改——用户自己
    塞进来的颜色（比如终端那块 #1c1c1c 一直是深色）不该被主题动。
    """
    options = (
        ("bg", _BG_MAP), ("activebackground", _BG_MAP),
        ("selectcolor", _BG_MAP), ("troughcolor", _BG_MAP),
        ("fg", _FG_MAP), ("activeforeground", _FG_MAP),
        ("insertbackground", _FG_MAP),
        ("highlightbackground", _BORDER_MAP), ("highlightcolor", _FG_MAP),
    )
    for option, table in options:
        try:
            value = str(widget.cget(option))
        except (tk.TclError, AttributeError):
            continue
        new = table.get(value.lower()) or table.get(value)
        if new is not None and new != value:
            try:
                widget.configure({option: new})
            except tk.TclError:
                pass
    # 自绘控件：画的内容也要重来一遍。ScrollArea 之类没有 _draw 的直接跳过。
    draw = getattr(widget, "_draw", None)
    if callable(draw):
        try:
            draw()
        except tk.TclError:
            pass
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return
    for child in children:
        recolor_tree(child)


def titlebar(window, dark=None):
    """把 Windows 那圈系统标题栏也刷成深色。

    Tk 只画窗口里面那块，标题栏是系统画的——深色主题下上面顶着一条白边，
    一眼就是"没做完"。DWMWA_USE_IMMERSIVE_DARK_MODE：Win10 20H1 起是 20，
    再早是 19，两个都试，成的那个就算了。别的平台、或者系统不认，静默跳过。
    """
    if sys.platform != "win32":
        return
    value = 1 if (dark is None and _current == "dark") or dark else 0

    def paint():
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            flag = ctypes.c_int(value)
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(flag),
                        ctypes.sizeof(flag)) == 0:
                    break
        except Exception:
            pass

    paint()
    # 窗口还没露出来的时候设的这一遍，DWM 经常不认账——标题栏一直浅着，
    # 等它映到屏幕上还得再来一次（实测 Win11 是这样）。
    try:
        if not window.winfo_ismapped():
            window.after(120, paint)
    except tk.TclError:
        pass


# ── 间距 ──────────────────────────────────────────────────────────────────

# 左列和终端栏之间、以及正文左右各留的空档。
#
# 搁这儿而不是 launcher.py 的常量区：用它的一共三处，其中两处已经拆到了别的
# 模块——内嵌终端（ui/terminal.py 的 _room_for_terminal）和会话启动（ui/
# sessions.py 的 launch_workspace）。那两个文件 import 不到 launcher 的模块级
# 常量，硬写就会绕成循环 import。
SIDE_GAP = 14

# 内容离窗口边那道统一空档。顶栏那条线、正文两栏、状态行文字、没装 claude 时那条
# 告警，左边缘（右边缘同理）都得落在同一条竖线上。
#
# 这是 0.4 收尾时量出来的问题：那几处本来各用各的数——顶栏和状态行是 20、正文是
# 14（图省事复用了上面那个 SIDE_GAP）、告警区又是 16。三套数并排一放，左导航那栏
# 就比顶栏和状态行的字凸出去 6 像素，右栏内容又比顶栏那颗按钮凸出去 6 像素，一眼
# 就看得出错位。所以单拎一个常数出来，五处都指它。`_probe_align.py` 盯着这件事。
#
# 跟 SIDE_GAP 的分工：那个是"两栏之间"的缝（还会被内嵌终端拿去算外挂宽度），
# 这个是"内容离窗口边"的缝，两件事。
PAGE_GUTTER = 20


def font(size=11, bold=False):
    return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)


FONT_FAMILY = "Microsoft YaHei UI"


# 量文字用的 Font 对象建起来不便宜，按字号缓存一份。
# 换了 Tk 根（比如测试里反复开关窗口）会让缓存的字体失效，那时重建即可。
_measured = {}


def measure(text, size=11, bold=False):
    key = (size, bold)
    try:
        return _measured[key].measure(text)
    except KeyError:
        _measured[key] = tkfont.Font(family=FONT_FAMILY, size=size,
                                     weight="bold" if bold else "normal")
        return _measured[key].measure(text)
    except tk.TclError:
        _measured.clear()
        return measure(text, size, bold)


def ellipsize(text, max_width, size=11, bold=False):
    if measure(text, size, bold) <= max_width:
        return text
    while text and measure(text + "…", size, bold) > max_width:
        text = text[:-1]
    return text + "…"
