"""设置窗：一个窗口 + 左侧分页。

设计说明-0.4.md 第二节定的：入口只有左栏底部那一个「⚙ 设置」，主窗和顶栏都不再
重复。一个窗而不是每域一个窗，是为了以后加设置项不用再加窗口——0.4 之前"凌乱"
的观感，恰恰是入口太多造成的。

四页：模型 / 工作区 / 行为开关 这三页把原来平铺在主窗上的三块搬进来，「关于」
那页收版本号、官网链接和手动查启动器新版。

**关窗只是 withdraw，不是 destroy**：模型那页的「测试」是后台线程往队列里灌结果、
主线程取出来回填到那几行卡片上的（见 ui/models.py 的 _drain_results）。窗口一销毁，
卡片没了，回填那一下就会 TclError。收起来不销毁，这一整类"关着窗的时候活还在跑"
的问题就不存在了；重开时刷新一遍，看到的还是最新的。
"""
import tkinter as tk
from tkinter import messagebox

from claude_tool import __version__
from claude_tool import versions
from claude_tool.host import EMBED_SUPPORTED, open_url
from claude_tool.theme import MUTED, PAGE_BG, PANEL_BG, TEXT, font
from claude_tool.widgets import PillButton, Tip


# 分页顺序就是这儿定的，_settings_rail 照着画。
PAGES = ("模型", "工作区", "行为开关", "关于")
# 每个分页由哪个方法建。名字在这儿配一次，_settings_show_page 照着调。
BUILDERS = {
    "模型": "_settings_page_models",
    "工作区": "_settings_page_workspaces",
    "行为开关": "_settings_page_switches",
    "关于": "_settings_page_about",
}
SETTINGS_W, SETTINGS_H = 640, 560
# 模型列表滚到多高封顶。原来跟 WORKSPACE_MAX 一起摆在 launcher.py 顶部，工作区
# 那份随列表搬去左栏、改由 NAV_MAX 管（见 ui/nav.py），这份跟着模型区搬到这儿。
MODEL_MAX = 300


class SettingsMixin:

    def open_settings(self, page=None):
        """开设置窗，或者把它从收起状态叫回来。"""
        if self._settings_win is None or not self._settings_win.winfo_exists():
            self._build_settings_window()
        else:
            self._settings_win.deiconify()
            self._settings_win.lift()
        self._settings_show_page(page or self._settings_page or PAGES[0])

    def _build_settings_window(self):
        win = tk.Toplevel(self)
        win.title("设置")
        win.configure(bg=PAGE_BG)
        win.protocol("WM_DELETE_WINDOW", self._close_settings)

        box = tk.Frame(win, bg=PAGE_BG)
        box.pack(fill="both", expand=True)
        self._settings_rail = tk.Frame(box, bg=PAGE_BG)
        self._settings_rail.pack(side="left", fill="y", padx=(16, 0), pady=16)
        self._settings_body = tk.Frame(box, bg=PAGE_BG)
        self._settings_body.pack(side="left", fill="both", expand=True,
                                 padx=(16, 16), pady=16)

        self._settings_pages = {}
        self._settings_page = None
        self._settings_win = win
        win.minsize(SETTINGS_W, SETTINGS_H)
        win.geometry("{}x{}".format(SETTINGS_W, SETTINGS_H))
        self._center(win)

    def _close_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.withdraw()

    def _settings_show_page(self, name):
        """切到某一页，没建过就现建。

        建过的不销毁：模型那页有正在跑的测试，重开一次就把结果丢一次。
        """
        self._settings_page = name
        for frame in self._settings_pages.values():
            frame.pack_forget()
        frame = self._settings_pages.get(name)
        if frame is None:
            frame = tk.Frame(self._settings_body, bg=PAGE_BG)
            self._settings_pages[name] = frame
            getattr(self, BUILDERS[name])(frame)
        frame.pack(fill="both", expand=True)
        self._settings_render_rail()

    def _settings_render_rail(self):
        """左边那条分页栏。当前那页用强调色，一眼看出人在哪儿。"""
        for child in self._settings_rail.winfo_children():
            child.destroy()
        for name in PAGES:
            PillButton(self._settings_rail, name,
                       lambda n=name: self._settings_show_page(n),
                       primary=(name == self._settings_page), bg=PAGE_BG,
                       ).pack(fill="x", pady=(0, 6))

    # ── 模型 ──

    def _settings_page_models(self, parent):
        # 还是 _build_section 那套（标题 + 右上那排动作 + 滚动列表），只是换了
        # 挂载的父控件。出来就是 self.model_list，下头的流水线一个字没改。
        self.model_list = self._build_section(
            parent, "模型", max_height=MODEL_MAX,
            actions=[("＋ 添加", self._open_model_dialog),
                     ("导入当前", self._import_current),
                     ("测试", self.test_all_models),
                     ("刷新", self.refresh_models)],
            expand=True)
        self.refresh_models()

    # ── 工作区 ──

    def _settings_page_workspaces(self, parent):
        # AI 托管、默认工作区这两行原来长在主窗「工作区」标题底下（见
        # launcher._build_workspace_rows），整块搬过来。
        self._build_workspace_rows(parent)
        line = tk.Frame(parent, bg=PAGE_BG)
        line.pack(fill="x", pady=(0, 8))
        PillButton(line, "＋ 添加工作区", self._open_add_workspace_picker,
                   bg=PAGE_BG).pack(side="left")
        PillButton(line, "重新扫描", self.rescan, bg=PAGE_BG,
                   ).pack(side="left", padx=(6, 0))
        tk.Label(parent,
                 text="某一本自己的事（改名、搬迁、移除）在右栏「管理」那一排，"
                      "对着选中的那一本做。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                 wraplength=SETTINGS_W - 200).pack(fill="x")

    # ── 行为开关 ──

    def _settings_page_switches(self, parent):
        # 名字还叫 switches：窗口下限和探针（_probe_size_state 量它的自然宽度、
        # _probe_footer_align 数里面那几个勾）都按这个名字找。
        self.switches = switches = tk.Frame(parent, bg=PAGE_BG)
        switches.pack(fill="x", pady=(4, 0))

        # 括号里写的是各自的真名：内嵌那条走的是 conhost（不是默认的 Windows
        # Terminal，字形回退差些），另一条挂的是 Claude Code 的 Stop hook。
        # 熟练用户要的是这几个词，好去翻文档、翻配置文件；只写大白话他就得猜。
        #
        # 行按功能分：怎么开终端、挂不挂 Stop hook、查不查 claude 新版。每行都从
        # 第 0 列起头，几个勾的左边缘对齐（0.4 去掉了「自动查启动器新版」那个勾，
        # 它现在每次启动都查，见 ui/update.py）。
        #
        # 内嵌那个勾只在 Windows 上摆，摆上了它独占第 0 行。别的平台上这行是空
        # 的，于是下面那几行就落到第 0、1 行——行号得跟着挪，不然中间空出一行。
        base = 1 if EMBED_SUPPORTED else 0
        rows = []
        if EMBED_SUPPORTED:
            rows.append((self.embed_var,
                         "内嵌终端 conhost（不勾就在新窗口里开）",
                         self._on_embed_toggle, 0, 0))
        rows += [
            (self.auto_continue_var, "自动继续 Stop hook（替用户拍板）",
             self._on_auto_continue_toggle, base, 0),
            (self.auto_version_var, "自动查 claude 新版（联网）",
             self._on_version_check_toggle, base + 1, 0),
        ]
        for var, text, command, row, col in rows:
            tk.Checkbutton(switches, text=text, variable=var, command=command,
                           bg=PAGE_BG, fg=TEXT, font=font(10),
                           activebackground=PAGE_BG, selectcolor=PANEL_BG,
                           highlightthickness=0, bd=0,
                           ).grid(row=row, column=col, sticky="w",
                                  padx=(0, 18), pady=(0, 6))

        # 上面那行括号里说的是"勾上会怎么样"，可 conhost、Stop hook 这两个词本身
        # 还是黑话。一行注解把词解释掉，术语照留——熟手认词，新手读注解。
        #
        # conhost 那句只在 Windows 上留着：别处没有那个勾，解释了也无处可指。
        note = "Stop hook 是 claude 干完活停下来时触发的动作。"
        if EMBED_SUPPORTED:
            note = "conhost 是 Claude Code 自带的终端窗口；" + note
        tk.Label(parent, text=note, bg=PAGE_BG, fg=MUTED, font=font(9),
                 anchor="w", justify="left",
                 wraplength=SETTINGS_W - 200).pack(fill="x", pady=(10, 0))
        tk.Label(parent,
                 text="这几个勾只影响新开的会话，不动已经在跑的那些。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                 justify="left", wraplength=SETTINGS_W - 200,
                 ).pack(fill="x", pady=(6, 0))

    # ── 关于 ──

    def _settings_page_about(self, parent):
        """这一版叫什么、官网在哪儿、想立刻问一次新版按哪儿。

        手动查那颗按钮走的是 update 那边的 `_start_self_check(manual=True)`。
        0.4 把「自动查启动器新版」那个勾去掉、改成启动即查之后，这个参数就再没
        人传 True 了——留着的唯一去处就是这一页（见 ui/update.py 里那段说明）。
        """
        tk.Label(parent, text="Claude 启动器", bg=PAGE_BG, fg=TEXT,
                 font=font(12, True), anchor="w").pack(fill="x")
        tk.Label(parent, text="版本 {}".format(__version__), bg=PAGE_BG,
                 fg=MUTED, font=font(10), anchor="w").pack(fill="x", pady=(2, 14))

        row = tk.Frame(parent, bg=PAGE_BG)
        row.pack(fill="x")
        PillButton(row, "打开官网", self._open_launcher_site, bg=PAGE_BG,
                   ).pack(side="left")
        check = PillButton(row, "查启动器新版", self._check_launcher_update,
                           primary=True, bg=PAGE_BG)
        check.pack(side="left", padx=(6, 0))
        Tip(check, "现在就去官网问一次有没有新版")
        # 地址原样摆出来：想手抄、想发给别人都用得上，比只留一颗按钮实在。
        tk.Label(row, text=versions.SITE, bg=PAGE_BG, fg=MUTED, font=font(9),
                 anchor="w").pack(side="left", padx=(10, 0))

        # 查的结果落在这一行上（update._say_self_check 往这儿写）。只看主窗底栏
        # 那条反馈是不够的——设置窗是独立的 Toplevel，常常正盖在主窗上面。
        tk.Label(parent, textvariable=self.self_check_note_var, bg=PAGE_BG,
                 fg=TEXT, font=font(9), anchor="w", justify="left",
                 wraplength=SETTINGS_W - 200).pack(fill="x", pady=(14, 0))

        tk.Label(parent,
                 text="启动器每次开都会自己去官网看一眼，落后了才在顶栏提一句；"
                      "这颗按钮只是「我现在就想问一次」。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                 justify="left", wraplength=SETTINGS_W - 200,
                 ).pack(fill="x", pady=(10, 0))

    def _open_launcher_site(self):
        try:
            open_url(versions.SITE)
        except OSError as error:
            messagebox.showerror("打不开", "拉不起浏览器：\n{}".format(error))
            return
        self.feedback_var.set("已在浏览器里打开官网：{}".format(versions.SITE))

    def _check_launcher_update(self):
        """手动查一次启动器自己有没有新版。

        先去上一句「正在问…」：这一问要联网，快也要一两秒，中间不给个回音，
        用户会以为按钮没反应、再点第二下。
        """
        self.self_check_note_var.set("正在问官网，稍等一下……")
        self._start_self_check(manual=True)
