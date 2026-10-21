"""配色、字体、间距，以及量文字宽度的那两个工具。

颜色不是一张常量表，而是六套调色板（三浅三深，见下面的 PALETTES）。这么写是为了让主题能在
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
# 六套配色，三浅三深。每套必须同键同名、一样齐全——apply() 按 SLOTS 逐个搬，
# 缺一项换完就会留下旧色。
#
# 约束：**同一个颜色值不能出现在两套配色的不同槽位里**。recolor_tree 是拿颜色
# 值反查槽位的；不同槽撞了值，就分不清该往哪儿换。同一个槽在多套里用同一个值
# 没问题（比如几套浅色的面板都是纯白）——反查落到同一个槽，换过去还是它。
#
# 浅色那一组刻意压住饱和度：这是天天开着的启动器，不是海报。深色那一组的
# 强调色都提亮过，PRIMARY_FG 相应换成近墨色，实心按钮才是深字。

PAPER = {
    # 底色从冷灰换成微暖的纸色：长时间看不刺眼，也跟 Claude 的陶土橙、
    # CodeBuddy 的蓝都压得住。面板仍旧纯白，靠一点暖差分层。
    "PAGE_BG": "#f4f3ef",
    "PANEL_BG": "#ffffff",
    "HOVER_BG": "#ebe9e3",
    "BORDER": "#dedbd3",
    "TEXT": "#1d211f",
    # 辅助文字。白底约 5.6:1、纸色底约 5.2:1，9pt 说明文字过 AA 的 4.5:1。
    "MUTED": "#636b67",
    # 强调色从亮靛蓝换成深青绿：跟 CodeBuddy 的蓝拉开距离，又跟 Claude 的
    # 陶土橙成互补。白字压在上面约 5:1。
    "ACCENT": "#0f766e",
    "ACCENT_HOVER": "#0b5d57",
    "ACCENT_SOFT": "#e2f0ed",
    "WARN": "#c0524b",
    "WARN_HOVER": "#a8453f",
    "OK": "#2c8a5f",
    "OK_HOVER": "#247350",
    "ALERT_BG": "#fbefec",
    "PRIMARY_FG": "#ffffff",
    "ROW_ACTION_HOVER": "#e4e2db",
    "SCROLL_THUMB": "#c7c6bf",
    "SCROLL_THUMB_HOVER": "#a8a7a0",
    "TERM_BG": "#1c1c1c",
    # 两个 agent 的招牌色。claude 是那家的陶土橙，codebuddy 是蓝。
    "AGENT_CLAUDE": "#b5542f",
    "AGENT_CLAUDE_BG": "#f7e7df",
    "AGENT_CODEBUDDY": "#2563d9",
    "AGENT_CODEBUDDY_BG": "#e3ecff",
}

CLAY = {
    # 奶油底 + 陶土橙强调。跟 Claude 的招牌色是一家人，但比 PAPER 更暖、
    # 更"纸张发黄"一点，适合全天开着当工作台。
    "PAGE_BG": "#f8f2e9",
    "PANEL_BG": "#fffaf2",
    "HOVER_BG": "#efe6d8",
    "BORDER": "#e2d6c4",
    "TEXT": "#2b211a",
    "MUTED": "#75614e",
    "ACCENT": "#a14a1c",
    "ACCENT_HOVER": "#853b13",
    "ACCENT_SOFT": "#f4e4d3",
    "WARN": "#b23a34",
    "WARN_HOVER": "#942c27",
    "OK": "#387146",
    "OK_HOVER": "#2c5c3a",
    "ALERT_BG": "#fbe9e0",
    "PRIMARY_FG": "#ffffff",
    "ROW_ACTION_HOVER": "#eaddcb",
    "SCROLL_THUMB": "#d6c8b5",
    "SCROLL_THUMB_HOVER": "#bfae97",
    "TERM_BG": "#201a15",
    "AGENT_CLAUDE": "#8f3b1c",
    "AGENT_CLAUDE_BG": "#f6e3d7",
    "AGENT_CODEBUDDY": "#2a5fbd",
    "AGENT_CODEBUDDY_BG": "#e5ecf8",
}

FROST = {
    # 冷雾蓝灰底 + 靛蓝强调。最"办公软件"的一套：白面板、低饱和底，
    # 长时间看字不累，跟暖色那两套放在一起也不会认错。
    "PAGE_BG": "#f2f4f8",
    "PANEL_BG": "#ffffff",
    "HOVER_BG": "#e8ecf3",
    "BORDER": "#d9dfea",
    "TEXT": "#1a2333",
    "MUTED": "#5a667c",
    "ACCENT": "#3457c4",
    "ACCENT_HOVER": "#2a47a5",
    "ACCENT_SOFT": "#e3e9fa",
    "WARN": "#c43f49",
    "WARN_HOVER": "#a6323c",
    "OK": "#2f7c6a",
    "OK_HOVER": "#256457",
    "ALERT_BG": "#fbeaec",
    "PRIMARY_FG": "#ffffff",
    "ROW_ACTION_HOVER": "#e1e6ef",
    "SCROLL_THUMB": "#c6cddc",
    "SCROLL_THUMB_HOVER": "#adb6c9",
    "TERM_BG": "#151a24",
    "AGENT_CLAUDE": "#a34a28",
    "AGENT_CLAUDE_BG": "#f3e5de",
    "AGENT_CODEBUDDY": "#2f5fc7",
    "AGENT_CODEBUDDY_BG": "#e4ebfa",
}

MIDNIGHT = {
    "PAGE_BG": "#141716",
    "PANEL_BG": "#1c201e",
    "HOVER_BG": "#262b29",
    "BORDER": "#333a37",
    "TEXT": "#e9edea",
    "MUTED": "#98a19c",
    # 深色下强调色得提亮才看得见；提亮之后白字压不住，所以 PRIMARY_FG 换成
    # 近黑的墨色。深色主题里实心按钮一律深字，对比反而更稳（约 10:1）。
    "ACCENT": "#5fd3c4",
    "ACCENT_HOVER": "#7ce0d3",
    "ACCENT_SOFT": "#1f3431",
    "WARN": "#e07a72",
    "WARN_HOVER": "#ef938b",
    "OK": "#55b98a",
    "OK_HOVER": "#6fcb9d",
    "ALERT_BG": "#2c1e1b",
    "PRIMARY_FG": "#0d1513",
    "ROW_ACTION_HOVER": "#2f3532",
    "SCROLL_THUMB": "#3d4441",
    "SCROLL_THUMB_HOVER": "#4f5753",
    "TERM_BG": "#0e0f12",
    "AGENT_CLAUDE": "#e08a6d",
    "AGENT_CLAUDE_BG": "#3a2a24",
    "AGENT_CODEBUDDY": "#6ea2ff",
    "AGENT_CODEBUDDY_BG": "#22304a",
}

ABYSS = {
    # 偏蓝的深海黑 + 亮靛蓝强调。比 MIDNIGHT 冷、比它更"屏幕感"，
    # 夜里看跟编辑器深色主题更接近。
    "PAGE_BG": "#0f141c",
    "PANEL_BG": "#171e29",
    "HOVER_BG": "#212a38",
    "BORDER": "#2e3a4c",
    "TEXT": "#e6ecf5",
    "MUTED": "#8f9bad",
    "ACCENT": "#7aa2ff",
    "ACCENT_HOVER": "#9bb9ff",
    "ACCENT_SOFT": "#1b2b45",
    "WARN": "#e5827e",
    "WARN_HOVER": "#ef9a96",
    "OK": "#5cbd9b",
    "OK_HOVER": "#78cdb0",
    "ALERT_BG": "#2b1b1e",
    "PRIMARY_FG": "#0b1017",
    "ROW_ACTION_HOVER": "#28323f",
    "SCROLL_THUMB": "#394657",
    "SCROLL_THUMB_HOVER": "#4d5b6e",
    "TERM_BG": "#090c11",
    "AGENT_CLAUDE": "#e0916f",
    "AGENT_CLAUDE_BG": "#3a2a23",
    "AGENT_CODEBUDDY": "#79b0ff",
    "AGENT_CODEBUDDY_BG": "#1f2c44",
}

PLUM = {
    # 深梅紫。给"想换换口味"的那天用：强调色是淡紫，正文仍是暖白，
    # 不靠高饱和抢视线。
    "PAGE_BG": "#18141b",
    "PANEL_BG": "#211b25",
    "HOVER_BG": "#2c2431",
    "BORDER": "#3b3041",
    "TEXT": "#f0e9f3",
    "MUTED": "#a595ad",
    "ACCENT": "#c99ae8",
    "ACCENT_HOVER": "#dbb4f5",
    "ACCENT_SOFT": "#33263d",
    "WARN": "#e88a92",
    "WARN_HOVER": "#f2a2a9",
    "OK": "#6cc39b",
    "OK_HOVER": "#86d2b0",
    "ALERT_BG": "#2f1d22",
    "PRIMARY_FG": "#1a1220",
    "ROW_ACTION_HOVER": "#362c3b",
    "SCROLL_THUMB": "#453a4a",
    "SCROLL_THUMB_HOVER": "#594c5f",
    "TERM_BG": "#100c12",
    "AGENT_CLAUDE": "#e69a7a",
    "AGENT_CLAUDE_BG": "#3d2a26",
    "AGENT_CODEBUDDY": "#8fa8ff",
    "AGENT_CODEBUDDY_BG": "#262c47",
}

PALETTES = {
    "paper": PAPER,
    "clay": CLAY,
    "frost": FROST,
    "midnight": MIDNIGHT,
    "abyss": ABYSS,
    "plum": PLUM,
}

# 「外观」页画色卡要用的那点元数据。label 也用在状态行那句"配色换成…了"。
PALETTE_INFO = {
    "paper": {"label": "暖纸青绿", "dark": False},
    "clay": {"label": "奶油陶土", "dark": False},
    "frost": {"label": "冷雾靛蓝", "dark": False},
    "midnight": {"label": "暖黑青绿", "dark": True},
    "abyss": {"label": "深海靛蓝", "dark": True},
    "plum": {"label": "深梅紫", "dark": True},
}

# 0.7 之前配置里只记着 light / dark。老配置读进来先翻成新名字，免得升级
# 之后一开窗退回默认色。
ALIASES = {"light": "paper", "dark": "midnight"}

SLOTS = tuple(PAPER)

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
_current = "paper"

# 初始先把名字绑到默认那套——模块 import 完、apply() 还没调的那一小段，
# 界面模块里的 `from ... import PAGE_BG` 拿到的得是个能用的颜色。
globals().update(PAPER)


def current():
    return _current


def canonical(name):
    """配置里的名字 → 调色板名。认不出来返回 None。"""
    if name in PALETTES:
        return name
    return ALIASES.get(name)


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
    """换一套调色板。认不出来（含老名字 light / dark）就按别名或默认处理。"""
    global _current
    name = canonical(name)
    palette = PALETTES[name] if name else PAPER
    _current = name or "paper"

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
    # 深浅不再看名字，看这套配色自己的 dark 标记——现在有六套，名字里带不带
    # "dark" 都不能说明问题。
    if dark is None:
        dark = PALETTE_INFO[_current]["dark"]
    value = 1 if dark else 0

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

# 圆角尺度：卡片一档、小徽标一档。胶囊按钮按高度自己算（height/2），不在这儿。
# 两档分开是为了让"大块"和"小标记"的弧度看起来是一家人，而不是各写各的数。
RADIUS_CARD = 12
RADIUS_CHIP = 6


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
