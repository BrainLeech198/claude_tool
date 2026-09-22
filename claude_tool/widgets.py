"""自己画的几个控件：胶囊按钮、列表行、滚动区，以及表单那三个小工具。

Row 和 PillButton 都是 Canvas 而不是 Frame——圆角、悬停换色、按动作分格这些
在 Tk 的原生控件上做不到。
"""
import tkinter as tk
from tkinter import ttk

from claude_tool.theme import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_SOFT,
    BORDER,
    HOVER_BG,
    MUTED,
    PAGE_BG,
    PANEL_BG,
    TEXT,
    WARN,
    ellipsize,
    font,
    measure,
)


def rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """在 canvas 上画一个圆角矩形（用平滑多边形近似）。"""
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class PillButton(tk.Canvas):
    """圆角胶囊按钮。可以置灰：画成灰底灰字、不吃悬停、点了也不回调。"""

    def __init__(self, parent, text, command, primary=False, bg=PAGE_BG, height=28,
                 enabled=True):
        width = measure(text, 10, primary) + 26
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, borderwidth=0)
        self._text = text
        self._command = command
        self._primary = primary
        self._enabled = enabled
        self._hover = False
        self._cw, self._ch = width, height

        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._click())
        self._draw()

    def _click(self):
        # Canvas 按钮没有 state 可配，禁用只能自己拦——照 dialogs 里装东西那排
        # 按钮的老办法。拦在这儿而不是调用方，是为了让"灰的按不动"跟画出来的
        # 样子是一回事。
        if self._enabled:
            self._command()

    def set_enabled(self, value):
        self._enabled = value
        self._set_hover(False)
        self._draw()

    def _set_hover(self, value):
        self._hover = value and self._enabled
        self.configure(cursor="hand2" if self._hover else "arrow")
        self._draw()

    def _draw(self):
        self.delete("all")
        if not self._enabled:
            fill, outline, fg = HOVER_BG, BORDER, MUTED
        elif self._primary:
            fill = ACCENT_HOVER if self._hover else ACCENT
            outline, fg = "", "#ffffff"
        else:
            fill = HOVER_BG if self._hover else PANEL_BG
            outline, fg = BORDER, TEXT
        rounded_rect(self, 0, 0, self._cw - 1, self._ch - 1, self._ch / 2,
                     fill=fill, outline=outline or fill)
        self.create_text(self._cw / 2, self._ch / 2 + 1, text=self._text,
                         fill=fg, font=font(10, self._primary and self._enabled))


class Row(tk.Canvas):
    """列表里的一张卡片。可带副标题、右侧文字动作、选中标记。"""

    # 动作格子的最小宽度。实际宽度按标签量出来（见 _action_spans），这只是个下限，
    # 免得单字动作挤成一条缝。
    ICON_W = 32

    def __init__(self, parent, title, subtitle="", on_click=None, actions=(),
                 active=False, marker=False, warn="", warn_color=WARN,
                 bg=PAGE_BG, height=52, badge=None):
        super().__init__(parent, height=height, bg=bg,
                         highlightthickness=0, borderwidth=0)
        self.title = title
        self.subtitle = subtitle
        self.on_click = on_click
        self.actions = list(actions)
        self.active = active
        self.marker = marker
        self.warn = warn
        self.badge = badge
        self.warn_color = warn_color
        self._hover = None

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)

    # 命中判定：返回 None / "row" / 动作下标
    def _hit(self, x):
        for index, (x1, x2) in enumerate(self._action_spans()):
            if x1 <= x <= x2:
                return index
        return "row" if self.on_click else None

    def _action_spans(self):
        """每个动作占的横向格子，从右往左排。

        宽度按标签实际量出来算，不写死——动作现在都是「改名」「打开」这样的词，
        不是固定宽度的单个字形了。量的是当前字体下的真实宽度，所以中文和其他
        字符混着也不会挤在一起。
        """
        spans = []
        right = self.winfo_width() - 8
        for glyph, _callback in self.actions:
            width = max(self.ICON_W, measure(glyph, 11) + 18)
            spans.append((right - width, right))
            right -= width
        return spans

    def _on_motion(self, event):
        target = self._hit(event.x)
        if target != self._hover:
            self._hover = target
            self.configure(cursor="hand2" if target is not None else "arrow")
            self._draw()

    def _on_leave(self, _event):
        if self._hover is not None:
            self._hover = None
            self.configure(cursor="arrow")
            self._draw()

    def _on_press(self, event):
        target = self._hit(event.x)
        if target == "row":
            self.on_click()
        elif isinstance(target, int):
            self.actions[target][1]()

    def set_warn(self, text, color=WARN):
        """换掉右侧那行小字；测试结果就是靠它落在卡片上的。"""
        self.warn, self.warn_color = text, color
        self._draw()

    def _draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1:
            return

        if self.active:
            fill = ACCENT_SOFT
        elif self._hover == "row":
            fill = HOVER_BG
        else:
            fill = PANEL_BG
        rounded_rect(self, 0, 0, width - 1, height - 1, 10,
                     fill=fill, outline=ACCENT_SOFT if self.active else BORDER)

        x = 16
        if self.badge is not None:
            # Ctrl+1~9 按的是"屏幕上第几个"，行上就得有号，不然用户得自己数。
            # 空串是"这行超过 9 了、没有号"——照样占着这块地，几行标题才对得齐。
            cy = height / 2
            if self.badge:
                rounded_rect(self, x, cy - 9, x + 20, cy + 9, 5,
                             fill=HOVER_BG, outline=BORDER)
                self.create_text(x + 10, cy + 1, text=self.badge, fill=MUTED,
                                 font=font(9, True))
            x += 28
        if self.marker:
            cy = height / 2
            if self.active:
                self.create_oval(x, cy - 5, x + 10, cy + 5,
                                 fill=ACCENT, outline="")
            else:
                self.create_oval(x + 1, cy - 4, x + 9, cy + 4,
                                 fill=PANEL_BG, outline=MUTED, width=2)
            x += 22

        spans = self._action_spans()
        action_edge = spans[-1][0] - 10 if spans else width - 16
        title_room = max(action_edge - x, 40)
        subtitle_room = title_room

        # 备注靠右对齐、跟标题同一行。放下面那行会跟路径抢地方，把「D:\File\claude\kk」
        # 截成「D:\File\...」——正好是最活跃的几行最难认。标题这边空得很（名字普遍
        # 四五个字），拿标题那行的余量换路径的完整，划算得多。
        if self.warn:
            # 备注最长只占到"给标题留 90px"为止。它的内容是外部来的（连不上的
            # 底层原因、异常类名），长度不可控；不封顶的话一条长错误就能把标题
            # 挤成一片省略号，那一行就认不出来了。
            warn = ellipsize(self.warn, max(action_edge - x - 102, 60), 9)
            warn_width = measure(warn, 9)
            note_y = height / 2 - 9 if self.subtitle else height / 2
            title_room = max(title_room - warn_width - 12, 40)
            self.create_text(action_edge, note_y, text=warn,
                             anchor="e", fill=self.warn_color, font=font(9))

        title_font = font(11, self.active)
        if self.subtitle:
            self.create_text(x, height / 2 - 9,
                             text=ellipsize(self.title, title_room, 11, self.active),
                             anchor="w", fill=ACCENT if self.active else TEXT,
                             font=title_font)
            self.create_text(x, height / 2 + 11,
                             text=ellipsize(self.subtitle, subtitle_room), anchor="w",
                             fill=MUTED, font=font(9))
        else:
            self.create_text(x, height / 2, text=ellipsize(self.title, title_room, 11, self.active),
                             anchor="w", fill=ACCENT if self.active else TEXT,
                             font=title_font)

        for index, (glyph, _callback) in enumerate(self.actions):
            x1, x2 = spans[index]
            cx, cy = (x1 + x2) / 2, height / 2
            if self._hover == index:
                self.create_oval(x1 + 2, cy - 11, x2 - 2, cy + 11,
                                 fill="#e6e9ef", outline="")
            # 悬停的时候字色压深一点，跟底下的浅灰圆一起把"这个能点"说清楚。
            self.create_text(cx, cy, text=glyph, font=font(11),
                             fill=TEXT if self._hover == index else MUTED)


class ScrollArea(tk.Frame):
    """带细滚动条的容器，内容都塞进 .inner。"""

    def __init__(self, parent, max_height, bg=PAGE_BG):
        super().__init__(parent, bg=bg)
        self.max_height = max_height
        self.canvas = tk.Canvas(self, height=max_height, bg=bg,
                                highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", style="Slim.Vertical.TScrollbar",
                            command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    def _on_inner_resize(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()

    def fit(self):
        """把高度收到内容那么多，超过 max_height 才出现滚动条。"""
        self.update_idletasks()
        self.canvas.configure(height=min(self.inner.winfo_reqheight(), self.max_height))

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def scroll(self, steps):
        self.canvas.yview_scroll(steps, "units")


class Tip:
    """挂在控件上的悬停提示。

    顶栏那排东西是有宽度上限的（左边还得摆模型名和版本号），想给按钮补一句
    解释就只能等鼠标停下来再冒出来。挂哪都行，用 add="+" 绑事件，不会顶掉
    控件自己原有的 <Enter>/<Leave>。
    """

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._timer = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._timer = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._timer is not None:
            self.widget.after_cancel(self._timer)
            self._timer = None

    def _show(self):
        self._timer = None
        if self._window is not None or not self.widget.winfo_ismapped():
            return
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.configure(bg=BORDER)
        tk.Label(window, text=self.text, bg=PANEL_BG, fg=TEXT, font=font(9),
                 padx=8, pady=4).pack(padx=1, pady=1)
        # 靠按钮右边缘对齐：顶栏那个按钮就贴着窗口右边，按左边缘摆会把提示推出屏幕
        window.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() - window.winfo_reqwidth()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        window.wm_geometry("+%d+%d" % (max(x, 0), y))
        self._window = window

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None


# ── 通用表单控件 ──────────────────────────────────────────────────────────


def make_entry(parent, var, width=40, secret=False):
    return tk.Entry(parent, textvariable=var, width=width, show="*" if secret else "",
                    bg=PANEL_BG, fg=TEXT, relief="flat", font=font(11),
                    insertbackground=TEXT, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=ACCENT)


def make_combo(parent, values, size=10, min_chars=18, textvariable=None):
    """只读下拉，宽度按最长那条选项量出来。

    ttk.Combobox 的 width 既不是像素也不是字数，单位是"数字 0 的宽度"：字号 10 时
    一个单位正好 10 像素。中文一条比西文宽得多（「改文件不问，跑命令才问
    （acceptEdits）」量出来 314 像素），照着字数写死 width=30 只给 300 像素的文本区，
    末尾那几个字连同括号一起被下拉箭头切掉。这里先量最长那条，再换算成这个单位。
    """
    values = [str(v) for v in values]
    unit = max(measure("0", size), 1)
    widest = max((measure(v, size) for v in values), default=0)
    chars = max(min_chars, int(widest // unit) + 2)
    return ttk.Combobox(parent, state="readonly", width=chars, font=font(size),
                        values=values, textvariable=textvariable)


def make_form(dialog, title):
    """建一个统一样式的对话框，返回可往里放控件的 body 框架。"""
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.configure(bg=PAGE_BG)
    dialog.transient(dialog.master)

    tk.Label(dialog, text=title, bg=PAGE_BG, fg=TEXT,
             font=font(13, True), anchor="w").pack(fill="x", padx=20, pady=(16, 10))
    body = tk.Frame(dialog, bg=PAGE_BG)
    body.pack(fill="x", padx=20)
    return body


def finish_form(dialog, buttons):
    """在对话框底部摆按钮，buttons 是 [(文字, 回调, 是否主按钮), ...]。"""
    holder = tk.Frame(dialog, bg=PAGE_BG)
    holder.pack(fill="x", padx=20, pady=(16, 16))
    for text, command, primary in buttons:
        PillButton(holder, text, command, primary=primary,
                   bg=PAGE_BG).pack(side="right", padx=(8, 0))


# ── 主窗口 ────────────────────────────────────────────────────────────────
