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
from tkinter import messagebox

from claude_tool import agents, history
from claude_tool.config import humanize_ago, last_chat_time, save_config
from claude_tool.handoff import HANDOFF_FILE
from claude_tool.presets import discover_presets
from claude_tool.theme import (BORDER, HOVER_BG, MUTED, OK, PAGE_BG, TEXT,
                               font)
from claude_tool.widgets import PillButton, Row, ScrollArea, Tip, agent_style


# 右栏该有多宽。它不是"右栏就直接摆这么宽"——右栏是 fill="both" + expand，多宽
# 由布局给。这个数只有一个用处：算窗口下限（见 launcher._apply_min_size），保证
# 缩到最窄时【管理】那一排**所有**按钮还在同一行上。
#
# 实测（Task 8 量的，`_probe_size_state.py` 现在把这几条钉住了）：不含
# 「删交接文档」时那排要 324 像素；含它（也就是最宽状态）要 **441**。Task 6 那版
# 按估的按钮宽度写的是 390，实测下来最窄窗口里右栏只分到 442——比 441 多 1 像素，
# 实质上是在赌字体。现在按实测取 460（441 + 19 余量）。
#
# 0.5 加了 agent 切换按钮（文案是档位的 label，最宽 "CodeBuddy Code"），管理排
# 实测（_probe_size_state）要 577px。按同样思路取 620（577 + 43 余量，label 宽度
# 随字体有出入，多留点）。
#
# 另外注意别拿 `detail_area.winfo_reqwidth()` 当依据：右栏里有个显示工作区路径的
# Label，路径长一点那个数就飘（实测能到 601）。下限要的是这排按钮的宽度，那是个
# 不随数据变的数。
DETAIL_MIN_WIDTH = 620

# 右栏会话列表：最多摆几条、一行多高。行高比左栏工作区卡片（52）矮一点——
# 这里只有标题和时间两行，不需要那么厚；6 条正好填满默认窗口那块空白。
SESSION_ROWS = 6
SESSION_ROW_H = 46


def _unbound_model_label(agent_id):
    """没绑预设时这一本用什么（说明见 dialogs.unbound_model_label）。

    文案在两个模块各留一份，是有意的：这是各管一摊的界面文案，为一行字把两个
    mixin 模块勾在一起不值当。codebuddy 家族（国内/国际）都算"自带默认模型"。
    """
    return ("CodeBuddy 自带默认模型"
            if agents.get(agent_id).family == "codebuddy" else "跟随全局")


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

        # 标题那行：左边一枚 agent 徽标（陶土橙 Claude / 蓝 CodeBuddy），右边标题。
        # 左栏行上地方只够一个字母，这里是全名——"这一本到底用哪档"是打开
        # 这一本之前最该一眼看清的事。
        title_row = tk.Frame(parent, bg=PAGE_BG)
        title_row.pack(fill="x", pady=(14, 2))
        self.agent_chip = tk.Label(title_row, text="", bg=HOVER_BG, fg=MUTED,
                                   font=font(9, True), padx=8, pady=2)
        self.agent_chip.pack(side="left", padx=(0, 8))
        self._detail_title_label = tk.Label(
            title_row, textvariable=self.detail_title_var, bg=PAGE_BG,
            fg=TEXT, font=font(16, True), anchor="w")
        self._detail_title_label.pack(side="left", fill="x", expand=True)
        tk.Label(parent, textvariable=self.detail_path_var, bg=PAGE_BG,
                 fg=MUTED, font=font(9), anchor="w").pack(fill="x")
        tk.Label(parent, textvariable=self.detail_meta_var, bg=PAGE_BG,
                 fg=MUTED, font=font(9), anchor="w").pack(fill="x", pady=(10, 0))

        # 绑定行：给这一本单独指定一个模型预设。改了只影响这本——开会话时它的 env
        # 走 --settings 带下去，全局那份 settings.json 一个字节都不动。不绑就跟随
        # 全局（claude）/ 用它自带默认模型（codebuddy，按积分倍率计费）。
        self.preset_row = tk.Frame(parent, bg=PAGE_BG)
        self.preset_row.pack(fill="x", pady=(10, 0))
        tk.Label(self.preset_row, text="工作区模型", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(0, 8))
        self._preset_btn = PillButton(self.preset_row, "跟随全局",
                                      self._detail_pick_preset, bg=PAGE_BG, height=26)
        self._preset_btn.pack(side="left")
        Tip(self._preset_btn, "给这一本单独挑一个模型预设；只改这本，"
                              "不动全局那份 settings.json")

        # 日常动作。三个都建成实例属性，没选中那本时要能一起置灰。
        #
        # **这排必须自己占一个行框**，不能直接 pack 进 parent：parent 里别的东西
        # 都是默认 side="top" 一路往下摞的，往里面塞三个 side="left" 的按钮，它们
        # 会各去抢一条"剩余区域的左边缘"，而且没写 fill="y" 就纵向居中——结果这排
        # 飘到整栏中间（实测 y=553），而后面那些 top 的框（分隔线、「管理」那排、
        # 插件区）全被挤到右半栏、宽度只剩 339（本该是整栏 616）。0.4 收尾时
        # `_probe_align.py` 量出来的。
        self.detail_daily_row = tk.Frame(parent, bg=PAGE_BG)
        self.detail_daily_row.pack(fill="x", pady=(14, 0))
        self._detail_daily = []
        for caption, command, primary in (
                ("新会话", self._detail_new_session, True),
                ("接着上次", self._detail_continue, False),
                ("打开目录", self._detail_open_folder, False)):
            button = PillButton(self.detail_daily_row, caption, command,
                                primary=primary, bg=PAGE_BG, height=30)
            self._detail_daily.append(button)
        self._pack_detail_daily()

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(18, 0))

        # 管理动作。顺序按"从轻到重"排：改名 → 搬迁 → 上下挪 → 移除 → 删交接
        # 文档。移除和删文档是最重的两个，摆最右边，离左边的日常动作最远。
        self._detail_manage = []
        manage = tk.Frame(parent, bg=PAGE_BG)
        manage.pack(fill="x", pady=(10, 0))
        # 留个引用：那排最宽状态（含「删交接文档」）要多少像素，是算窗口下限的
        # 依据（见 _probe_size_state.py），量的时候得拿这个框、不能拿 detail_area
        # ——后者里面还有个显示路径的 Label，会随数据飘。
        self.detail_manage_row = manage
        tk.Label(manage, text="管理", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(0, 8))
        # agent 切换：点了在 claude / codebuddy 之间翻。文案跟着当前档位变。
        self._agent_btn = PillButton(manage, "claude",
                                     self._detail_switch_agent,
                                     bg=PAGE_BG, height=26)
        self._agent_btn.pack(side="left", padx=(0, 6))
        self._detail_manage.append(self._agent_btn)
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

        # ── 会话 ──
        # 「管理」下面本来是空的，这块正好填进去：这一本聊过哪几条、各自多久以前。
        # 列表直接读 CLI 落盘的会话文件（见 history），不另存一份索引——用户在
        # 启动器外面开的会话也得能看见，自己维护一份迟早对不上。
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(18, 0))
        session_head = tk.Frame(parent, bg=PAGE_BG)
        session_head.pack(fill="x", pady=(10, 6))
        tk.Label(session_head, text="会话", bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        self.session_count_var = tk.StringVar(value="")
        tk.Label(session_head, textvariable=self.session_count_var, bg=PAGE_BG,
                 fg=MUTED, font=font(9)).pack(side="left", padx=(8, 0))
        # 右边那句是操作提示，不占按钮位置。会话多了以后下面还有「查看全部」。
        tk.Label(session_head, text="点一条接着聊", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="right")

        self.session_area = ScrollArea(parent, max_height=SESSION_ROWS * SESSION_ROW_H)
        self.session_area.pack(fill="x")
        self.session_empty = tk.Label(parent, text="这个工作区还没有会话",
                                      bg=PAGE_BG, fg=MUTED, font=font(9),
                                      anchor="w")
        self.session_more = PillButton(parent, "查看全部", self._open_session_picker,
                                       bg=PAGE_BG, height=26)
        self._sessions = []
        self._session_agent = None
        # 窗口矮的时候列表得跟着收，不能把底下的插件区顶出可视范围。
        parent.bind("<Configure>", self._fit_sessions, add="+")

        # 插件挂载区：宿主自己的东西在上面，插件注册的排在下面这条分隔线之后
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(18, 0))
        self.detail_plugin_area = tk.Frame(parent, bg=PAGE_BG)
        self.detail_plugin_area.pack(fill="x", pady=(10, 0))

    def _pack_detail_daily(self):
        """把三颗日常动作按钮摆进 `detail_daily_row`。

        它们必须是同一个行框里的 side="left"，理由见那边那段注释——直接摆进
        右栏本身会把整栏的纵向堆叠搅乱。
        """
        for index, button in enumerate(self._detail_daily):
            button.pack(side="left", padx=(0 if index == 0 else 6, 0))

    # ── 会话列表 ──

    def refresh_sessions(self):
        """重读一遍选中那本的会话记录，重画列表。

        直接读文件而不是查缓存：会话是在 CLI 那边写的，这中间可能刚开过、刚关过。
        读的是最近 60 条，够右栏和「查看全部」用；再老的翻出来也没人挑。
        """
        if not hasattr(self, "session_area"):
            return
        item = self.selected_entry()
        if item is None or not os.path.isdir(item["path"]):
            self._sessions = []
            self._session_agent = None
            self._render_sessions()
            return
        agent_id = item.get("agent")
        try:
            sessions = history.list_sessions(agent_id, item["path"], limit=60)
        except Exception:
            # 会话文件是外部数据，读坏一条不该把整个右栏带塌。
            sessions = []
        self._sessions = sessions
        self._session_agent = agent_id
        self._render_sessions()

    def _render_sessions(self):
        self.session_area.clear()
        item = self.selected_entry()
        shown = self._sessions[:SESSION_ROWS]
        for session in shown:
            running = (item is not None and
                       self.running_session(item["path"], session["id"]) is not None)
            row = Row(self.session_area.inner,
                      title=session["title"],
                      subtitle=humanize_ago(session["updated"]),
                      height=SESSION_ROW_H,
                      warn="运行中" if running else "",
                      warn_color=OK,
                      on_click=lambda s=session: self._open_session(s),
                      actions=[("⋯", lambda s=session: self._session_options(s)),
                               ("删除", lambda s=session: self._delete_session(s)),
                               ("改名", lambda s=session: self._rename_session(s))])
            row.pack(fill="x", pady=3)

        total = len(self._sessions)
        self.session_count_var.set(str(total) if total else "")
        if shown:
            self.session_empty.pack_forget()
        else:
            self.session_empty.pack(fill="x", pady=(2, 4))
        if total > SESSION_ROWS:
            self.session_more.pack(anchor="w", pady=(6, 0))
        else:
            self.session_more.pack_forget()
        self.session_area.fit()
        self._fit_sessions()

    def _fit_sessions(self, _event=None):
        """列表高度跟着右栏剩下的空间走。

        默认窗口下 6 条摆得下；窗口缩到下限（660 高）时只剩一两行的位置，这时
        让列表自己出滚动条，而不是把插件区顶出去。
        """
        if not hasattr(self, "session_area"):
            return
        room = self.detail_area.winfo_height() - self.session_area.winfo_y() - 8
        want = max(SESSION_ROW_H, min(SESSION_ROWS * SESSION_ROW_H, room))
        if abs(want - self.session_area.max_height) > 4:
            self.session_area.max_height = want
            self.session_area.fit()

    def _open_session(self, session):
        """点一条会话：没开着就续上；开着就把那扇窗口提到前台。

        单击直连（跟 Trae / WorkBuddy 一致），权限沿用这本工作区存的那档。要改
        权限、或者想先读交接文档，走行尾那个「⋯」——它打开的还是原来那个对话框。
        """
        item = self.selected_entry()
        if item is None:
            return
        running = self.running_session(item["path"], session["id"])
        if running is not None:
            self.focus_running(running)
            return
        self.launch_workspace(item, cont=False, session_id=session["id"])

    def _session_options(self, session):
        """行尾「⋯」：带着这条会话去开原来的对话框（权限 / 交接文档都能改）。"""
        item = self.selected_entry()
        if item is not None:
            self.open_workspace(item, cont=True, session_id=session["id"])

    def _open_session_picker(self):
        item = self.selected_entry()
        if item is not None:
            self.open_session_picker(item, self._sessions)

    def _rename_session(self, session):
        """给一条会话改名：新名字写进记录文件里的 ai-title。"""
        item = self.selected_entry()
        if item is not None:
            self.rename_session(item, session)

    def _delete_session(self, session):
        """删一条会话记录。开着的不让删，删之前问一句。

        删的是 CLI 落盘的那份文件（见 history.delete_session），不进回收站，
        所以确认框里把话说明白。
        """
        item = self.selected_entry()
        if item is None:
            return
        if self.running_session(item["path"], session["id"]) is not None:
            self.feedback_var.set("这条会话还开着，先关掉再删。")
            return
        if not messagebox.askyesno(
                "删除会话",
                "删除「{}」？\n\n记录文件会直接删掉，不进回收站。".format(
                    session["title"]), parent=self):
            return
        if history.delete_session(item.get("agent"), item["path"],
                                  session["id"]):
            self.feedback_var.set("删掉了会话「{}」。".format(session["title"]))
        else:
            self.feedback_var.set("没删成，记录文件可能已经被别处挪走了。")
        self.refresh_sessions()

    # ── 跟着选中态走 ──

    def refresh_detail(self):
        """按当前选中的那一本重画右栏。没选中就整片置灰，别留一堆按不动的按钮
        让人猜为什么按不动。"""
        # 顶上那颗「当前模型」说的是**这一本**用的 agent 现在生效哪个预设，得跟着
        # 选中一起换。放在这儿而不是只靠 refresh_models：选中一本另一档的工作区时
        # 并没有重新扫模型，不给它一个刷新点，胶囊会一直写着上一本的名字。
        self._refresh_model_chip()
        item = self.selected_entry()
        if item is None:
            self.detail_title_var.set("还没选工作区")
            self.detail_path_var.set("左边点一下，或者先去设置里添加一个")
            self.detail_meta_var.set("")
            self._preset_btn.set_text("跟随全局")
            self.agent_chip.pack_forget()
            self._detail_set_enabled(False)
            self.refresh_sessions()
            return
        self.detail_title_var.set(item["name"])
        self.detail_path_var.set(item["path"])
        self.detail_meta_var.set(self._detail_note(item))
        # agent 徽标：底色和字色各用各档的招牌色，换主题时 theme.recolor_tree
        # 会把它们翻成新调色板里对应的那组（见 theme.resolve 那套）。
        chip_bg, chip_fg, _letter = agent_style(item.get("agent"))
        self.agent_chip.configure(text="● " + agents.get(item.get("agent")).label,
                                  bg=chip_bg, fg=chip_fg)
        if self.agent_chip.winfo_manager() != "pack":
            self.agent_chip.pack(side="left", padx=(0, 8),
                                 before=self._detail_title_label)
        self._agent_btn.set_text(agents.get(item.get("agent")).label)
        self._preset_btn.set_text(self._preset_button_text(item))
        self._detail_set_enabled(True)
        self.refresh_sessions()

    def _preset_button_text(self, item):
        """绑定按钮上那行字：绑了写预设名，没绑写"跟随全局/自带默认"。

        绑的那份预设要是已经被删了（或者被手改名），开会话时是当"没绑"算的（见
        sessions._bound_preset_env）——这儿就得把话说出来，不然按钮上还挂着个
        早就不存在的名字，用户以为它在生效。
        """
        bound = item.get("preset") or ""
        if not bound:
            return _unbound_model_label(item.get("agent"))
        if bound in discover_presets(item.get("agent")):
            return bound
        return bound + "（已失效）"

    def _detail_set_enabled(self, flag):
        for button in self._detail_daily + self._detail_manage:
            button.set_enabled(flag)
        self._preset_btn.set_enabled(flag)
        if self._handoff_btn.winfo_manager() == "pack":
            self._handoff_btn.set_enabled(flag)

    def _detail_note(self, item):
        """meta 那行：本机 claude 版本 · 这目录现在什么状态。

        不重复写模型名——上面那颗胶囊就在同一栏里摆着，两处各写一遍只会让人
        怀疑它们是不是说的两件事。

        后半截原来挂在左栏列表行的右侧（见 ui/nav.py 的说明）：挪到这儿是对的，
        左栏只回答"有哪些"，"这一本现在什么样"是右栏的事。
        """
        # 头一句说的是"这一本用的那个 agent"。_local_version 本身就带着 "claude "
        # 前缀（见 update._probe_claude_version），别再补一个——以前补了，结果这行
        # 一直印成 "claude claude 1.2.3"。别的档位没探过版本号，就只报档位名。
        profile = agents.get(item.get("agent"))
        if profile.id == agents.DEFAULT_AGENT:
            line = self._local_version or "版本未知"
        else:
            line = profile.label
        path = item["path"]
        if not os.path.isdir(path):
            # 目录没了，这条路上走不到下面那句 _sync_handoff_button——得在这儿
            # 显式把「删交接文档」撤掉，不然切到一本已经不在的书，按钮会挂着。
            self._sync_handoff_button(False)
            return line + " · 目录不存在"
        parts = [line]
        # 有交接文档是"现在能接着干"的信号，摆在 meta 里让用户一眼看到；
        # 这同时决定下面「删交接文档」那颗按钮摆不摆。
        has_handoff = os.path.isfile(os.path.join(path, HANDOFF_FILE))
        if has_handoff:
            parts.append("有交接文档 " + HANDOFF_FILE)
        stamp = last_chat_time(path, item.get("agent"))
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

    def _detail_pick_preset(self):
        """给这一本单独挑模型预设（弹框在 dialogs.py）。"""
        self._with_selected(self._open_preset_picker)

    def _detail_switch_agent(self):
        """在这本工作区上翻一下用哪个 agent（挨个档位轮）。"""
        self._with_selected(self._switch_agent)

    def _switch_agent(self, item):
        """在这本工作区上翻一下用哪个 agent。

        原来是 claude ↔ CodeBuddy 两档对翻；加了国际档之后挨个档位轮：
        claude → CodeBuddy → CodeBuddy 国际版 → 回到 claude，顺序跟
        agents.AGENTS 的声明一致。
        """
        order = list(agents.ids())
        current = item.get("agent") or agents.DEFAULT_AGENT
        try:
            new_id = order[(order.index(current) + 1) % len(order)]
        except ValueError:
            new_id = agents.DEFAULT_AGENT
        item["agent"] = new_id
        # 预设名是**分档**存的，换档后原来那个名字多半对不上新档的目录；留着的话
        # 这本会"看着绑了个模型、开着其实是默认"。松开，让用户按新档重挑。
        had_preset = bool(item.get("preset"))
        item["preset"] = ""
        save_config(self.config_data)
        # 整片右栏重画：换档不只改那颗按钮的字，顶上「当前模型」胶囊、meta 那行、
        # 绑定按钮说的也都是"这一本用哪个 agent"。
        self.refresh_detail()
        note = "「{}」改用 {}。".format(item["name"], agents.get(new_id).label)
        if had_preset:
            note += "原来绑的模型已松开，按新档重挑一个。"
        self.feedback_var.set(note)

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
