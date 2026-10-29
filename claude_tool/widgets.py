"""自己画的几个控件：胶囊按钮、列表行、滚动区，以及表单那三个小工具。

Row 和 PillButton 都是 Canvas 而不是 Frame——圆角、悬停换色、按动作分格这些
在 Tk 的原生控件上做不到。
"""
import tkinter as tk
from tkinter import ttk

from claude_tool import agents, theme
from claude_tool.theme import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_SOFT,
    AGENT_CLAUDE,
    AGENT_CLAUDE_BG,
    AGENT_CODEBUDDY,
    AGENT_CODEBUDDY_BG,
    BORDER,
    HOVER_BG,
    MUTED,
    OK,
    OK_HOVER,
    PAGE_BG,
    PANEL_BG,
    PRIMARY_FG,
    RADIUS_CARD,
    RADIUS_CHIP,
    ROW_ACTION_HOVER,
    TEXT,
    WARN,
    WARN_HOVER,
    ellipsize,
    font,
    measure,
    resolve,
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


def agent_style(agent_id):
    """agent 徽标用的那套颜色和字母：claude 陶土橙 C，codebuddy 蓝 B。

    字母是给 240 宽左栏用的——全名摆不下，拼音首字母又没人看得懂；右栏详情
    那边地方宽裕，直接写全名（见 ui/detail.py）。

    国内/国际两个 codebuddy 档同族同徽标（family=codebuddy）：同一套 CLI 的
    两个账号，徽标再分就认不出了；区分靠右栏全名和设置页的档位按钮。
    """
    if agents.get(agent_id).family == "codebuddy":
        return AGENT_CODEBUDDY_BG, AGENT_CODEBUDDY, "B"
    return AGENT_CLAUDE_BG, AGENT_CLAUDE, "C"


def section_title(parent, text):
    """分节标题：左边一条强调色小竖条 + 标题。

    0.4 之前标题和正文一个色，扫一眼分不出"这是一节的头"还是"这是内容"。
    竖条不占几个像素，但整页的层次立刻出来了。
    """
    box = tk.Frame(parent, bg=PAGE_BG)
    tk.Frame(box, bg=ACCENT, width=3, height=13).pack(side="left")
    tk.Label(box, text=text, bg=PAGE_BG, fg=TEXT,
             font=font(12, True)).pack(side="left", padx=(7, 0))
    return box


class PillButton(tk.Canvas):
    """圆角胶囊按钮。可以置灰：画成灰底灰字、不吃悬停、点了也不回调。

    键盘也能用：takefocus 打开，Tab 走到它、回车或空格当按一下，聚焦时描一圈
    强调色的边。自绘控件默认进不了 Tab 顺序，不补这段的话纯键盘用户在主窗里
    除了 Ctrl+数字之外什么都按不了。

    tone 用来表达"这颗按钮带着状况"：warn 是红底（模型连不上），ok 是绿底。
    不传就是普通按钮/强调按钮，跟以前一样。
    """

    def __init__(self, parent, text, command, primary=False, bg=None, height=28,
                 enabled=True, tone=None, width=None):
        # width 传了就把胶囊撑到那个宽度（左栏底部那排等宽按钮用）；不传还是
        # 按文字量出来的自然宽度。
        canvas_width = (width if width is not None
                        else measure(text, 10, primary) + 26)
        super().__init__(parent, width=canvas_width, height=height,
                         bg=PAGE_BG if bg is None else bg,
                         highlightthickness=0, borderwidth=0, takefocus=1)
        self._fixed_width = width
        self._text = text
        self._command = command
        self._primary = primary
        self._tone = tone
        self._enabled = enabled
        self._hover = False
        self._focused = False
        self._cw, self._ch = canvas_width, height

        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._click())
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Return>", lambda e: self._click())
        self.bind("<space>", lambda e: self._click())
        self._draw()

    def _set_focus(self, value):
        self._focused = value
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

    def set_text(self, text):
        self._text = text
        if self._fixed_width is None:
            self._cw = measure(text, 10, self._primary) + 26
            self.configure(width=self._cw)
        self._draw()

    def _set_hover(self, value):
        self._hover = value and self._enabled
        self.configure(cursor="hand2" if self._hover else "arrow")
        self._draw()

    def _draw(self):
        self.delete("all")
        if not self._enabled:
            fill, outline, fg = HOVER_BG, BORDER, MUTED
        elif self._tone == "warn":
            fill, outline, fg = (WARN_HOVER if self._hover else WARN), "", PRIMARY_FG
        elif self._tone == "ok":
            fill, outline, fg = (OK_HOVER if self._hover else OK), "", PRIMARY_FG
        elif self._primary:
            fill = ACCENT_HOVER if self._hover else ACCENT
            outline, fg = "", PRIMARY_FG
        else:
            fill = HOVER_BG if self._hover else PANEL_BG
            outline, fg = BORDER, TEXT
        # 键盘焦点画一圈强调色边，跟悬停那个换底色是两回事——鼠标悬停和 Tab
        # 聚焦可能同时发生，各表达各的。
        if self._focused and self._enabled:
            outline = ACCENT
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
                 active=False, marker=False, warn="", warn_color=None,
                 bg=None, height=52, badge=None, agent=None, tags=()):
        super().__init__(parent, height=height, bg=PAGE_BG if bg is None else bg,
                         highlightthickness=0, borderwidth=0)
        self.title = title
        self.subtitle = subtitle
        # 副标题那一行右端贴一排小标签。只在有副标题时摆——没有副标题的行
        # 标题本来就占满，再塞标签会把名字挤没。
        self.tags = list(tags or ())
        self.on_click = on_click
        self.actions = list(actions)
        self.active = active
        self.marker = marker
        self.warn = warn
        self.badge = badge
        # 这一行是哪一档 agent 的工作区。传了就在标题那行右端画个小徽标
        # （左栏 240 宽摆不下全名，用 C / B 一个字母）。
        self.agent = agent
        self.warn_color = WARN if warn_color is None else warn_color
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

    def set_warn(self, text, color=None):
        """换掉右侧那行小字；测试结果就是靠它落在卡片上的。"""
        self.warn, self.warn_color = text, (WARN if color is None else color)
        self._draw()

    def set_active(self, value):
        """换选中态。选中行不重建也能变——会话中心用上下键换行时走这条。"""
        if self.active != value:
            self.active = value
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
        rounded_rect(self, 0, 0, width - 1, height - 1, RADIUS_CARD,
                     fill=fill, outline=ACCENT_SOFT if self.active else BORDER)
        if self.active:
            # 选中那一行在最左边加一道强调色竖条。只靠浅色底的话，深色主题下
            # 跟悬停底几乎分不出来。
            rounded_rect(self, 6, 10, 9, height - 10, 1.5,
                         fill=ACCENT, outline="")

        x = 16
        if self.badge is not None:
            # Ctrl+1~9 按的是"屏幕上第几个"，行上就得有号，不然用户得自己数。
            # 空串是"这行超过 9 了、没有号"——照样占着这块地，几行标题才对得齐。
            cy = height / 2
            if self.badge:
                rounded_rect(self, x, cy - 9, x + 20, cy + 9, RADIUS_CHIP,
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

        # agent 徽标：标题那行右端一个小色块。先占位、再算标题的可用宽度，
        # 不然标题会一直画到徽标底下。
        chip_left = None
        if self.agent:
            cy = height / 2 - 9 if self.subtitle else height / 2
            cx2 = width - 10
            cx1 = cx2 - 20
            chip_bg, chip_fg, letter = agent_style(self.agent)
            rounded_rect(self, cx1, cy - 8, cx2, cy + 8, RADIUS_CHIP,
                         fill=chip_bg, outline=chip_fg, width=1)
            self.create_text((cx1 + cx2) / 2, cy + 1, text=letter,
                             font=font(8, True), fill=chip_fg)
            chip_left = cx1 - 6
            title_room = max(min(title_room, chip_left - x), 40)

        # 备注靠右对齐、跟标题同一行。放下面那行会跟路径抢地方，把「D:\File\claude\kk」
        # 截成「D:\File\...」——正好是最活跃的几行最难认。标题这边空得很（名字普遍
        # 四五个字），拿标题那行的余量换路径的完整，划算得多。
        if self.warn:
            # 备注最长只占到"给标题留 90px"为止。它的内容是外部来的（连不上的
            # 底层原因、异常类名），长度不可控；不封顶的话一条长错误就能把标题
            # 挤成一片省略号，那一行就认不出来了。
            edge = chip_left if chip_left is not None else action_edge
            warn = ellipsize(self.warn, max(edge - x - 102, 60), 9)
            warn_width = measure(warn, 9)
            note_y = height / 2 - 9 if self.subtitle else height / 2
            title_room = max(title_room - warn_width - 12, 40)
            self.create_text(edge, note_y, text=warn,
                             anchor="e", fill=resolve(self.warn_color),
                             font=font(9))

        title_font = font(11, self.active)
        if self.subtitle:
            # 标签从右往左排，排不下就丢掉靠左的那几个（名字和工作区在前，
            # 标签只是补充信息）。剩下的宽度才给副标题。
            chips = []
            edge = action_edge
            for tag in reversed(self.tags):
                caption = "#" + tag
                chip_w = measure(caption, 9) + 14
                if edge - chip_w < x + 70:
                    break
                chips.append((caption, edge - chip_w, edge))
                edge -= chip_w + 6
            chips.reverse()
            if chips:
                subtitle_room = max(min(subtitle_room, edge - 6 - x), 40)
            self.create_text(x, height / 2 - 9,
                             text=ellipsize(self.title, title_room, 11, self.active),
                             anchor="w", fill=ACCENT if self.active else TEXT,
                             font=title_font)
            self.create_text(x, height / 2 + 11,
                             text=ellipsize(self.subtitle, subtitle_room), anchor="w",
                             fill=MUTED, font=font(9))
            for caption, x1, x2 in chips:
                cy = height / 2 + 11
                rounded_rect(self, x1, cy - 8, x2, cy + 8, RADIUS_CHIP,
                             fill=HOVER_BG, outline=BORDER)
                self.create_text((x1 + x2) / 2, cy + 1, text=caption,
                                 fill=MUTED, font=font(8))
        else:
            self.create_text(x, height / 2, text=ellipsize(self.title, title_room, 11, self.active),
                             anchor="w", fill=ACCENT if self.active else TEXT,
                             font=title_font)

        for index, (glyph, _callback) in enumerate(self.actions):
            x1, x2 = spans[index]
            cx, cy = (x1 + x2) / 2, height / 2
            if self._hover == index:
                self.create_oval(x1 + 2, cy - 11, x2 - 2, cy + 11,
                                 fill=ROW_ACTION_HOVER, outline="")
            # 悬停的时候字色压深一点，跟底下的浅灰圆一起把"这个能点"说清楚。
            self.create_text(cx, cy, text=glyph, font=font(11),
                             fill=TEXT if self._hover == index else MUTED)


class PaletteCard(tk.Canvas):
    """「外观」页的一张配色卡：左边一块示意，右边名字和深浅。

    示意里的颜色是**这张卡代表的那套配色自己的**，不是当前主题的——所以都用
    create_* 画成图元；recolor_tree 只改控件选项，碰不到图元。换主题时设置页
    会把整排卡重建一遍（见 settings._render_theme_choice）。
    """

    W, H = 220, 66

    def __init__(self, parent, name, palette, label, dark, selected, command):
        super().__init__(parent, width=self.W, height=self.H, bg=PAGE_BG,
                         highlightthickness=0, borderwidth=0, takefocus=1)
        self.name = name
        self.palette = palette
        self.label = label
        self.dark = dark
        self.selected = selected
        self._command = command
        self._hover = False
        self._focused = False

        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._click())
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Return>", lambda e: self._click())
        self.bind("<space>", lambda e: self._click())
        self._draw()

    def _set_hover(self, value):
        self._hover = value
        self.configure(cursor="hand2" if value else "arrow")
        self._draw()

    def _set_focus(self, value):
        self._focused = value
        self._draw()

    def _click(self):
        self._command()

    def _draw(self):
        self.delete("all")
        width, height = self.W, self.H
        # 卡片本身跟着当前主题走；只有左边那块示意用的是目标配色的颜色。
        fill = HOVER_BG if self._hover else PANEL_BG
        marked = self.selected or self._focused
        rounded_rect(self, 1, 1, width - 2, height - 2, RADIUS_CARD,
                     fill=fill, outline=ACCENT if marked else BORDER,
                     width=2 if marked else 1)

        x, y, size = 10, 11, 44
        rounded_rect(self, x, y, x + size, y + size, RADIUS_CHIP,
                     fill=self.palette["PAGE_BG"],
                     outline=self.palette["BORDER"])
        rounded_rect(self, x + 5, y + 5, x + size - 5, y + size - 5, 4,
                     fill=self.palette["PANEL_BG"], outline="")
        rounded_rect(self, x + 10, y + 11, x + size - 10, y + 16, 2.5,
                     fill=self.palette["ACCENT"], outline="")
        rounded_rect(self, x + 10, y + 21, x + size - 14, y + 25, 1.5,
                     fill=self.palette["TEXT"], outline="")
        rounded_rect(self, x + 10, y + 29, x + size - 20, y + 33, 1.5,
                     fill=self.palette["MUTED"], outline="")

        if self.selected:
            self.create_text(width - 18, 17, text="✓", fill=ACCENT,
                             font=font(10, True))
        self.create_text(66, height / 2 - 9, anchor="w", text=self.label,
                         fill=TEXT, font=font(10, True))
        self.create_text(66, height / 2 + 10, anchor="w",
                         text="深色配色" if self.dark else "浅色配色",
                         fill=MUTED, font=font(9))


class ScrollArea(tk.Frame):
    """带细滚动条的容器，内容都塞进 .inner。

    滚动条是"要用才出现"的：内容装得下就收起来，装不下才摆出来。小对话框
    用不着一条空滚条占着右边。

    auto_size=True 时，画布的**请求**尺寸跟着内容的自然尺寸走：对话框没自己
    定 geometry，收放就刚好包住正文，不会因为画布默认的 378×max_height 而
    空出一大块。只改请求值——外层把窗口拖大拖小时，pack expand 照样说了算。
    """

    # bg 不能写成 bg=PAGE_BG：默认参数在 import 那一刻就定死了，换主题时
    # theme.apply 重绑的是模块里的名字，改不动已经存进默认参数里的那个值。
    def __init__(self, parent, max_height, bg=None, auto_size=False):
        bg = PAGE_BG if bg is None else bg
        super().__init__(parent, bg=bg)
        self.max_height = max_height
        self.auto_size = auto_size
        self.canvas = tk.Canvas(self, height=max_height, bg=bg,
                                highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical",
                                 style="Slim.Vertical.TScrollbar",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self._syncing_bar = False

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    def _on_inner_resize(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.auto_size:
            # 高度照内容的自然高度要，不拿 max_height 封顶：大屏上整页摊开
            # 比框着 600 再看滚动条舒服；小屏由 fit_window 按屏幕收，画布被
            # pack 挤窄之后滚动条自然就出来了。
            want_w = max(self.inner.winfo_reqwidth(), 1)
            want_h = max(self.inner.winfo_reqheight(), 1)
            if want_w != self.canvas.winfo_reqwidth():
                self.canvas.configure(width=want_w)
            if want_h != self.canvas.winfo_reqheight():
                self.canvas.configure(height=want_h)
        self.sync_bar()

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        self.sync_bar()

    def sync_bar(self):
        """内容装得下就把滚动条收起来，装不下再摆出来。

        收放会改画布宽度，画布一改又转回这儿——拿 _syncing_bar 挡一下，别转圈。
        """
        if self._syncing_bar:
            return
        self._syncing_bar = True
        try:
            need = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1
            shown = bool(self.bar.winfo_manager())
            if need and not shown:
                self.bar.pack(side="right", fill="y")
            elif not need and shown:
                self.bar.pack_forget()
        except tk.TclError:
            pass
        finally:
            self._syncing_bar = False

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()

    def fit(self):
        """把高度收到内容那么多，超过 max_height 才出现滚动条。"""
        self.update_idletasks()
        self.canvas.configure(height=min(self.inner.winfo_reqheight(), self.max_height))
        self.sync_bar()

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def scroll(self, steps):
        self.canvas.yview_scroll(steps, "units")

    def see(self, widget):
        """把某一行滚进可视范围。上下键换选中时用。"""
        self.update_idletasks()
        total = self.inner.winfo_height()
        if total <= 0:
            return
        top = widget.winfo_y()
        bottom = top + widget.winfo_height()
        view_top = self.canvas.yview()[0] * total
        room = self.canvas.winfo_height()
        if top < view_top:
            self.canvas.yview_moveto(top / total)
        elif bottom > view_top + room:
            self.canvas.yview_moveto(max(bottom - room, 0) / total)


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
    """建一个统一样式的对话框，返回可往里放控件的 body 框架。

    **正文放进可滚动的区域，按钮先钉在底部**：安装、托管、模型这几扇表单
    内容能高到 900 像素，768 高的本子上摆不下——以前整个窗口被内容撑到屏幕
    外，底下「开始装」那排按钮直接看不见，用户连命令都跑不了。现在的分工是：
    窗口按屏幕收拢，正文超出的部分靠滚，按钮永远在窗口底边。

    底部占位框在正文之前 pack：pack 从上往下分地盘，正文后摆、又带 expand，
    先摆的底部那排才能把它的位置留住。
    """
    dialog.title(title)
    dialog.resizable(True, True)
    dialog.configure(bg=PAGE_BG)
    dialog.transient(dialog.master)
    theme.titlebar(dialog)

    tk.Label(dialog, text=title, bg=PAGE_BG, fg=TEXT,
             font=font(13, True), anchor="w").pack(fill="x", padx=20, pady=(16, 10))
    holder = tk.Frame(dialog, bg=PAGE_BG)
    holder.pack(side="bottom", fill="x", padx=20, pady=(12, 16))
    dialog._form_foot = holder

    area = ScrollArea(dialog, max_height=600, bg=PAGE_BG, auto_size=True)
    area.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 6))
    return area.inner


def finish_form(dialog, buttons):
    """在对话框底部摆按钮，buttons 是 [(文字, 回调, 是否主按钮), ...]。"""
    holder = getattr(dialog, "_form_foot", None)
    if holder is None:
        holder = tk.Frame(dialog, bg=PAGE_BG)
        holder.pack(fill="x", padx=20, pady=(16, 16))
    for text, command, primary in buttons:
        PillButton(holder, text, command, primary=primary,
                   bg=PAGE_BG).pack(side="right", padx=(8, 0))


def anchor_point(win, anchor=None):
    """父窗的中心点；父窗没显示或者压根没给，就退回屏幕中间偏上。

    用 winfo_viewable 而不是 winfo_exists：收起的窗口还在，但拿它当锚点，对话框
    会摆到看不见的地方去。
    """
    if anchor is not None:
        try:
            if anchor.winfo_exists() and anchor.winfo_viewable():
                return (anchor.winfo_rootx() + anchor.winfo_width() // 2,
                        anchor.winfo_rooty() + anchor.winfo_height() // 2)
        except tk.TclError:
            pass
    return (win.winfo_vrootx() + win.winfo_vrootwidth() // 2,
            win.winfo_vrooty() + win.winfo_vrootheight() // 3)


def frame_insets(win):
    """窗口外框比客户区多出来的那点尺寸（左右边框合起来、标题栏加底边）。

    **为什么必须扣这个**：Tk 的 geometry 里，位置 +x+y 指的是**外框**左上角，
    宽高 x y 指的却是**客户区**。只拿客户区尺寸去跟工作区比，算出来的 y 会让
    整扇窗口往下多出标题栏那 30 多像素——1080p 上刚好压进任务栏，768 高的
    本子上就是底边一排按钮落在屏幕外。

    量的是 win 自己；它还没映射（刚建出来）就问父窗的顶层——同一套系统边框，
    数值一样。都拿不到就按 0 算（Linux/macOS 那边 Tk 给的就是客户区）。

    **只用 Tk 自己的坐标**，不去调 Win32：探针会在同一个 ctypes 句柄上改
    GetWindowRect 的参数类型（它们要按 wintypes.RECT 收），跟这儿抢起来就是
    一句 ArgumentError。顶层窗口的 winfo_x/y 是外框左上角，winfo_rootx/rooty
    是客户区左上角，两个一减，左边框和标题栏就都出来了。
    """
    candidates = [win]
    master = getattr(win, "master", None)
    if master is not None:
        try:
            candidates.append(master.winfo_toplevel())
        except tk.TclError:
            pass
    for candidate in candidates:
        is_mapped = getattr(candidate, "winfo_ismapped", None)
        if is_mapped is None:
            continue
        try:
            if not is_mapped():
                continue
            left = candidate.winfo_rootx() - candidate.winfo_x()
            top = candidate.winfo_rooty() - candidate.winfo_y()
        except (AttributeError, tk.TclError):
            continue
        if left >= 0 and top >= 0 and (left or top):
            # 左右各一条边框，高度是标题栏加上下面那条边框。
            return 2 * left, top + left
    return 0, 0


def fit_window(win, want_w, want_h, anchor=None, min_w=1, min_h=1, margin=0):
    """把顶层窗口收进屏幕工作区，再摆到 anchor 中间。

    小屏、高 DPI、副屏都走这一套算术：先拿离 anchor 最近那块屏的工作区（已经
    去掉任务栏），按**外框**把尺寸封顶（见 frame_insets），再居中；位置越界就
    贴边（geometry.fit_rect）。margin 是窗口四周至少留出的空档，免得贴着屏幕
    边、跟任务栏混在一起。

    屏幕比 min_w/min_h 还小时，让位给屏幕：这时窗口会比界面自己定的下限还小，
    但总比有一截永远够不着强——正文都做成了可滚动的，挤一挤还能用。返回最终
    (x, y, w, h)，是客户区的尺寸。

    anchor 传父窗（Tk/Toplevel）。这样设置窗里开的对话框落回设置窗那块屏，
    不会跑到主窗所在的另一块屏去。
    """
    from claude_tool.geometry import fit_rect
    from claude_tool.host import work_area

    ax, ay = anchor_point(win, anchor)
    left, top, right, bottom = work_area(win, ax, ay)
    inset_w, inset_h = frame_insets(win)
    room_w = max(right - left - 2 * margin - inset_w, 1)
    room_h = max(bottom - top - 2 * margin - inset_h, 1)
    floor_w = min(max(int(min_w), 1), room_w)
    floor_h = min(max(int(min_h), 1), room_h)
    w = min(max(int(want_w), floor_w), room_w)
    h = min(max(int(want_h), floor_h), room_h)

    # 位置说的是外框左上角，把外框整个夹进扣掉 margin 的工作区。
    outer_w, outer_h = w + inset_w, h + inset_h
    inner = (left + margin, top + margin, right - margin, bottom - margin)
    x = ax - outer_w // 2
    y = ay - outer_h // 3
    x, y, _w, _h = fit_rect(x, y, outer_w, outer_h, inner,
                            floor_w + inset_w, floor_h + inset_h)
    # Tk 的 "+-100" 就是 x=-100（副屏在主屏左边时用得上），三个平台的 WM 都认。
    win.geometry("{}x{}+{}+{}".format(w, h, x, y))
    return x, y, w, h


# ── 主窗口 ────────────────────────────────────────────────────────────────
