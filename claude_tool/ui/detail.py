"""右栏：选中那本的详情和动作，以及插件挂载区。

左栏管"有哪些"，这里管"选中的这一本是什么、能对它做什么"。

动作分两排是有意的：**上排三个是日常动作**（新会话 / 接着上次 / 打开目录），
**下排是管理动作**（改名 / 搬迁 / 移除 / ↑↓ / 删交接文档）。0.4 之前这些全挤在
列表行的右侧，一行里排七个两字词，糊成一片；而且"移除"和"新会话"并排摆着，
手滑的代价完全不一样。

挂载区现在是空的（插件宿主是下一版的事），但位置先留出来 —— 见
设计说明-0.4.md 第五节，插件就挂这几处之一。
"""
import os
import tkinter as tk

from claude_tool.config import humanize_ago, last_chat_time
from claude_tool.handoff import HANDOFF_FILE
from claude_tool.theme import BORDER, HOVER_BG, MUTED, PAGE_BG, TEXT, font
from claude_tool.widgets import PillButton, Tip


# 右栏该有多宽。它不是"右栏就直接摆这么宽"——右栏是 fill="both" + expand，多宽
# 由布局给。这个数只有一个用处：算窗口下限（见 launcher._apply_min_size），保证
# 缩到最窄时【管理】那一排**六个**按钮还在同一行上。
#
# 实测（Task 8 量的，`_probe_size_state.py` 现在把这几条钉住了）：不含
# 「删交接文档」时那排要 324 像素；含它（也就是最宽状态）要 **441**。Task 6 那版
# 按估的按钮宽度写的是 390，实测下来最窄窗口里右栏只分到 442——比 441 多 1 像素，
# 实质上是在赌字体。现在按实测取 460（441 + 19 余量）。
#
# 另外注意别拿 `detail_area.winfo_reqwidth()` 当依据：右栏里有个显示工作区路径的
# Label，路径长一点那个数就飘（实测能到 601）。下限要的是这排按钮的宽度，那是个
# 不随数据变的数。
DETAIL_MIN_WIDTH = 460


class DetailMixin:

    def _build_detail(self, parent):
        # 顶上这条"本机是什么状况"：当前模型 + claude 版本 + 手动查一次。
        # 0.4 之前这三样长在顶栏上，可它们是"看着某一本的时候"才关心的信息
        # （设计说明第一节），挪进右栏正好——顶栏腾出来只留给"需要立刻知道"的
        # 更新胶囊。
        strip = tk.Frame(parent, bg=PAGE_BG)
        strip.pack(fill="x", pady=(16, 0))
        tk.Label(strip, text="当前模型", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(0, 8))
        self.status_var = tk.StringVar(value="未设置")
        # 模型名做成个小胶囊。有模型时用强调色，没设的时候是中性灰——
        # 空状态不该长得像报错或者链接。
        self.model_chip = tk.Label(strip, textvariable=self.status_var,
                                   bg=HOVER_BG, fg=MUTED, font=font(10, True),
                                   padx=10, pady=2)
        self.model_chip.pack(side="left")

        self.version_var = tk.StringVar(value="正在查 claude 版本…")
        tk.Label(strip, textvariable=self.version_var, bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(14, 0))
        # 手动问一次。设置窗里那个勾管的是"开启动器时自动问一次"，这颗按钮管的是
        # "我现在就想知道"，不受那个勾影响。
        check = PillButton(strip, "查更新", self.check_claude_update, bg=PAGE_BG)
        check.pack(side="left", padx=(10, 0))
        Tip(check, "问一次网上 claude 出到哪一版了，跟本机这个比一比")

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(14, 0))

        self.detail_title_var = tk.StringVar()
        self.detail_path_var = tk.StringVar()
        self.detail_meta_var = tk.StringVar()
        tk.Label(parent, textvariable=self.detail_title_var, bg=PAGE_BG,
                 fg=TEXT, font=font(15, True), anchor="w").pack(
            fill="x", pady=(14, 2))
        tk.Label(parent, textvariable=self.detail_path_var, bg=PAGE_BG,
                 fg=MUTED, font=font(9), anchor="w").pack(fill="x")
        tk.Label(parent, textvariable=self.detail_meta_var, bg=PAGE_BG,
                 fg=MUTED, font=font(9), anchor="w").pack(fill="x", pady=(10, 0))

        # 日常动作。三个都建成实例属性，没选中那本时要能一起置灰。
        self._detail_daily = []
        for caption, command, primary in (
                ("新会话", self._detail_new_session, True),
                ("接着上次", self._detail_continue, False),
                ("打开目录", self._detail_open_folder, False)):
            button = PillButton(parent, caption, command, primary=primary,
                                bg=PAGE_BG, height=30)
            self._detail_daily.append(button)
        self._pack_detail_daily()

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(18, 0))

        # 管理动作。顺序按"从轻到重"排：改名 → 搬迁 → 上下挪 → 移除 → 删交接
        # 文档。移除和删文档是最重的两个，摆最右边，离左边的日常动作最远。
        self._detail_manage = []
        manage = tk.Frame(parent, bg=PAGE_BG)
        manage.pack(fill="x", pady=(10, 0))
        tk.Label(manage, text="管理", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(0, 8))
        for caption, handler in (
                ("改名", self._detail_rename),
                ("搬迁", self._detail_move),
                ("↑", self._detail_up),
                ("↓", self._detail_down),
                ("移除", self._detail_remove)):
            button = PillButton(manage, caption, handler, bg=PAGE_BG, height=26)
            button.pack(side="left", padx=(0, 6))
            self._detail_manage.append(button)
        # 「删交接文档」平时不摆：这一本没有那份文件时按下去只会得到一句"没有"。
        # 有文档才 pack 出来，位置上永远排在最后一个（=最右边）。
        self._handoff_btn = PillButton(manage, "删交接文档", self._detail_remove_handoff,
                                       bg=PAGE_BG, height=26)

        # 插件挂载区：宿主自己的东西在上面，插件注册的排在下面这条分隔线之后
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(18, 0))
        self.detail_plugin_area = tk.Frame(parent, bg=PAGE_BG)
        self.detail_plugin_area.pack(fill="x", pady=(10, 0))

    def _pack_detail_daily(self):
        for index, button in enumerate(self._detail_daily):
            button.pack(side="left", padx=(0 if index == 0 else 6, 0))

    # ── 跟着选中态走 ──

    def refresh_detail(self):
        """按当前选中的那一本重画右栏。没选中就整片置灰，别留一堆按不动的按钮
        让人猜为什么按不动。"""
        item = self.selected_entry()
        if item is None:
            self.detail_title_var.set("还没选工作区")
            self.detail_path_var.set("左边点一下，或者先去设置里添加一个")
            self.detail_meta_var.set("")
            self._detail_set_enabled(False)
            return
        self.detail_title_var.set(item["name"])
        self.detail_path_var.set(item["path"])
        self.detail_meta_var.set(self._detail_note(item))
        self._detail_set_enabled(True)

    def _detail_set_enabled(self, flag):
        for button in self._detail_daily + self._detail_manage:
            button.set_enabled(flag)
        if self._handoff_btn.winfo_manager() == "pack":
            self._handoff_btn.set_enabled(flag)

    def _detail_note(self, item):
        """meta 那行：本机 claude 版本 · 这目录现在什么状态。

        不重复写模型名——上面那颗胶囊就在同一栏里摆着，两处各写一遍只会让人
        怀疑它们是不是说的两件事。

        后半截原来挂在左栏列表行的右侧（见 ui/nav.py 的说明）：挪到这儿是对的，
        左栏只回答"有哪些"，"这一本现在什么样"是右栏的事。
        """
        line = "claude " + (self._local_version or "版本未知")
        path = item["path"]
        if not os.path.isdir(path):
            return line + " · 目录不存在"
        parts = [line]
        # 有交接文档是"现在能接着干"的信号，摆在 meta 里让用户一眼看到；
        # 这同时决定下面「删交接文档」那颗按钮摆不摆。
        has_handoff = os.path.isfile(os.path.join(path, HANDOFF_FILE))
        if has_handoff:
            parts.append("有交接文档 " + HANDOFF_FILE)
        stamp = last_chat_time(path)
        if stamp is not None:
            parts.append("上次聊 " + humanize_ago(stamp))
        self._sync_handoff_button(has_handoff)
        return " · ".join(parts)

    def _sync_handoff_button(self, has_handoff):
        """按这份交接文档有没有，摆上/撤掉那颗按钮。

        判断"现在摆没摆"用 `winfo_manager()`，**不是** `winfo_ismapped()`。后者问的
        是"此刻在屏幕上可见吗"，它受窗口 map 时机影响：离屏跑（探针把窗口摆到
        +30000+30000）时一个明明 pack 着的按钮也报 0，于是这里会走进"已经摆上了"
        的错误分支——按钮撤不下去，切到没交接文档的那本还挂着它。
        """
        packed = self._handoff_btn.winfo_manager() == "pack"
        if has_handoff == packed:
            return
        if has_handoff:
            self._handoff_btn.pack(side="left", padx=(0, 6))
        else:
            self._handoff_btn.pack_forget()

    # ── 日常动作 ──

    # 两个开会话的按钮都还走 open_workspace 那个对话框，只是把"这次要哪种"带进去。
    # 不直接调 launch_workspace 是有具体原因的：**权限等级只有那个对话框能改**
    # （它往 item["permission"] 里写，workspace_permission() 从那儿读），绕过去
    # 等于把这个设置项从界面上抹掉了。顺带"先读交接文档"那个勾也还在。

    def _detail_new_session(self):
        item = self.selected_entry()
        if item is not None:
            self.open_workspace(item, cont=False)

    def _detail_continue(self):
        item = self.selected_entry()
        if item is not None:
            self.open_workspace(item, cont=True)

    def _detail_open_folder(self):
        item = self.selected_entry()
        if item is not None:
            self.open_folder(item["path"])

    # ── 管理动作 ──

    def _detail_rename(self):
        self._with_selected(self.rename_workspace)

    def _detail_move(self):
        self._with_selected(self.open_move_dialog)

    def _detail_up(self):
        self._with_selected(lambda item: self.move_workspace(item, -1))

    def _detail_down(self):
        self._with_selected(lambda item: self.move_workspace(item, 1))

    def _detail_remove(self):
        self._with_selected(self.remove_workspace)

    def _detail_remove_handoff(self):
        self._with_selected(self.remove_handoff)

    def _with_selected(self, handler):
        """管理动作的统一入口：取当前选中那本，没有就什么都不做。

        每个动作各写一遍 `item = self.selected_entry(); if item is not None`
        是同一件事抄六遍，抄漏一处就是一个会在"没选中"时炸的地方。
        """
        item = self.selected_entry()
        if item is not None:
            handler(item)
