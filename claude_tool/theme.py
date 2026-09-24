"""配色、字体、间距，以及量文字宽度的那两个工具。

measure/ellipsize 单独拎出来是因为它们被 widgets 和 ui 大量用到来算布局，
是"界面"这件事里唯一有逻辑的部分。
"""
import tkinter as tk
from tkinter import font as tkfont


# ── 外观 ──────────────────────────────────────────────────────────────────

PAGE_BG = "#f4f5f7"
PANEL_BG = "#ffffff"
HOVER_BG = "#eef0f4"
BORDER = "#e2e5ea"
TEXT = "#1f2328"
MUTED = "#8b939e"
ACCENT = "#4a6cf7"
ACCENT_HOVER = "#3b5ce0"
ACCENT_SOFT = "#eaefff"
WARN = "#d05a56"
OK = "#2f9e6b"
ALERT_BG = "#fdf3f2"

FONT_FAMILY = "Microsoft YaHei UI"


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
