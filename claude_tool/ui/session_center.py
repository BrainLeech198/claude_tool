"""会话中心：把所有工作区的会话摊在一条时间线上找、看、接着聊。

右栏那份列表只管"当前选中的这一本"，翻另一本就得先切左边。要找回一句"前几
天在别处聊过的话"时，那两步都是多余的，所以有了这扇全屏窗口（Ctrl+K 开关）。

这一版只做四件事：全局搜索、跨工作区时间线、续聊前预览、接着聊。收藏和标签
没做——真要加的时候，写进会话 jsonl 末尾（跟改名同一套路），不另存配置。

数据仍然只读 CLI 落盘的 jsonl（见 history.timeline / search / preview），这扇窗
不维护自己的索引：会话文件是唯一真源。
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

from claude_tool import agents, history, theme
from claude_tool.config import humanize_ago
from claude_tool.theme import (
    ACCENT,
    BORDER,
    MUTED,
    PAGE_BG,
    PANEL_BG,
    TEXT,
    font,
)
from claude_tool.widgets import PillButton, Row, ScrollArea, fit_window, measure

# 左边一次最多摆多少条。timeline 本身按每个工作区 200 条聚合，这里不再二次
# 截断——搜索是在整份时间线上做的，摆多少就搜多少，免得"搜得到却找不到"。
ROW_H = 56
PANEL_W = 430


def _day_label(stamp):
    """时间戳 -> 「今天」「昨天」「2026-10-18」这种分组标题。"""
    now = time.localtime()
    then = time.localtime(stamp)
    if (now.tm_year, now.tm_yday) == (then.tm_year, then.tm_yday):
        return "今天"
    yesterday = time.localtime(time.time() - 86400)
    if (yesterday.tm_year, yesterday.tm_yday) == (then.tm_year, then.tm_yday):
        return "昨天"
    if now.tm_year == then.tm_year:
        return time.strftime("%m 月 %d 日", then)
    return time.strftime("%Y-%m-%d", then)


class SessionCenter(tk.Toplevel):
    """一扇覆盖几乎整个屏幕的会话窗口。

    `app` 是主窗口（Launcher）：续聊、找已经开着的窗口、改名/删除都沿用主窗
    那套动作，这里只负责把它们摆到眼前。
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("会话中心")
        self.configure(bg=PAGE_BG)
        self.transient(app)
        theme.titlebar(self)

        # 按整屏算出来的大小再收进工作区：任务栏、屏幕缩放和副屏都交给
        # fit_window 一道处理，不再自己算 x/y。
        width = max(780, min(1440, self.winfo_screenwidth() - 120))
        height = max(560, min(1020, self.winfo_screenheight() - 90))
        _x, _y, width, height = fit_window(
            self, width, height, anchor=app, min_w=780, min_h=540, margin=24)
        # 下限跟着实际收下来的尺寸走：小屏上 fit_window 会把窗口压到屏幕能放下
        # 的大小，这时候再硬钉 780×540，窗口又被 minsize 顶出去一截。
        self.minsize(min(780, width), min(540, height))

        self.mode = "simple"
        self.query = tk.StringVar()
        self.sessions = []
        self.matches = []
        # 上一次搜索的完整结果。筛选条件变了只需在这份上重筛，不用再扫一次全文。
        self._raw = []
        self.favorite_only = False
        self.active_tags = []
        self.index = -1
        self._rows = []
        self._filter_width = 0
        self._debounce = None
        self._scan_id = 0
        self._scan_queue = queue.Queue()
        self._scanning = False
        self._deep_key = None

        self._build()
        self.reload()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", self._on_escape)
        self.after(30, self.search_entry.focus_set)
        self.query.trace_add("write", lambda *_: self._queue_search())

    # ── 界面骨架 ──

    def _build(self):
        head = tk.Frame(self, bg=PAGE_BG)
        head.pack(fill="x", padx=24, pady=(18, 10))
        tk.Label(head, text="会话中心", bg=PAGE_BG, fg=TEXT,
                 font=font(17, True)).pack(side="left")
        self.total_var = tk.StringVar()
        tk.Label(head, textvariable=self.total_var, bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(10, 0))
        tk.Label(head, text="Ctrl+K 开关 · Esc 清空 / 关闭", bg=PAGE_BG,
                 fg=MUTED, font=font(9)).pack(side="right")

        search = tk.Frame(self, bg=PAGE_BG)
        search.pack(fill="x", padx=24)
        entry = tk.Entry(search, textvariable=self.query, font=font(13),
                         bg=PANEL_BG, fg=TEXT, relief="flat", insertbackground=TEXT,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.search_entry = entry
        entry.bind("<Return>", self._on_return)
        entry.bind("<Down>", lambda _e: self._move(1))
        entry.bind("<Up>", lambda _e: self._move(-1))

        self.mode_row = tk.Frame(search, bg=PAGE_BG)
        self.mode_row.pack(side="left", padx=(10, 0))
        self._render_modes()

        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").pack(fill="x", padx=26, pady=(6, 0))

        body = tk.Frame(self, bg=PAGE_BG)
        body.pack(fill="both", expand=True, padx=24, pady=(8, 18))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(2, weight=0, minsize=PANEL_W)

        left = tk.Frame(body, bg=PAGE_BG)
        left.grid(row=0, column=0, sticky="nsew")
        # 筛选条不跟时间线一起滚：收藏开关和标签胶囊得一直摆在眼前。
        self.filters = tk.Frame(left, bg=PAGE_BG)
        self.filters.pack(fill="x", pady=(0, 8))
        self.favorite_row = tk.Frame(self.filters, bg=PAGE_BG)
        self.favorite_row.pack(fill="x")
        self.chips_frame = tk.Frame(self.filters, bg=PAGE_BG)
        self.chips_frame.pack(fill="x", pady=(6, 0))
        self.chips_frame.bind("<Configure>", self._on_chips_resize)

        self.area = ScrollArea(left, max_height=10000)
        self.area.pack(fill="both", expand=True)

        tk.Frame(body, bg=BORDER, width=1).grid(row=0, column=1, sticky="ns",
                                                padx=(14, 14))

        right = tk.Frame(body, bg=PAGE_BG)
        right.grid(row=0, column=2, sticky="nsew")
        self.preview_title_var = tk.StringVar()
        self.preview_meta_var = tk.StringVar()
        tk.Label(right, textvariable=self.preview_title_var, bg=PAGE_BG, fg=TEXT,
                 font=font(14, True), anchor="w", justify="left",
                 wraplength=PANEL_W - 10).pack(fill="x")
        tk.Label(right, textvariable=self.preview_meta_var, bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w", justify="left",
                 wraplength=PANEL_W - 10).pack(fill="x", pady=(4, 0))

        # 搜索命中时把命中的那句话也摆出来：光看标题，用户不知道为什么会搜到它。
        self.match_var = tk.StringVar()
        self.match_label = tk.Label(right, textvariable=self.match_var, bg=PAGE_BG,
                                    fg=ACCENT, font=font(9), anchor="w",
                                    justify="left", wraplength=PANEL_W - 10)
        self.tags_var = tk.StringVar()
        self.tags_label = tk.Label(right, textvariable=self.tags_var, bg=PAGE_BG,
                                   fg=MUTED, font=font(9), anchor="w",
                                   justify="left", wraplength=PANEL_W - 10)

        self.actions_row = tk.Frame(right, bg=PAGE_BG)
        self.actions_row.pack(fill="x", pady=(10, 8))
        self.continue_btn = PillButton(self.actions_row, "接着聊", self._continue,
                                       primary=True, bg=PAGE_BG, height=30)
        self.continue_btn.pack(side="left")
        self.favorite_btn = PillButton(self.actions_row, "☆ 收藏",
                                       self._toggle_favorite, bg=PAGE_BG, height=30)
        self.favorite_btn.pack(side="left", padx=(6, 0))
        self.tags_btn = PillButton(self.actions_row, "标签", self._edit_tags,
                                   bg=PAGE_BG, height=30)
        self.tags_btn.pack(side="left", padx=(6, 0))
        self.rename_btn = PillButton(self.actions_row, "改名", self._rename,
                                     bg=PAGE_BG, height=30)
        self.rename_btn.pack(side="left", padx=(6, 0))
        self.delete_btn = PillButton(self.actions_row, "删除", self._delete,
                                     bg=PAGE_BG, height=30)
        self.delete_btn.pack(side="left", padx=(6, 0))

        self.preview_area = ScrollArea(right, max_height=10000)
        self.preview_area.pack(fill="both", expand=True)

    def _render_modes(self):
        for child in self.mode_row.winfo_children():
            child.destroy()
        for caption, value in (("简单", "simple"), ("深度", "deep")):
            button = PillButton(self.mode_row, caption,
                                lambda v=value: self.set_mode(v),
                                primary=self.mode == value, bg=PAGE_BG, height=30)
            button.pack(side="left", padx=(6, 0))

    # ── 读数据 / 搜索 ──

    def reload(self):
        """重读所有工作区的会话，把时间线整个重建一遍（打开窗口时走这条）。"""
        workspaces = self.app.config_data.get("workspaces") or []
        self.sessions = history.timeline(workspaces)
        # 之前选中的标签可能随着会话删掉/改标签而消失，先剔掉再画筛选条。
        pool = set(self._tag_pool())
        self.active_tags = [tag for tag in self.active_tags if tag in pool]
        self.total_var.set("共 {} 条会话 · {} 个工作区".format(
            len(self.sessions), len(workspaces)))
        self._render_filters()
        self.run_search()

    def set_mode(self, mode):
        if mode not in ("simple", "deep") or mode == self.mode:
            return
        self.mode = mode
        self._deep_key = None
        self._render_modes()
        if mode == "deep" and self.query.get().strip():
            # 深度模式不跟着打字每一下都扫全文；先摆着已有的结果，等用户回车。
            self.status_var.set("深度搜索：回车扫全文")
        else:
            self.run_search()

    def run_search(self, deep=None):
        """按当前关键词筛一遍。deep=None 时跟着模式走。"""
        if self._debounce is not None:
            try:
                self.after_cancel(self._debounce)
            except tk.TclError:
                pass
            self._debounce = None
        deep = (self.mode == "deep") if deep is None else deep
        keyword = self.query.get().strip()
        self._scan_id += 1
        token = self._scan_id
        self._scanning = False

        if not keyword:
            self._raw = list(self.sessions)
            self.matches = self._filtered(self._raw)
            self._deep_key = None
            self._render_timeline()
            self.status_var.set(self._status_text())
            return

        self._deep_key = (keyword, deep)
        if deep:
            self._scanning = True
            self.status_var.set("正在扫全文 0/{}…".format(len(self.sessions)))
            threading.Thread(target=self._deep_worker,
                             args=(token, keyword), daemon=True).start()
            self.after(80, lambda: self._poll_scan(token))
            return

        self._raw = history.search(self.sessions, keyword)
        self.matches = self._filtered(self._raw)
        self._render_timeline()
        self.status_var.set(self._status_text())

    def _deep_worker(self, token, keyword):
        def progress(done, total):
            self._scan_queue.put(("progress", token, (done, total)))

        try:
            found = history.search(self.sessions, keyword, deep=True,
                                   progress=progress)
        except Exception as error:   # 外部数据；读到坏文件别让窗口卡在"正在扫"
            self._scan_queue.put(("error", token, str(error)))
            return
        self._scan_queue.put(("done", token, found))

    def _poll_scan(self, token):
        if token != self._scan_id or not self.winfo_exists():
            return
        finished = False
        while True:
            try:
                kind, seen, payload = self._scan_queue.get_nowait()
            except queue.Empty:
                break
            if seen != token:
                continue
            if kind == "progress":
                done, total = payload
                self.status_var.set("正在扫全文 {}/{}…".format(done, total))
            elif kind == "done":
                self._scanning = False
                self._raw = payload
                self.matches = self._filtered(payload)
                self._render_timeline()
                self.status_var.set(
                    self._status_text() + "（深度）")
                finished = True
            elif kind == "error":
                self._scanning = False
                self.status_var.set("扫到一半出错了：{}".format(payload))
                finished = True
        if not finished:
            self.after(80, lambda: self._poll_scan(token))

    def _queue_search(self):
        """打字时延迟一下再筛，别每敲一个字就重建整条时间线。"""
        if self._debounce is not None:
            self.after_cancel(self._debounce)
            self._debounce = None
        if self.mode == "deep":
            self.status_var.set("深度搜索：打完按回车扫全文")
            return
        self._debounce = self.after(180, self._run_simple)

    def _run_simple(self):
        self._debounce = None
        self.run_search(deep=False)

    # ── 筛选：收藏 + 标签 ──

    def _tag_pool(self):
        """所有会话用到的标签，按用得多的排前面。"""
        counts = {}
        for session in self.sessions:
            for tag in session.get("tags") or ():
                counts[tag] = counts.get(tag, 0) + 1
        return sorted(counts, key=lambda tag: (-counts[tag], tag))

    def _passes(self, session):
        if self.favorite_only and not session.get("favorite"):
            return False
        # 多选标签是"同时具备"，不是"命中任意一个"——筛选是用来收窄的。
        if self.active_tags:
            owned = set(session.get("tags") or ())
            if not set(self.active_tags) <= owned:
                return False
        return True

    def _filtered(self, sessions):
        return [session for session in sessions if self._passes(session)]

    def _status_text(self):
        bits = []
        if self.favorite_only:
            bits.append("只看收藏")
        if self.active_tags:
            bits.append("#" + " #".join(self.active_tags))
        note = " · " + "、".join(bits) if bits else ""
        if self.query.get().strip():
            return "找到 {} 条{}".format(len(self.matches), note)
        return "共 {} 条会话{}".format(len(self.matches), note)

    def _render_filters(self):
        """重画收藏开关和标签胶囊。筛选状态变了就整排重建，省得逐个改底色。"""
        for child in self.favorite_row.winfo_children():
            child.destroy()
        PillButton(self.favorite_row, "★ 只看收藏",
                   self._toggle_favorite_only, primary=self.favorite_only,
                   bg=PAGE_BG, height=24).pack(side="left")
        if self.favorite_only or self.active_tags:
            PillButton(self.favorite_row, "清除筛选", self._clear_filters,
                       bg=PAGE_BG, height=24).pack(side="left", padx=(6, 0))
        tk.Label(self.favorite_row, text="标签可多选（同时具备）", bg=PAGE_BG,
                 fg=MUTED, font=font(9)).pack(side="right")
        self._render_tag_chips()

    def _render_tag_chips(self):
        for child in self.chips_frame.winfo_children():
            child.destroy()
        pool = self._tag_pool()
        if not pool:
            tk.Label(self.chips_frame, text="还没有标签；贴一个，之后就能按标签筛。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                     ).pack(fill="x")
            return
        width = self._filter_width if self._filter_width > 100 else 700
        line = tk.Frame(self.chips_frame, bg=PAGE_BG)
        line.pack(fill="x")
        used = 0
        for tag in pool:
            chip_w = measure("#" + tag, 10) + 28
            if used and used + chip_w > width:
                line = tk.Frame(self.chips_frame, bg=PAGE_BG)
                line.pack(fill="x", pady=(6, 0))
                used = 0
            PillButton(line, "#" + tag, lambda t=tag: self._toggle_tag(t),
                       primary=tag in self.active_tags, bg=PAGE_BG,
                       height=24).pack(side="left", padx=(0, 6))
            used += chip_w + 6

    def _on_chips_resize(self, event):
        # 窗口拉伸后重新换行；宽度没实质变化就跳过，免得自己触发自己。
        if abs(event.width - self._filter_width) > 24:
            self._filter_width = event.width
            self._render_tag_chips()

    def _toggle_favorite_only(self):
        self.favorite_only = not self.favorite_only
        self._render_filters()
        self._refilter()

    def _toggle_tag(self, tag):
        if tag in self.active_tags:
            self.active_tags.remove(tag)
        else:
            self.active_tags.append(tag)
        self._render_filters()
        self._refilter()

    def _clear_filters(self):
        self.favorite_only = False
        self.active_tags = []
        self._render_filters()
        self._refilter()

    def _refilter(self):
        """筛选条件变了：在上一份搜索结果上重筛，不重读文件。"""
        self.matches = self._filtered(self._raw)
        self._render_timeline()
        self.status_var.set(self._status_text())

    # ── 时间线 ──

    def _render_timeline(self):
        previous = self.current()
        keep = (previous["file"], previous["id"]) if previous else None
        self.area.clear()
        self._rows = []
        day = None
        for index, session in enumerate(self.matches):
            label = _day_label(session["updated"])
            if label != day:
                day = label
                tk.Label(self.area.inner, text=label, bg=PAGE_BG, fg=MUTED,
                         font=font(9, True), anchor="w",
                         ).pack(fill="x", padx=4, pady=(12 if self._rows else 0, 4))
            row = Row(self.area.inner,
                      title=("★ " if session.get("favorite") else "") + session["title"],
                      subtitle=self._subtitle(session), height=ROW_H,
                      agent=session["agent"],
                      tags=session.get("tags") or (),
                      on_click=lambda i=index: self.select(i))
            row.pack(fill="x", pady=(0, 2))
            self._rows.append(row)

        if not self.matches:
            if self.favorite_only or self.active_tags:
                empty = "没有符合筛选条件的会话"
            elif self.query.get().strip():
                empty = "没有匹配的会话"
            else:
                empty = "还没有会话记录"
            tk.Label(self.area.inner, text=empty, bg=PAGE_BG, fg=MUTED,
                     font=font(10), anchor="w").pack(fill="x", padx=4, pady=12)

        index = 0
        if keep is not None:
            for position, session in enumerate(self.matches):
                if (session["file"], session["id"]) == keep:
                    index = position
                    break
        self.index = -1
        if self.matches:
            self.select(index)
        else:
            self._render_preview()

    @staticmethod
    def _subtitle(session):
        workspace = session.get("workspace") or {}
        name = workspace.get("name") or workspace.get("path") or ""
        return "{} · {}".format(name, humanize_ago(session["updated"]))

    def current(self):
        if 0 <= self.index < len(self.matches):
            return self.matches[self.index]
        return None

    def select(self, index, scroll=False):
        if not self.matches:
            self.index = -1
            return
        index = max(0, min(len(self.matches) - 1, index))
        if index != self.index:
            if 0 <= self.index < len(self._rows):
                self._rows[self.index].set_active(False)
            self.index = index
            self._rows[index].set_active(True)
        if scroll and self._rows:
            self.area.see(self._rows[index])
        self._render_preview()

    def _move(self, delta):
        if self.matches:
            self.select(self.index + delta, scroll=True)
        return "break"

    # ── 右侧预览 ──

    def _render_preview(self):
        self.preview_area.clear()
        self.match_label.pack_forget()
        session = self.current()
        if session is None:
            self.preview_title_var.set("选一条会话")
            self.preview_meta_var.set("左边按天排着所有工作区最近聊过的东西。")
            self.match_var.set("")
            self.tags_var.set("")
            self.match_label.pack_forget()
            self.tags_label.pack_forget()
            for button in (self.continue_btn, self.favorite_btn, self.tags_btn,
                           self.rename_btn, self.delete_btn):
                button.set_enabled(False)
            tk.Label(self.preview_area.inner, text="选一条就能看到最近聊了什么。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                     wraplength=PANEL_W - 20, justify="left",
                     ).pack(fill="x", pady=6)
            return

        for button in (self.continue_btn, self.favorite_btn, self.tags_btn,
                       self.rename_btn, self.delete_btn):
            button.set_enabled(True)
        self.preview_title_var.set(
            ("★ " if session.get("favorite") else "") + session["title"])
        self.favorite_btn.set_text("★ 已收藏" if session.get("favorite") else "☆ 收藏")
        self.preview_meta_var.set("{} · {} · {}".format(
            (session["workspace"] or {}).get("name", ""),
            agents.get(session["agent"]).label,
            humanize_ago(session["updated"])))
        if session.get("match_text"):
            self.match_var.set("命中{}：{}".format(
                session.get("match_where", ""), session["match_text"]))
            self.match_label.pack(fill="x", pady=(6, 0), before=self.actions_row)
        else:
            self.match_var.set("")
            self.match_label.pack_forget()
        tags = session.get("tags") or []
        if tags:
            self.tags_var.set("标签：" + "  ".join("#" + tag for tag in tags))
            self.tags_label.pack(fill="x", pady=(6, 0), before=self.actions_row)
        else:
            self.tags_var.set("")
            self.tags_label.pack_forget()

        messages = history.preview(session["file"], count=8)
        for message in messages:
            card = tk.Frame(self.preview_area.inner, bg=PANEL_BG,
                            highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))
            is_user = message["role"] == "user"
            tk.Label(card, text="你" if is_user else agents.get(session["agent"]).label,
                     bg=PANEL_BG, fg=ACCENT if is_user else MUTED,
                     font=font(9, True), anchor="w").pack(
                         fill="x", padx=10, pady=(8, 0))
            tk.Label(card, text=self._clip(message["text"]), bg=PANEL_BG, fg=TEXT,
                     font=font(10), anchor="w", justify="left",
                     wraplength=PANEL_W - 30).pack(fill="x", padx=10, pady=(3, 9))
        if not messages:
            tk.Label(self.preview_area.inner, text="这条会话里还没有可预览的文字。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w").pack(fill="x")

    @staticmethod
    def _clip(text, limit=900):
        """单条消息太长时截一下：预览是让人认出来的，不是重读整段。"""
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n…（后面还有）"

    # ── 右侧那三颗按钮 ──

    def _continue(self):
        session = self.current()
        if session is None:
            return
        item = session["workspace"]
        running = self.app.running_session(item["path"], session["id"])
        self.close()
        if running is not None:
            self.app.focus_running(running)
        else:
            self.app.launch_workspace(item, cont=False, session_id=session["id"])

    def _rename(self):
        session = self.current()
        if session is None:
            return
        # owner 不传：改名框关上之后不该把这扇窗口变成 grab 状态。
        self.app.rename_session(session["workspace"], session,
                                on_done=lambda: self._after_change(session))

    def _after_change(self, session):
        if self.winfo_exists():
            # 搜索结果里那份是副本，标题改了要按文件同步回 sessions，
            # 否则清空搜索之后又变回旧名字。
            for bucket in (self.sessions, self.matches, self._raw):
                for item in bucket:
                    if item["file"] == session["file"]:
                        item["title"] = session["title"]
            self._render_timeline()
            self.status_var.set("改动已保存")

    def _toggle_favorite(self):
        session = self.current()
        if session is None:
            return
        item = session["workspace"]
        favorite = not session.get("favorite")
        saved = history.set_flags(item.get("agent"), item["path"], session["id"],
                                  favorite, session.get("tags") or [])
        if saved is None:
            messagebox.showwarning("没记上", "记录文件可能已经被别处挪走了。",
                                   parent=self)
            return
        self._sync_flags(session, favorite, saved)
        self.app.refresh_sessions()
        self.app.feedback_var.set(
            "收藏了「{}」。".format(session["title"]) if favorite
            else "取消收藏「{}」。".format(session["title"]))
        self._render_filters()
        self._refilter()

    def _edit_tags(self):
        session = self.current()
        if session is None:
            return
        self.app.edit_session_tags(session["workspace"], session,
                                   on_done=lambda: self._after_tags(session))

    def _after_tags(self, session):
        if not self.winfo_exists():
            return
        self._sync_flags(session, session.get("favorite"),
                         session.get("tags") or [])
        self._render_filters()
        self._refilter()

    def _sync_flags(self, session, favorite, tags):
        """同一会话在 sessions / matches / _raw 里各有一份（搜索结果是副本），
        按文件路径一起改，免得某个视图还留着旧状态。"""
        session["favorite"], session["tags"] = bool(favorite), list(tags)
        for bucket in (self.sessions, self.matches, self._raw):
            for item in bucket:
                if item["file"] == session["file"]:
                    item["favorite"], item["tags"] = bool(favorite), list(tags)

    def _delete(self):
        session = self.current()
        if session is None:
            return
        item = session["workspace"]
        if self.app.running_session(item["path"], session["id"]) is not None:
            messagebox.showinfo("还没关", "这条会话还开着，先关掉再删。", parent=self)
            return
        if not messagebox.askyesno(
                "删除会话",
                "删除「{}」？\n\n记录文件会直接删掉，不进回收站。".format(
                    session["title"]), parent=self):
            return
        if not history.delete_session(item.get("agent"), item["path"],
                                      session["id"]):
            messagebox.showwarning("没删成", "记录文件可能已经被别处挪走了。",
                                   parent=self)
            return
        # 按文件认，不按对象认：搜索结果是复制出来的 dict，跟 sessions 里那份
        # 不是同一个对象，用 is 判断会漏删背后那份。
        self.matches = [s for s in self.matches if s["file"] != session["file"]]
        self.sessions = [s for s in self.sessions if s["file"] != session["file"]]
        self._raw = [s for s in self._raw if s["file"] != session["file"]]
        self.app.refresh_sessions()
        self.app.feedback_var.set("删掉了会话「{}」。".format(session["title"]))
        # 这条可能带着筛选条上唯一的某个标签，池子要跟着重算。
        self._render_filters()
        self._refilter()
        self.status_var.set("删掉了「{}」".format(session["title"]))

    # ── 键盘 / 关闭 ──

    def _on_return(self, _event=None):
        keyword = self.query.get().strip()
        if (self.mode == "deep" and keyword
                and self._deep_key != (keyword, True)):
            self.run_search(deep=True)
        else:
            self._continue()
        return "break"

    def _on_escape(self, _event=None):
        if self.query.get():
            self.query.set("")
            self.run_search()
        else:
            self.close()
        return "break"

    def close(self):
        if self._debounce is not None:
            try:
                self.after_cancel(self._debounce)
            except tk.TclError:
                pass
            self._debounce = None
        # 还在跑的深度扫描不用等它：token 一变，回来的结果就没人接了。
        self._scan_id += 1
        self._scanning = False
        if getattr(self.app, "_session_center", None) is self:
            self.app._session_center = None
        self.destroy()


class SessionCenterMixin:
    """主窗口上的两个入口：打开会话中心、Ctrl+K 开关它。"""

    def open_session_center(self):
        window = getattr(self, "_session_center", None)
        if window is not None and window.winfo_exists():
            window.lift()
            window.focus_force()
            return window
        window = SessionCenter(self)
        self._session_center = window
        return window

    def toggle_session_center(self):
        window = getattr(self, "_session_center", None)
        if window is not None and window.winfo_exists():
            window.close()
            return
        self.open_session_center()
