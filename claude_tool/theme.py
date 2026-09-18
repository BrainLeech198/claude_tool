"""配色、字体，以及量文字宽度的那两个工具。

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
