"""左栏：工作区导航。

选中和工作是同一件事——左栏高亮的那一行就是「当前工作区」，插件下一版从这儿读。
所以这个状态放这儿，不放主窗：谁渲染谁持有。

从 launcher.py 里按域切出来的一块（见 设计说明-0.4.md 第一节）。**行上的动作不在
这儿**：0.4 把「改名/搬迁/移除/↑↓/删交接文档」挪进了右栏（ui/detail.py），这一栏
只管"有哪些文件夹、当前盯着哪一本"。

左栏是定宽的：它是导航，不该跟着窗口变宽——省下来的横向空间全给右栏的详情。
"""
import tkinter as tk

from claude_tool.config import save_config
from claude_tool.theme import MUTED, PAGE_BG, TEXT, font
from claude_tool.widgets import PillButton, Row, ScrollArea, make_entry


# 左栏宽度。定死，不参与 _apply_min_size 的"按内容量"那套——见上面那段。
NAV_WIDTH = 240
# ScrollArea.fit() 的封顶值。这不是"最多显示这么高"，是"内容高过它才出滚动条"：
# 左栏在 pack 里是 fill="both" + expand，真正多高由布局分配，这里给一个够大的
# 数就行，免得 fit() 把 canvas 的请求高度掐到比内容还矮。
NAV_MAX = 1200


class NavMixin:

    def _build_nav(self, parent):
        # 底下那条必须**先** pack：pack 是按调用顺序抢位置的，先摆底部这条，
        # 后面 expand 的列表才会把剩下的空间全吃掉而不压到它。
        self.rail_footer = tk.Frame(parent, bg=PAGE_BG)
        self.rail_footer.pack(side="bottom", fill="x", pady=(0, 12))
        # 列表级的两个动作（不是某一本上的动作）跟列表同栏摆着最顺：加一本、
        # 重扫一遍，说的都是"这张列表里有什么"。
        #
        # 分两行：240 宽摆不下「＋ 添加工作区」和「重新扫描」并排（量出来 194
        # 像素，只剩十几个像素余量，换个字体就顶出去了）。
        first = tk.Frame(self.rail_footer, bg=PAGE_BG)
        first.pack(fill="x")
        PillButton(first, "＋ 添加工作区", self._open_add_workspace_picker,
                   bg=PAGE_BG).pack(side="left")
        second = tk.Frame(self.rail_footer, bg=PAGE_BG)
        second.pack(fill="x", pady=(6, 0))
        PillButton(second, "重新扫描", self.rescan, bg=PAGE_BG).pack(side="left")
        # 设置入口只此一处（右栏和顶栏都不再重复），见 设计说明-0.4.md 第二节。
        PillButton(second, "⚙ 设置", self.open_settings, bg=PAGE_BG,
                   ).pack(side="right")
        # 插件入口单独占一行：240 宽下把「插件」塞进上面任何一行都顶出去了
        # （「重新扫描」和「⚙ 设置」那一行实测已经快贴边，见本方法开头那段）。
        third = tk.Frame(self.rail_footer, bg=PAGE_BG)
        third.pack(fill="x", pady=(6, 0))
        PillButton(third, "插件", self.open_plugins, bg=PAGE_BG).pack(side="left")

        head = tk.Frame(parent, bg=PAGE_BG)
        head.pack(fill="x", pady=(16, 6))
        tk.Label(head, text="工作区", bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        box = tk.Frame(parent, bg=PAGE_BG)
        box.pack(fill="x", pady=(0, 6))
        tk.Label(box, text="筛选", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(2, 6))
        # 变量还是 ws_filter：Ctrl+F 那条快路（_focus_filter）和 refresh_workspaces
        # 都指着它，名字不能改。
        self.filter_entry = make_entry(box, self.ws_filter, width=10)
        self.filter_entry.pack(side="left", fill="x", expand=True)

        self.nav_area = ScrollArea(parent, max_height=NAV_MAX)
        # 别名：老代码和探针里到处是 self.ws_list（_render_running / SessionMixin
        # 的 fit()、_probe_move 数行数），含义没变——"工作区那张列表"。head 是
        # _build_section 约定的锚点，那边不再有工作区区，这里自己挂一份。
        self.ws_list = self.nav_area
        self.nav_area.head = head
        self.nav_area.pack(fill="both", expand=True)

    # ── 当前工作区 ──

    def select_workspace(self, path):
        """选中某一本。记住它，右栏跟着换，别的不干。

        刻意不在这里开 claude——以前点一下就直接开会话，那样插件没法"先看着这
        一本再决定"。开会话是右栏那个按钮的事。

        path 传 None 表示"清掉选中"（工作区被删空、或者旧配置里没这个键时走
        这儿），不是错。
        """
        self.selected_path = path
        self.config_data["selected_workspace"] = path
        save_config(self.config_data)
        self._render_nav_rows()
        self.refresh_detail()
        # 当前选中那本变了，通知订阅了工作区的插件（没插件就是个空操作）。
        self._notify_plugin_workspace()

    def selected_entry(self):
        """当前选中的工作区 dict，没有就 None。

        只认配置里那一条——目录被删了也照样返回它的 dict（右栏要显示"目录不存在"
        这类状态）。真正"这条已经不在列表里"的情况由 _sync_selection 兜。
        """
        if not self.selected_path:
            return None
        for item in self.config_data["workspaces"]:
            if item["path"] == self.selected_path:
                return item
        return None

    def _sync_selection(self):
        """选中那本要是已经从列表里没了，退到第一条。

        跑在 refresh_workspaces 里而不是只在启动那一次：工作区是能被移除、改名、
        搬迁的，选中态得跟着走，不然右栏会一直显示一个不存在的路径。

        认的是**全量**列表，不是筛完的 ws_view —— 筛选框敲一个字就让选中跳到
        另一本，那是另一回事，不是这里的活。
        """
        workspaces = self.config_data["workspaces"]
        if self.selected_path and any(w["path"] == self.selected_path
                                      for w in workspaces):
            return
        fallback = workspaces[0]["path"] if workspaces else None
        if fallback == self.selected_path:
            return
        self.selected_path = fallback
        self.config_data["selected_workspace"] = fallback
        save_config(self.config_data)

    # ── 画 ──

    def _render_nav_rows(self):
        """按 ws_view 重画左栏。选中那行高亮。

        行上只有三样：Ctrl 编号、名字、路径。原来挂在行右侧那行小字（有交接
        文档 / 上次聊多久）挪去右栏了——240 宽下它跟标题抢地方，标题会被截成
        两三个字。
        """
        for child in self.nav_area.inner.winfo_children():
            child.destroy()
        for position, item in enumerate(self.ws_view):
            picked = item["path"] == self.selected_path
            row = Row(self.nav_area.inner, title=item["name"], subtitle=item["path"],
                      active=picked,
                      # 行号就是 Ctrl+行号。9 以后没有号（按不到），但位置留着，
                      # 免得后几行的标题跟前面错开一格。
                      badge=str(position + 1) if position < 9 else "",
                      on_click=lambda it=item: self.select_workspace(it["path"]),
                      )
            row.pack(fill="x", pady=3)
            # 右键菜单留给插件（workspace_row 挂载点）：针对这一行，不一定先选中
            # 它。没有插件挂这个点就不弹（见 ui/plugins.py 的 _plugin_row_menu）。
            row.bind("<Button-3>", lambda e, it=item: self._plugin_row_menu(e, it))
        self.nav_area.fit()

    def _render_nav_hint(self, title, body):
        """列表空着/筛不出来时左栏那块引导语。"""
        for child in self.nav_area.inner.winfo_children():
            child.destroy()
        tk.Label(self.nav_area.inner, text=title, bg=PAGE_BG, fg=TEXT,
                 font=font(10, True), justify="left", anchor="w",
                 wraplength=NAV_WIDTH - 40,
                 ).pack(anchor="w", padx=6, pady=(12, 3))
        tk.Label(self.nav_area.inner, text=body, bg=PAGE_BG, fg=MUTED,
                 font=font(9), justify="left", anchor="w",
                 wraplength=NAV_WIDTH - 40,
                 ).pack(anchor="w", padx=6, pady=(0, 9))
        self.nav_area.fit()
