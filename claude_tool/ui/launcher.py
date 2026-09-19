"""主窗口。

左右两栏：左边模型，右边工作区；底下一排开关，中间一条"正在跑"。
对话框（加模型、加工作区、点工作区那次询问）在 ui/dialogs.py，以 mixin
的形式挂在这个类上——它们跟主窗口共享 config_data / feedback_var 那些状态。
"""
import ctypes
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from claude_tool.paths import (
    CONFIG_FILE,
    ICON_FILE,
    PRESET_DIR,
    SETTINGS_FILE,
    TOOL_DIR,
)
from claude_tool.theme import (
    ACCENT,
    ACCENT_SOFT,
    ALERT_BG,
    BORDER,
    HOVER_BG,
    MUTED,
    OK,
    PAGE_BG,
    PANEL_BG,
    TEXT,
    WARN,
    ellipsize,
    font,
)
from claude_tool.permissions import (
    permission_option,
    workspace_permission,
)
from claude_tool.presets import (
    active_preset,
    describe_preset,
    discover_presets,
    migrate_presets,
    read_model,
    test_preset,
)
from claude_tool.config import (
    default_config,
    humanize_ago,
    last_chat_time,
    load_config,
    merge_scanned,
    path_key,
    save_config,
)
from claude_tool.claude import (
    CLAUDE_INSTALL_URL,
    CLAUDE_WINGET_ID,
    CREATE_NEW_CONSOLE,
    CREATE_NO_WINDOW,
    claude_exe,
    find_claude,
    launch,
)
from claude_tool.handoff import (
    AUTO_CONTINUE_MAX,
    AUTO_CONTINUE_RESET,
    HANDOFF_COOLDOWN,
    HANDOFF_FILE,
    HANDOFF_MIN_TURNS,
    HANDOFF_PROMPT,
    HANDOFF_TOOLS,
    READ_HANDOFF_PROMPT,
    ensure_hook_settings,
    hook_fired_at,
    note_handoff_written,
    read_hook_state,
)
from claude_tool.winhost import (
    EmbeddedConsole,
    bring_next_to,
    close_window,
    fresh_console,
    fresh_terminal,
    place_window,
    spawn_console,
    terminal_windows,
    toggle_topmost,
    window_position,
)
from claude_tool.widgets import (
    PillButton,
    Row,
    ScrollArea,
    Tip,
    make_entry,
)

from claude_tool.ui.dialogs import LauncherDialogs


MODEL_MAX, WORKSPACE_MAX = 200, 320
# 内嵌终端那一栏的宽度。它从右边长出来，窗口跟着变宽，左列不动。
TERMINAL_MIN_WIDTH = 900
# 左列和终端栏之间、以及正文左右各留的空档。
SIDE_GAP = 14
PAGE_PAD = 16

# 窗口下限按内容的自然尺寸算（见 _apply_min_size），这两条是它的兜底和冗余。
# 冗余别省：字体在不同机器上宽窄有出入，贴着内容算迟早还会切掉一两个字。
# 48 而不是 32：底下那行初始提示比那排开关还宽一点，它按设计不进下限计算
# （会随反馈消息变长变短），这点余量留给它。
MIN_MARGIN = 48
MIN_HEIGHT_FLOOR = 520
# 关窗时留给内嵌会话收尾的时间（毫秒）。过了这个点还赖着，销毁父窗口自然会
# 把它连窗口带进程一起带走。
CLOSE_GRACE_MS = 800

# 迁移流水线里"等旧会话退干净""等新窗口认出来"的轮询节奏和上限。早先这几处是拍
# 一个固定秒数硬等（关完旧窗口等 1.5 秒、重开完等 2.5 秒）：快机器上白等，慢机器
# 上又未必够。改成看条件——一成立立刻走，到上限就不再等，记一句照常往下走。
MIGRATE_POLL_MS = 200
MIGRATE_WAIT_TRIES = 75          # 200 毫秒 × 75 ≈ 15 秒

# hook 那一轮写交接文档，盯到这么久还没见 handoff.md 动过就不盯了。
# 这个是兜底——正常一次写就几十秒到几分钟。放得偏长是因为提前解锁更糟：文档可能
# 还在写，这时候放「整理交接文档」进去就是两个进程往同一份文件上写。
HOOK_REFRESH_GIVE_UP = 10 * 60

# write_handoff 撞上 hook 锁时交给流水线的原因串。流水线认这个串：把那个会话放回
# 队尾等一会儿再补一份，而不是当"写失败了"跳过——hook 那份可能漏掉它停下之前的
# 最后一点新对话，用户要的就是补上这一点。
HANDOFF_LOCKED_WHY = "正被那个会话自己的 hook 刷着"


class Launcher(LauncherDialogs, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude 启动器")
        self.geometry("640x760")
        self.configure(bg=PAGE_BG)
        # 图得挂在 self 上留个引用：PhotoImage 只被局部变量拿着的话，__init__ 一
        # 返回就被回收，标题栏和任务栏那个图标会默默变回 Tk 自带的。
        # master 必须显式写 self：不写的话这张图会挂到 tkinter 的"默认根窗口"上，
        # 进程里要是先建过别的 Tk（探针就是这么干的），两个解释器对不上，
        # iconphoto 会当场报 "not a photo image"。
        self.icon = tk.PhotoImage(master=self, file=ICON_FILE)
        # default=True：以后新开的 Toplevel（那几个对话框）跟着一起用这个图。
        self.iconphoto(True, self.icon)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.layout("Slim.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {
                "children": [("Vertical.Scrollbar.thumb",
                              {"expand": "1", "sticky": "nswe"})],
                "sticky": "ns"})])
        style.configure("Slim.Vertical.TScrollbar", background="#cbd1da",
                        troughcolor=PAGE_BG, bordercolor=PAGE_BG,
                        darkcolor="#cbd1da", lightcolor="#cbd1da",
                        arrowsize=0, width=8)
        style.map("Slim.Vertical.TScrollbar", background=[("active", "#aab3c0")])
        # 交接进度条：细一点，跟旁边那排小字一个量级；clam 默认那条太粗。
        style.configure("Task.Horizontal.TProgressbar", background=ACCENT,
                        troughcolor=HOVER_BG, bordercolor=HOVER_BG,
                        lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)

        # clam 默认把只读下拉画成灰底，看着像禁用了。改成跟旁边的输入框一个样。
        style.configure("TCombobox", fieldbackground=PANEL_BG, background=PANEL_BG,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL_BG)],
                  background=[("readonly", PANEL_BG)],
                  foreground=[("readonly", TEXT)],
                  arrowcolor=[("readonly", MUTED)])
        # 下拉弹出来的列表是独立的 Listbox，样式不跟着 Combobox 走，得单独上色
        for pattern, value in (
                ("*TCombobox*Listbox.background", PANEL_BG),
                ("*TCombobox*Listbox.foreground", TEXT),
                ("*TCombobox*Listbox.selectBackground", ACCENT_SOFT),
                ("*TCombobox*Listbox.selectForeground", TEXT),
                ("*TCombobox*Listbox.font", font(10))):
            self.option_add(pattern, value)

        self.active_name = None
        self.embedded = None      # 当前塞在窗口里的那个 conhost
        self._pending = None      # 正在等窗口冒出来的那次启动
        self._placing = False
        self._width_before_embed = None
        self.ws_filter = tk.StringVar()   # 工作区筛选框
        self.ws_view = []                 # 当前真正显示出来的工作区，快捷键按它算序号
        self.model_rows = {}              # 名称 -> 卡片，测试结果要回填到上面
        self._testing = False
        self._results = queue.Queue()
        self._handoff_proc = None         # 正在写交接文档的那个 claude
        self._handoff_ctx = None          # (工作区, 目标文件, 写之前的 mtime, 回调)
        self._handoff_log = None
        # 这个启动器开出去、还开着的 claude：[{name, path, proc, hwnd}]
        self.running = []
        self._running_shown = False       # 「正在跑」那块现在摆没摆出来
        # 换模型时那条流水线：写交接文档 → 关旧的 → 用新模型重开，一个接一个。
        # 队列就是"还没轮到的"，从头取；挑中的那个要是正被它自己的 hook 锁着，
        # 就先换到后面去（见 _run_next_task），所以 _task_total 是当初选了几个、
        # _task_queue 是还剩几个，第几个 = 总数 - 剩的 + 1。
        self._task_queue = []
        self._task_total = 0
        self._task_running = False
        self._task_visible = False
        self._task_text = None
        self._task_started = 0.0
        self._task_timer = None
        # hook 自己在刷交接文档这件事。那一轮跑在会话自己的终端窗口里，启动器只
        # 看得见（读节流状态和 handoff.md 的时间戳），控制不了——所以只能上把锁、
        # 摆个提示，让用户知道它在花 token。
        #   _hook_refreshes  键=工作目录，值见 _start_hook_refresh
        #   _hook_seen       键=工作目录，值=上次见到的时间戳，用来认"刚触发"
        self._hook_refreshes = {}
        self._hook_seen = {}
        self._hook_rows = {}              # 键=工作目录，值=(行容器, 秒数 StringVar)
        self._handoff_visible = False     # 「正在交接」那块现在摆没摆出来
        self._jobs_shown = False          # 上面那个容器现在摆没摆出来
        self.version_queue = queue.Queue()   # 后台问出来的 claude 版本号，主线程来取
        # 目录骨架和一次性迁移都在启动时做完，之后各处只管用，不必再判存在。
        for directory in (TOOL_DIR, PRESET_DIR):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                pass
        self.migrated_presets = migrate_presets()

        self.config_data = load_config()
        fresh = self.config_data is None
        if fresh:
            self.config_data = default_config()
        if not os.path.exists(CONFIG_FILE):
            # 首次在新位置落盘：全新安装顺手把默认工作区目录建出来，从旧位置
            # 迁过来的就原样照搬，不再多扫一遍目录。
            try:
                os.makedirs(self.config_data["workplace"], exist_ok=True)
            except OSError:
                pass
            if fresh:
                merge_scanned(self.config_data)
            save_config(self.config_data)

        self._build_ui()
        self.refresh_models()
        self.refresh_workspaces()
        self._apply_min_size()
        self._probe_claude_version()
        self.after(1000, self._poll_running)
        self.ws_filter.trace_add("write", lambda *_: self.refresh_workspaces())
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Control-f>", self._focus_filter)
        self.bind_all("<Control-F>", self._focus_filter)
        for number in range(1, 10):
            self.bind_all("<Control-Key-{}>".format(number),
                          lambda _e, n=number: self.launch_nth(n))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._restore_geometry()

    def _apply_min_size(self):
        """窗口下限按内容量出来，别写死。

        底下那排开关（内嵌终端 + 两个挂 Stop hook 的行为，分两行摆）是最宽的一块，
        两段文字加上间距比左列那几块都宽；原先写死的下限 560（默认宽 640）都装不下，
        右边那个勾的标题会被切掉一截，字号再大点的机器切得更多。

        量的只有那排开关和左列这两块**定死**的东西，不去拿整窗的 reqwidth：顶栏
        的模型名、底下那行反馈都是会变长的字符串，它们一长整窗的自然宽度就跟着
        跳，下限跟着跳，用户就会看到窗口自己忽大忽小。

        高度**不**跟着算：中间那两块列表是可滚动的，它们的自然高度不该拿来当下限
        ——而且这个数还跟问的时机有关，在 _build_ui() 刚建完时问是 823，等列表
        fit() 完再问是 410，差一倍。高度就守 520 这个可用底线。

        得等界面整个建完、列表也填过之后再调，量的才是最终布局。
        """
        self.update_idletasks()
        content = max(self.switches.winfo_reqwidth(),
                      self.side.winfo_reqwidth())
        width = content + 2 * PAGE_PAD + MIN_MARGIN
        if self._width_before_embed is not None:
            # 内嵌时右边那栏是外挂上去的，下限得跟着抬；不然用户往回一缩，
            # 先挨挤的还是左边那半张脸——这一版改动要避免的正是这个。
            width = max(width, self._width_before_embed + SIDE_GAP
                        + TERMINAL_MIN_WIDTH)
        self.minsize(width, MIN_HEIGHT_FLOOR)

    def _restore_geometry(self):
        saved = self.config_data["window"]
        if not saved:
            return
        self.geometry("{}x{}".format(saved["w"], saved["h"]))
        self.update_idletasks()
        place_window(self, saved["x"], saved["y"])

    def _on_close(self):
        if self._task_running and not messagebox.askyesno(
                "还在换模型",
                "正在给会话换模型，还没弄完。\n\n"
                "现在关掉的话，某个会话可能正好卡在「旧的已经关了、新的还没开」"
                "那一步，那一轮上下文就断了。\n\n真要现在关吗？"):
            return
        if self.embedded is not None:
            # 内嵌的那个是挂在这扇窗口底下的子窗口，窗口一销毁它就跟着没了。
            # 所以不能只是"放它走"（那等于连窗口带会话一起抽掉），也不能说完
            # 就立刻销毁（等于拔电）。先正经道个别——等同点它标题栏的 X，给
            # claude 一点时间把手上的活收尾——歇一下再关自己。
            try:
                self.embedded.close()
            except Exception:
                pass
            self.embedded = None
            self.after(CLOSE_GRACE_MS, self._shutdown)
            return
        self._shutdown()

    def _shutdown(self):
        try:
            x, y = window_position(self)
            # 存进去的是"没内嵌时候"的宽度：内嵌时窗口是临时撑宽的，下次开
            # 机不该照那个宽度铺一扇空荡荡的窗口。
            self.config_data["window"] = {
                "x": x, "y": y,
                "w": self._width_before_embed or self.winfo_width(),
                "h": self.winfo_height()}
            save_config(self.config_data)
        except Exception:
            pass
        self.destroy()

    # ── 布局 ──

    def _build_ui(self):
        # 顶上是条状态条：当前模型、claude 装的是哪版、以及去配置目录的入口。
        # 这里不再写一遍"Claude 启动器"——窗口标题栏上已经有那个名字了，
        # 正文再来一遍纯属占地方。
        header = tk.Frame(self, bg=PANEL_BG)
        header.pack(fill="x")
        line = tk.Frame(header, bg=PANEL_BG)
        line.pack(fill="x", padx=20, pady=12)
        # "配置目录"四个字对没上手的人等于没解释，得补一句这是哪儿。但顶栏就
        # 一条，左边还摆着模型名和版本号——塞一句话进去版本号立刻被截掉半截。
        # 所以挂悬停提示，鼠标停上去才出。
        tool_dir = PillButton(line, "打开配置目录", self._open_tool_dir, bg=PANEL_BG)
        tool_dir.pack(side="right")
        Tip(tool_dir, "模型预设和工作区都记在这儿，想手改文件就从这儿进去")

        tk.Label(line, text="当前模型", bg=PANEL_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(0, 8))
        self.status_var = tk.StringVar(value="未设置")
        # 模型名做成个小胶囊。有模型时用强调色，没设的时候是中性灰——
        # 空状态不该长得像报错或者链接。
        self.model_chip = tk.Label(line, textvariable=self.status_var, bg=HOVER_BG,
                                   fg=MUTED, font=font(10, True), padx=10, pady=2)
        self.model_chip.pack(side="left")

        self.version_var = tk.StringVar(value="正在查 claude 版本…")
        tk.Label(line, textvariable=self.version_var, bg=PANEL_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(16, 0))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # 没装 claude 才挂出来的告警条，装好了整条不占地方
        self.banner = tk.Frame(self, bg=ALERT_BG)
        self._build_claude_banner()

        footer = tk.Frame(self, bg=PAGE_BG)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x")
        # 存下来是给 _apply_min_size 量的：这排开关是窗口该有多宽的那个基准。
        self.switches = switches = tk.Frame(footer, bg=PAGE_BG)
        switches.pack(fill="x", padx=PAGE_PAD, pady=(8, 0))
        self.embed_var = tk.BooleanVar(
            value=bool(self.config_data.get("embed")))
        # 会花用户 token、会替用户拍板的功能，默认关；这几个勾只影响新开的会话，
        # 不动已经跑着的
        self.auto_handoff_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_handoff")))
        self.auto_continue_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_continue")))
        # 括号里写的是各自的真名：内嵌那条走的是 conhost（不是默认的 Windows
        # Terminal，字形回退差些），另两条挂的都是 Claude Code 的 Stop hook。
        # 熟练用户要的是这几个词，好去翻文档、翻配置文件；只写大白话他就得猜。
        #
        # 拆两行摆，不是一行塞三个：窗口下限是按这排开关的自然宽度量的
        # （见 _apply_min_size），三个挤一行会把下限从 742 顶到 1029。行按功能分——
        # 上排是终端怎么开，下排是两个挂 Stop hook 的行为。
        for var, text, command, row, col in (
                (self.embed_var, "内嵌终端 conhost（不勾就在新窗口里开）",
                 self._on_embed_toggle, 0, 0),
                (self.auto_handoff_var, "自动刷交接文档 Stop hook（会多用 token）",
                 self._on_auto_handoff_toggle, 1, 0),
                (self.auto_continue_var, "自动继续 Stop hook（替用户拍板）",
                 self._on_auto_continue_toggle, 1, 1)):
            tk.Checkbutton(switches, text=text, variable=var, command=command,
                           bg=PAGE_BG, fg=TEXT, font=font(9), activebackground=PAGE_BG,
                           selectcolor=PANEL_BG, highlightthickness=0, bd=0,
                           ).grid(row=row, column=col, sticky="w",
                                  padx=(0, 18), pady=(0, 2))
        self.feedback_var = tk.StringVar(
            value="点工作区选「新会话」或「接着上次聊」；行首那个数字按住 Ctrl 就能直接开，"
                  "Ctrl+F 跳到筛选框。")
        tk.Label(footer, textvariable=self.feedback_var, bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").pack(fill="x", padx=20, pady=(2, 8))

        # footer 先 pack 是为了让它先把自己的高度要走——窗口被拉矮时该挤的是
        # 中间那块列表，不是这行提示。
        body = tk.Frame(self, bg=PAGE_BG)
        body.pack(fill="both", expand=True, padx=SIDE_GAP)

        # side 是常年都在的左列；panel 是内嵌终端，平时不摆出来。
        # 两者都 side="left"，终端一出现就从右侧长出来，窗口跟着变宽。
        self.side = tk.Frame(body, bg=PAGE_BG)
        self.side.pack(side="left", fill="both", expand=True)

        # 先建好但不 pack——没会话在跑的时候，界面上不该看出有这么一块。
        # 一有活的会话，_render_running() 就把它插到模型区上面。
        self._build_running(self.side)
        # 交接进度那块也先建好不摆出来，有活的时候插到「正在跑」上面
        self._build_tasks(self.side)
        # hook 自己刷交接文档那块的壳子，同样先建好不摆，见 _show_handoff_area
        self._build_handoffs()

        self.model_list = self._build_section(
            self.side, "模型", max_height=MODEL_MAX,
            actions=[("＋ 添加", self._open_model_dialog),
                     ("导入当前", self._import_current),
                     ("测试", self.test_all_models),
                     ("刷新", self.refresh_models)])

        self.ws_list = self._build_section(
            self.side, "工作区", max_height=WORKSPACE_MAX, expand=True,
            actions=[("＋ 添加工作区", self._open_add_workspace_picker),
                     ("重新扫描", self.rescan)],
            search=self.ws_filter, top=self._build_autonomy_row)

        self.panel = tk.Frame(body, bg=PAGE_BG)
        self._build_terminal(self.panel)

    def _build_claude_banner(self):
        """找不到 claude 时在顶上挂一条，给三条明路。找到就什么都不摆。"""
        self.claude_path = find_claude()
        if self.claude_path is not None:
            return
        banner = self.banner
        # 建的时候 body 还没 pack，所以这一 pack 就正好落在标题栏和正文之间
        banner.pack(fill="x")
        inner = tk.Frame(banner, bg=ALERT_BG)
        inner.pack(fill="x", padx=20, pady=10)
        tk.Label(inner, text="没找到 claude，装好才能启动。", bg=ALERT_BG,
                 fg=WARN, font=font(10, True)).pack(side="left")
        PillButton(inner, "重新检测", self._recheck_claude, bg=ALERT_BG,
                   ).pack(side="right", padx=(6, 0))
        PillButton(inner, "打开官网说明", self._open_install_page, bg=ALERT_BG,
                   ).pack(side="right", padx=(6, 0))
        PillButton(inner, "一键安装", self._install_claude, primary=True,
                   bg=ALERT_BG).pack(side="right")
        tk.Frame(banner, bg=BORDER, height=1).pack(fill="x", side="bottom")

    def _recheck_claude(self):
        self.claude_path = find_claude()
        if self.claude_path is None:
            self.feedback_var.set("还是没找到。装完可能要重开一次启动器，PATH 才会刷新。")
            return
        self.banner.pack_forget()
        self.feedback_var.set("找到 claude 了：{}".format(self.claude_path))

    def _open_install_page(self):
        try:
            os.startfile(CLAUDE_INSTALL_URL)
        except OSError as e:
            messagebox.showerror("打不开", "拉不起浏览器：\n{}".format(e))
            return
        self.feedback_var.set("已在浏览器里打开安装说明。")

    def _install_claude(self):
        """调 winget 装。这是动系统的操作，先问一声，不偷偷跑。"""
        if not shutil.which("winget"):
            messagebox.showinfo(
                "没有 winget",
                "这台机器上没有 winget，没法一键装。\n\n"
                "点「打开官网说明」照着装，或者装好 Node 之后跑：\n"
                "npm install -g @anthropic-ai/claude-code")
            return
        if not messagebox.askyesno(
                "安装 claude",
                "会用 winget 装 Anthropic 官方的 Claude Code：\n\n"
                "winget install --id {}\n\n"
                "会弹一个新窗口显示进度，装完自己关掉就行。要继续吗？"
                .format(CLAUDE_WINGET_ID)):
            return
        try:
            subprocess.Popen(
                ["winget", "install", "--id", CLAUDE_WINGET_ID,
                 "--accept-package-agreements", "--accept-source-agreements"],
                creationflags=CREATE_NEW_CONSOLE)
        except Exception as e:
            messagebox.showerror("启动失败", "拉不起 winget：\n{}".format(e))
            return
        self.feedback_var.set("正在装 claude，装完点「重新检测」。")

    def _build_terminal(self, parent):
        """内嵌终端那一块，先建好但不摆出来，等真要内嵌时再 pack。"""
        self.term_head = tk.Frame(parent, bg=PAGE_BG)
        tk.Label(self.term_head, text="终端", bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        self.term_name_var = tk.StringVar()
        tk.Label(self.term_head, textvariable=self.term_name_var, bg=PAGE_BG,
                 fg=MUTED, font=font(10)).pack(side="left", padx=(10, 0))
        PillButton(self.term_head, "放到独立窗口", self.detach_terminal,
                   bg=PAGE_BG).pack(side="right", padx=(6, 0))
        PillButton(self.term_head, "关掉", self.close_terminal,
                   bg=PAGE_BG).pack(side="right", padx=(6, 0))

        # 这一栏宽度写死：内嵌时它从右边长出来，宽窄不该由里面那行字决定，
        # 更不该反过来去挤左边那半张脸。propagate 关掉，configure 的宽度才算数。
        parent.pack_propagate(False)
        parent.configure(width=TERMINAL_MIN_WIDTH)

        self.term_holder = tk.Frame(parent, bg="#1c1c1c", height=240)
        self.term_holder.pack_propagate(False)
        self.term_holder.bind("<Configure>", self._on_term_resize)

    def _build_section(self, parent, title, max_height, actions, expand=False,
                       search=None, top=None):
        head = tk.Frame(parent, bg=PAGE_BG)
        head.pack(fill="x", pady=(16, 6))
        tk.Label(head, text=title, bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        for text, command in reversed(actions):
            PillButton(head, text, command, bg=PAGE_BG).pack(side="right", padx=(6, 0))
        # 标题和筛选框中间那一条。搜索框得跟在标题后面、列表前面，不然 pack 顺序
        # 会把列表挤到它上面去，所以这一条也得赶在搜索框之前 pack。
        if top is not None:
            top(parent)
        if search is not None:
            box = tk.Frame(parent, bg=PAGE_BG)
            box.pack(fill="x", pady=(0, 6))
            tk.Label(box, text="筛选", bg=PAGE_BG, fg=MUTED,
                     font=font(9)).pack(side="left", padx=(2, 6))
            self.filter_entry = make_entry(box, search, width=10)
            self.filter_entry.pack(side="left", fill="x", expand=True)
        area = ScrollArea(parent, max_height=max_height)
        # 记下标题栏，"正在跑"那块要靠它把自己插到模型区上面
        area.head = head
        if expand:
            area.pack(fill="both", expand=True)
        else:
            area.pack(fill="x")
        return area

    def _build_autonomy_row(self, parent):
        """「工作区」标题底下那一行：把一整件事布置给它自己跑。

        摆在这儿而不是顶栏，是因为托管要挑的就是一个工作区——「托管哪个目录、
        用哪一档」跟在哪儿挑工作区是同一件事，凑在一起看才顺。
        """
        line = tk.Frame(parent, bg=PAGE_BG)
        line.pack(fill="x", pady=(0, 8))
        PillButton(line, "AI 托管", self._open_autonomy_dialog, primary=True,
                   bg=PAGE_BG).pack(side="left")
        tk.Label(line, text="布置一个任务，交给它自己跑", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(10, 0))

    def _on_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        for target in (self.model_list, self.ws_list):
            if target.contains(widget):
                target.scroll(-int(event.delta / 120))
                return

    # ── 正在跑的会话 ──

    def _build_running(self, parent):
        """「正在跑」那块。建好先不 pack，等真有会话了再插进去。

        只管从这个启动器开出去的窗口——记的是 Popen 句柄，poll() 一下就知道
        那个控制台还开没开着。别的终端里自己敲的 claude 它看不见，也没打算看见。
        """
        self.running_frame = tk.Frame(parent, bg=PAGE_BG)
        head = tk.Frame(self.running_frame, bg=PAGE_BG)
        head.pack(fill="x", pady=(16, 6))
        tk.Label(head, text="正在跑", bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        tk.Label(head, text="（从这个启动器开出去的窗口）", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(8, 0))
        self.running_rows = tk.Frame(self.running_frame, bg=PAGE_BG)
        self.running_rows.pack(fill="x")

    def _render_running(self):
        """照 self.running 重画那几行。空了就整块收起来。"""
        for child in self.running_rows.winfo_children():
            child.destroy()
        if not self.running:
            self.running_frame.pack_forget()
            self._running_shown = False
            self.model_list.fit()
            self.ws_list.fit()
            return
        for item in self.running:
            line = tk.Frame(self.running_rows, bg=PANEL_BG,
                            highlightbackground=BORDER, highlightthickness=1)
            line.pack(fill="x", pady=3)
            tk.Label(line, text="●", bg=PANEL_BG, fg=ACCENT,
                     font=font(10)).pack(side="left", padx=(12, 8), pady=8)
            text = tk.Frame(line, bg=PANEL_BG)
            text.pack(side="left", fill="x", expand=True)
            tk.Label(text, text=item["name"], bg=PANEL_BG, fg=TEXT, font=font(10),
                     anchor="w").pack(fill="x")
            tk.Label(text, text=ellipsize(item["path"], 320, 9), bg=PANEL_BG,
                     fg=MUTED, font=font(9), anchor="w").pack(fill="x")
            # 操作按屏幕从左到右念：移过来 置顶 关掉，最后是整理交接文档。
            # side="right" 是先摆的在最右边，所以得倒着 pack。
            ops = []
            if item.get("hwnd"):
                # 这三样只有独立窗口有——内嵌那个本来就长在本窗口里，挪位置和
                # 压顶层对它没意义（那一栏自己还有「放到独立窗口」「关掉」）。
                ops += [("移过来", lambda it=item: self.bring_running(it)),
                        ("置顶", lambda it=item: self.top_running(it)),
                        ("关掉", lambda it=item: self.close_running(it))]
            # 现在就让它写，读的是硬盘上已经存下来的那份会话记录——所以哪怕
            # 里面那份 claude 还开着也照写不误，只是会 fork 出一份副本。
            ops.append(("整理交接文档", lambda it=item: self.write_handoff(it)))
            holder = tk.Frame(line, bg=PANEL_BG)
            holder.pack(side="right", padx=(8, 10), pady=8)
            for caption, command in reversed(ops):
                PillButton(holder, caption, command,
                           bg=PANEL_BG).pack(side="right", padx=(6, 0))
        self.running_frame.pack(fill="x", before=self.model_list.head)
        self._running_shown = True
        self.model_list.fit()
        self.ws_list.fit()

    # ── 交接进度 ──

    def _build_tasks(self, parent):
        """「有活在跑」这块区域，建好不摆出来，有活才插到最上面。

        里面装两块：启动器自己起的活（换模型那条流水线、手动整理交接文档），
        和 hook 自己在刷交接文档——两者都是"在写一份文档，好几秒往上"。不摆个
        一直在动的东西，用户会以为窗口卡死了、把正写到一半的 claude 关掉；
        hook 那块还得让人看见它在花 token。
        """
        self.jobs_frame = tk.Frame(parent, bg=PAGE_BG)
        self.task_frame = tk.Frame(self.jobs_frame, bg=PANEL_BG,
                                   highlightbackground=BORDER,
                                   highlightthickness=1)
        inner = tk.Frame(self.task_frame, bg=PANEL_BG)
        inner.pack(fill="x", padx=12, pady=10)
        head = tk.Frame(inner, bg=PANEL_BG)
        head.pack(fill="x")
        self.task_head_var = tk.StringVar(value="正在换模型")
        tk.Label(head, textvariable=self.task_head_var, bg=PANEL_BG, fg=TEXT,
                 font=font(10, True)).pack(side="left")
        self.task_count_var = tk.StringVar()
        tk.Label(head, textvariable=self.task_count_var, bg=PANEL_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(8, 0))
        self.task_name_var = tk.StringVar()
        tk.Label(head, textvariable=self.task_name_var, bg=PANEL_BG, fg=ACCENT,
                 font=font(10)).pack(side="right")

        # 走马灯式：真进度算不出来（那一份文档写多久全看那边），能表示的只有
        # "还在动"，所以不假装有百分比。
        self.task_bar = ttk.Progressbar(inner, mode="indeterminate",
                                        style="Task.Horizontal.TProgressbar")
        self.task_bar.pack(fill="x", pady=(8, 6))
        self.task_step_var = tk.StringVar()
        tk.Label(inner, textvariable=self.task_step_var, bg=PANEL_BG, fg=MUTED,
                 font=font(9), anchor="w", justify="left",
                 ).pack(fill="x")

    # ── hook 自己在刷交接文档 ──

    def _build_handoffs(self):
        """「正在交接」那块，跟上面换模型那块上下排。

        这条路上的写文档不是启动器起的进程——是会话里的 Stop hook 回了
        {"decision":"block"} 之后，被逼出来的那一轮在终端窗口里写的。启动器只能
        看着（读节流状态和 handoff.md 的时间戳），控制不了。但用户必须看得见：
        看不见的话这个功能等于没开，而它是真花 token 的。
        """
        self.handoff_frame = tk.Frame(self.jobs_frame, bg=PANEL_BG,
                                      highlightbackground=BORDER,
                                      highlightthickness=1)
        inner = tk.Frame(self.handoff_frame, bg=PANEL_BG)
        inner.pack(fill="x", padx=12, pady=10)
        self.handoff_head_var = tk.StringVar(value="正在交接")
        tk.Label(inner, textvariable=self.handoff_head_var, bg=PANEL_BG,
                 fg=TEXT, font=font(10, True)).pack(anchor="w")
        self.handoff_rows = tk.Frame(inner, bg=PANEL_BG)
        self.handoff_rows.pack(fill="x")
        # 这句得从头摆到尾：刷的过程中在这个会话里说的话，不在这次要写的材料里。
        self.handoff_warn = tk.Label(
            inner,
            text="刷的这会儿在这个会话里接着说的话，不会进这份文档，"
                 "要的话等它刷完再重新交接一次。",
            bg=PANEL_BG, fg=WARN, font=font(9), anchor="w", justify="left")
        self.handoff_warn.pack(fill="x", pady=(6, 0))
        inner.bind("<Configure>", lambda e: self.handoff_warn.configure(
            wraplength=max(240, e.width - 24)))

    def _start_hook_refresh(self, key, item):
        target = os.path.join(item["path"], HANDOFF_FILE)
        try:
            before = os.path.getmtime(target)
        except OSError:
            before = 0.0
        self._hook_refreshes[key] = {"name": item["name"], "path": item["path"],
                                     "started": time.time(), "before": before,
                                     "outcome": None, "finished": 0.0}

    def _finish_hook_refresh(self, key, outcome):
        job = self._hook_refreshes.get(key)
        if job is None or job["outcome"] is not None:
            return
        job["outcome"] = outcome
        job["finished"] = time.time()
        if outcome == "ok":
            self.feedback_var.set(
                "「{}」的交接文档被那个会话自己刷新了。".format(job["name"]))

    def _hook_locked(self, path):
        """这个目录的交接文档，正被它自己会话的 hook 写着没有。

        启动器要动手之前都得先问这一句：两个进程往同一份 handoff.md 上写，
        出来的东西是两份搅在一起。
        """
        job = self._hook_refreshes.get(path_key(path))
        return job is not None and job["outcome"] is None

    def _note_handoff_written(self, path):
        """启动器自己写完了这份文档：顶掉 hook 的节流表，基线也跟着挪过去。

        不挪基线的话，这次写把节流表里的 last 顶成了"现在"，下一次心跳会把这个
        新 last 当成"hook 刚触发"，凭空摆出一块「正在交接」，一直挂到超时。
        """
        note_handoff_written(path)
        key = path_key(path)
        if key in self._hook_seen:
            self._hook_seen[key] = hook_fired_at(read_hook_state(), path)

    def _poll_hook_handoffs(self):
        """每秒一趟：哪个会话的 hook 刚被逼着去写文档了、写完了没。

        认"刚触发"只能靠节流状态里那个 last——只有真决定要刷的那一次才写它，
        普通轮次只动 turns。认"写完"靠 handoff.md 的时间戳往前走了。两头都不是
        启动器能控制的，所以这里只做观察，外加一把锁，别跟自己起的那份撞上。
        """
        watched = {}
        for item in self.running:
            if item.get("hook"):
                watched[path_key(item["path"])] = item
        for key in list(self._hook_seen):
            if key not in watched:
                del self._hook_seen[key]

        if watched:
            state = read_hook_state()
            for key, item in watched.items():
                fired = hook_fired_at(state, item["path"])
                if key not in self._hook_seen:
                    self._hook_seen[key] = fired
                elif fired > self._hook_seen[key]:
                    self._hook_seen[key] = fired
                    self._start_hook_refresh(key, item)

        now = time.time()
        for key in list(self._hook_refreshes):
            job = self._hook_refreshes[key]
            if job["outcome"] is not None:
                # 收工那句留两秒再撤，不然一闪而过等于没说。
                if now - job["finished"] >= 2:
                    del self._hook_refreshes[key]
                continue
            if key not in watched:
                # 会话都没了，被逼出来的那一轮自然也死了。
                self._finish_hook_refresh(key, "gone")
                continue
            try:
                mtime = os.path.getmtime(os.path.join(job["path"], HANDOFF_FILE))
            except OSError:
                mtime = 0.0
            if mtime > job["before"]:
                self._finish_hook_refresh(key, "ok")
            elif now - job["started"] >= HOOK_REFRESH_GIVE_UP:
                self._finish_hook_refresh(key, "timeout")

        if self._hook_refreshes:
            self._render_handoffs()
            self._show_handoff_area()
        else:
            self._hide_handoff_area()

    def _render_handoffs(self):
        """照 _hook_refreshes 更新那几行。行是一秒一跳地改字，不重建控件。"""
        for key in list(self._hook_rows):
            if key not in self._hook_refreshes:
                frame, _var = self._hook_rows.pop(key)
                frame.destroy()
        now = time.time()
        for key, job in self._hook_refreshes.items():
            row = self._hook_rows.get(key)
            if row is None:
                frame = tk.Frame(self.handoff_rows, bg=PANEL_BG)
                frame.pack(fill="x", pady=(4, 0))
                tk.Label(frame, text="「{}」".format(job["name"]), bg=PANEL_BG,
                         fg=ACCENT, font=font(10)).pack(side="left")
                var = tk.StringVar()
                tk.Label(frame, textvariable=var, bg=PANEL_BG, fg=MUTED,
                         font=font(9)).pack(side="right")
                row = (frame, var)
                self._hook_rows[key] = row
            if job["outcome"] == "ok":
                row[1].set("写好了 {}".format(HANDOFF_FILE))
            elif job["outcome"] == "timeout":
                row[1].set("没看到文档更新，不盯了")
            elif job["outcome"] == "gone":
                row[1].set("会话关了，不盯了")
            else:
                row[1].set("正在写 {} · 已用 {} 秒".format(
                    HANDOFF_FILE, int(now - job["started"])))

    def _show_handoff_area(self):
        if self._handoff_visible:
            return
        self._handoff_visible = True
        self._layout_jobs()

    def _hide_handoff_area(self):
        if not self._handoff_visible:
            return
        self._handoff_visible = False
        for frame, _var in self._hook_rows.values():
            frame.destroy()
        self._hook_rows = {}
        self._layout_jobs()

    def _show_task_area(self):
        """把进度区插到「正在跑」上面（没有「正在跑」就插到模型区上面）。

        已经在摆着就什么都不做——流水线里每一步都会叫它一次，进来一次就重置
        一次秒表的话，那个"已用多久"就永远停在几秒，看着更像卡死了。
        """
        if self._task_visible:
            return
        self._task_visible = True
        self._layout_jobs()
        self.task_bar.start(12)
        self._task_started = time.time()
        self._task_tick()

    def _hide_task_area(self):
        self._task_visible = False
        if self._task_timer is not None:
            self.after_cancel(self._task_timer)
            self._task_timer = None
        self.task_bar.stop()
        self._task_text = None
        self._layout_jobs()

    def _layout_jobs(self):
        """重排那两块，顺带定容器该不该出现。

        两块每次都重新 pack 一遍，而不是各自 pack_forget/pack：谁在上取决于谁先
        摆，顶上那块不留间距、下面那块留，这两件事得一起算。
        """
        self.task_frame.pack_forget()
        self.handoff_frame.pack_forget()
        first = True
        for frame, on in ((self.task_frame, self._task_visible),
                          (self.handoff_frame, self._handoff_visible)):
            if on:
                frame.pack(fill="x", pady=(0, 0) if first else (10, 0))
                first = False

        showing = self._task_visible or self._handoff_visible
        if showing == self._jobs_shown:
            return
        self._jobs_shown = showing
        if showing:
            # 锚在「正在跑」上面；「正在跑」自己永远锚在模型区上面，所以后摆的
            # 那个反而排在下面——这里得赶在它之前摆，出来的顺序才是活在上。
            anchor = (self.running_frame if self._running_shown
                      else self.model_list.head)
            self.jobs_frame.pack(fill="x", pady=(16, 0), before=anchor)
        else:
            self.jobs_frame.pack_forget()
        self.model_list.fit()
        self.ws_list.fit()

    def _task_step(self, text):
        """换掉进度条底下那行说明。秒数自己会往上加，表示它没死。"""
        self._task_text = text
        self._task_render()

    def _task_render(self):
        if self._task_text is None:
            return
        self.task_step_var.set("{} · 已用 {} 秒".format(
            self._task_text, int(time.time() - self._task_started)))

    def _task_tick(self):
        self._task_timer = None
        if not self._task_visible:
            return
        self._task_render()
        self._task_timer = self.after(1000, self._task_tick)

    def _poll_running(self):
        """每秒一趟：窗口还开着没、版本号回来了没、hook 在不在刷交接文档。

        都走这条心跳是因为它是现成的、启动时就起来的定时器；再造几个不值当。
        """
        try:
            while True:
                self.version_var.set(self.version_queue.get_nowait())
        except queue.Empty:
            pass

        live = [item for item in self.running if item["proc"].poll() is None]
        if len(live) != len(self.running):
            gone = len(self.running) - len(live)
            self.running = live
            self._render_running()
            if gone:
                self.feedback_var.set("有会话关掉了，正在跑的那块已经跟着更新。")
        if self.embedded is not None and self.embedded.process.poll() is not None:
            # 里面那个 claude 自己退了（敲了 /exit，或者刚被「关掉」掐掉），那
            # 右边就只剩一块黑着的死终端占地方，收回去。
            self.embedded = None
            self._hide_terminal()
        self._poll_hook_handoffs()
        self.after(1000, self._poll_running)

    def track_running(self, name, path, process, hwnd=None, hook=False):
        """把一个刚开出去的会话记进名单，界面立刻多出一行。返回那一行。

        hook=True 表示这个会话是带着自动刷交接文档那个 hook 起来的（只有启动时
        勾着「自动刷交接文档」才有）。只有这种会话的 Stop hook 会去写文档，也只有
        它们才值得盯。
        """
        item = None
        for known in self.running:
            if known["path"] == path:
                # 同一个目录又开了一个，只留最新那个，免得同一行重复
                known.update({"name": name, "proc": process, "hwnd": hwnd,
                              "hook": hook, "watched": False})
                item = known
                break
        if item is None:
            item = {"name": name, "path": path, "proc": process, "hwnd": hwnd,
                    "hook": hook, "watched": False}
            self.running.append(item)
        self._render_running()
        key = path_key(path)
        if hook and key not in self._hook_seen:
            # 记下现状当基线，否则状态文件里那条老记录会冒充"刚触发"。
            self._hook_seen[key] = hook_fired_at(read_hook_state(), path)
        return item

    def _watch_terminal(self, item, known, tries=0):
        """等下开出去那扇终端窗口冒出来，把句柄记到这一行上。

        窗口是 claude 那边异步建的，句柄只有比对启动前后两份窗口名单才拿得到
        ——比对的事在 launch_workspace 拍快照那一步就决定好了，这里只是轮着等。
        拿到句柄之前那一行照样显示，只是没有窗口操作那三个按钮。

        等到 30 秒就不等了。要是 Windows Terminal 被设成"新窗口开成已有窗口的
        标签页"，那就永远等不到——桌面上的窗口数压根没变，这边也就没法从里面
        认出哪一个是我们那扇。再轮下去只是白烧 CPU。

        收工（认出来了、退了、或者等超时）都盖一个 watched 戳。迁移流水线靠它判断
        刚重开的那扇是不是已经认完了，好决定能不能接着拍下一份窗口名单。
        """
        if item not in self.running or item["proc"].poll() is not None:
            item["watched"] = True
            return
        hwnd = fresh_terminal(known)
        if hwnd is not None:
            item["hwnd"] = hwnd
            item["watched"] = True
            self._render_running()
            return
        if tries >= 150:
            item["watched"] = True
            return
        self.after(200, lambda: self._watch_terminal(item, known, tries + 1))

    def _live_handle(self, item):
        """那一行记的句柄还作不作数。不作数就顺手抹掉，别留着摆个假按钮。"""
        hwnd = item.get("hwnd")
        if hwnd and ctypes.windll.user32.IsWindow(hwnd):
            return hwnd
        if hwnd:
            # 用户自己把那扇窗口关了，或者它已经退干净了
            item["hwnd"] = None
            self._render_running()
        self.feedback_var.set("「{}」的窗口已经关掉了。".format(item["name"]))
        return None

    def bring_running(self, item):
        hwnd = self._live_handle(item)
        if hwnd is None:
            return
        bring_next_to(self, hwnd)
        self.feedback_var.set("把「{}」的窗口挪到旁边了。".format(item["name"]))

    def top_running(self, item):
        hwnd = self._live_handle(item)
        if hwnd is None:
            return
        pinned = toggle_topmost(hwnd)
        if pinned is None:
            return
        self.feedback_var.set("「{}」{}。".format(
            item["name"], "已压在最上层" if pinned else "不再压在最上层"))

    def close_running(self, item):
        """关掉一扇独立窗口。问一声再关：WT 一扇窗口可能挂着好几个标签页，
        这一下会把里面别的标签页一起带走。"""
        hwnd = self._live_handle(item)
        if hwnd is None:
            return
        if not messagebox.askyesno(
                "关掉窗口",
                "关掉「{}」的窗口？\n\n如果那扇窗口里还开着别的标签页，"
                "会一起关掉。".format(item["name"])):
            return
        close_window(hwnd)
        item["hwnd"] = None
        self._render_running()
        self.feedback_var.set("已让「{}」的窗口关闭。".format(item["name"]))

    # ── 顶栏 ──

    def _probe_claude_version(self):
        """后台问一句 `claude --version`，结果通过队列丢回主线程。

        不能直接在主线程跑：claude 那个 shim 启动要一两秒，卡在启动路径上窗口
        就成了白板。也不能在工作线程里碰 tk——tkinter 不是线程安全的，那边的
        规矩是只有主线程能改控件。所以这里只往队列里塞字符串，
        _poll_running 每次醒过来顺手把它捞出来贴上。
        """
        exe = find_claude()
        if not exe:
            self.version_queue.put("没找到 claude")
            return

        def work():
            try:
                result = subprocess.run(
                    [exe, "--version"], capture_output=True,
                    creationflags=CREATE_NO_WINDOW, timeout=20)
                # 明确按 UTF-8 解：这台机器子进程默认走 cp936，版本串里万一有
                # 非 ASCII 会解出乱码甚至抛异常。
                text = result.stdout.decode("utf-8", "replace").strip()
                if not text:
                    text = result.stderr.decode("utf-8", "replace").strip()
                # 输出可能不止一行（新版偶尔会跟一句更新提示），只留第一行。
                if text:
                    self.version_queue.put("claude " + text.splitlines()[0])
                else:
                    self.version_queue.put("读不到版本号")
            except Exception:
                self.version_queue.put("读不到版本号")

        threading.Thread(target=work, daemon=True).start()

    def _update_model_chip(self, text, known):
        """模型名胶囊。text 是要显示的字，known 是说这个名字是不是一个真预设。

        配色跟着 known 走：认得出来的预设才用强调色，其余一律中性灰——没有预设
        和"预设已经对不上现在这份 settings.json"都得是灰的。空状态长得像链接或者
        报错都是误导，用户会以为点它有用、或者以为哪里错了。
        """
        self.status_var.set(text)
        self.model_chip.configure(bg=ACCENT_SOFT if known else HOVER_BG,
                                  fg=ACCENT if known else MUTED)

    # ── 模型 ──

    def refresh_models(self):
        self.model_list.clear()
        self.model_rows = {}
        presets = discover_presets()
        self.active_name = active_preset(presets)
        inner = self.model_list.inner

        if not presets:
            # 空状态是新手第一眼撞上的东西，别只丢一句"点右上角"。先把"模型"这
            # 个词解释了（备注留着，术语本身不动），再把两条路直接摆到眼前。
            tk.Label(inner, text="还没有模型。", bg=PAGE_BG, fg=TEXT,
                     font=font(10, True)).pack(anchor="w", padx=6, pady=(12, 3))
            tk.Label(inner, text="模型 = 你打算用哪家的 AI（DeepSeek、Kimi 这种）。\n"
                                 "已经在别的窗口里用着 claude 了，就直接导进来；"
                                 "没配过就手动填一个。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                     ).pack(anchor="w", padx=6, pady=(0, 9))
            buttons = tk.Frame(inner, bg=PAGE_BG)
            buttons.pack(anchor="w", padx=6)
            PillButton(buttons, "导入当前在用的", self._import_current, primary=True,
                       bg=PAGE_BG).pack(side="left")
            PillButton(buttons, "手动填", self._open_model_dialog, bg=PAGE_BG,
                       ).pack(side="left", padx=(8, 0))
            self._update_model_chip("未设置", False)
            self.model_list.fit()
            return

        for name in sorted(presets):
            path = presets[name]
            row = Row(inner, title=name, subtitle=describe_preset(path), marker=True,
                      active=(name == self.active_name), height=50,
                      on_click=lambda n=name, p=path: self.switch_model(n, p),
                      actions=[("移除", lambda n=name, p=path: self.remove_model(n, p)),
                               ("编辑", lambda n=name, p=path: self._open_model_dialog((n, p)))])
            row.pack(fill="x", pady=3)
            self.model_rows[name] = row
        self._update_model_chip(self.active_name or "自定义 / 未知",
                                bool(self.active_name))
        self.model_list.fit()

    def test_all_models(self):
        """并发打一遍所有预设，结果落在各自的卡片右侧。"""
        if self._testing:
            self.feedback_var.set("上一轮还在测，等等。")
            return
        presets = discover_presets()
        jobs = [(name, presets[name]) for name in self.model_rows if name in presets]
        if not jobs:
            self.feedback_var.set("没有可测的模型预设。")
            return

        self._testing = True
        self._results = queue.Queue()
        for row in self.model_rows.values():
            row.set_warn("测试中…", MUTED)
        self.feedback_var.set("正在测 {} 个模型…".format(len(jobs)))

        def worker(name, path):
            self._results.put((name, test_preset(path)))

        for name, path in jobs:
            threading.Thread(target=worker, args=(name, path), daemon=True).start()
        self.after(100, lambda: self._drain_results(len(jobs)))

    def _drain_results(self, remaining):
        """在主线程里收后台线程塞进队列的结果——Tk 控件只能主线程碰。"""
        try:
            while True:
                name, (ok, note) = self._results.get_nowait()
                remaining -= 1
                row = self.model_rows.get(name)
                if row is not None:
                    try:
                        row.set_warn(note, OK if ok else WARN)
                    except tk.TclError:
                        pass
        except queue.Empty:
            pass
        if remaining > 0:
            self.after(100, lambda: self._drain_results(remaining))
            return
        self._testing = False
        self.feedback_var.set("测完了：绿色=打得通，红色=打不通。")

    def switch_model(self, name, path):
        try:
            shutil.copyfile(path, SETTINGS_FILE)
        except Exception as e:
            messagebox.showerror("切换失败", "写入 settings.json 失败：\n{}".format(e))
            return
        self.feedback_var.set(
            "已切换到 {}（{}）。重启 Claude Code 后生效。".format(name, read_model(path)))
        self.refresh_models()
        self._offer_migration()

    # ── 换模型时把开着的会话接过去 ──

    def _offer_migration(self):
        """正在跑的会话还挂在旧模型上，逐个问要不要接着聊下去。

        claude 是启动时读一次 settings.json，已经跑起来的那些不会因为换了预设
        就跟着变，得重开。但重开不能是空会话——先让旧会话把交接文档写下来，新
        会话带着"先读 handoff.md 接着干"起来，中间那段上下文才不断。

        问的是"要不要续"这件事本身，所以措辞里得把后面那串动作说全：写完文档
        就关掉旧的那个。不然用户以为旧窗口会留着，结果被关掉了。
        """
        if self._task_running:
            self.feedback_var.set("上一轮换模型的活还没干完，等它跑完。")
            return
        sessions = list(self.running)
        if not sessions:
            return
        embedded_proc = self.embedded.process if self.embedded is not None else None

        picked = []
        for item in sessions:
            if messagebox.askyesno(
                    "要接着聊吗",
                    "「{}」这个会话还挂在旧模型上。\n\n"
                    "要给它写一份交接文档，用新模型接着上次的进度重新开吗？\n\n"
                    "会依次做：写交接文档 → 关掉旧窗口 → 用新模型重开。"
                    .format(item["name"])):
                picked.append({"item": item,
                               "was_embedded": item["proc"] is embedded_proc})
        if not picked:
            self.feedback_var.set("换好了。正在跑的会话保持原样，没动它们。")
            return

        self._task_queue = picked
        self._task_total = len(picked)
        self._task_running = True
        self.task_head_var.set("正在换模型")
        self.feedback_var.set("要接着聊的有 {} 个，一个一个来。".format(self._task_total))
        self._run_next_task()

    def _run_next_task(self):
        """流水线跑下一个会话：写文档 → 关旧的 → 用新模型重开。

        队头那个可能正被它自己的 hook 锁着（那个会话的 Stop hook 刚被逼着去写同一
        份 handoff.md）。挨个往下找一个没锁的换到队头先干；全锁着就原地等一秒再看。
        被锁的留在队里轮回来——不是跳过，等它刷完还是要补一份的。
        """
        if self._pending:
            # 上一个内嵌会话还在等控制台窗口冒出来。这会儿再开一个，
            # embed_workspace 会直接打回去、那个工作区就白排队了。
            self.after(500, self._run_next_task)
            return
        if not self._task_queue:
            self._finish_tasks()
            return

        free = None
        for pos in range(len(self._task_queue)):
            if not self._hook_locked(self._task_queue[pos]["item"]["path"]):
                free = pos
                break
        if free is None:
            # 别在这儿空转：hook 那边要么写完（锁自己就撤了）、要么十分钟超时。
            self._task_step("等「{}」的会话刷完交接文档，再接着来。".format(
                self._task_queue[0]["item"]["name"]))
            self.after(1000, self._run_next_task)
            return
        self._task_queue[0], self._task_queue[free] = (
            self._task_queue[free], self._task_queue[0])

        job = self._task_queue.pop(0)
        item = job["item"]
        self.task_count_var.set("第 {}/{} 个".format(
            self._task_total - len(self._task_queue), self._task_total))
        self.task_name_var.set(item["name"])
        self.write_handoff(item, done=lambda ok, why, j=job:
                           self._after_handoff(j, ok, why))

    def _after_handoff(self, job, ok, why):
        item = job["item"]
        if not ok:
            if why == HANDOFF_LOCKED_WHY:
                # 挑的时候还没锁，写的时候锁上了——hook 就卡在这零点几秒里触发的。
                # 放回队尾，等它刷完再来补这一份。
                self._task_queue.append(job)
                self._task_step("「{}」的会话正自己刷着交接文档，等它写完补一份。"
                                .format(item["name"]))
                self.after(1000, self._run_next_task)
                return
            # 文档没写成就别动这个会话了——空着手重开等于把上下文丢了。
            self._task_step("这一份没写成（{}），跳过。".format(why))
            self.after(2500, self._run_next_task)
            return
        self._task_step("交接文档写好了，先把旧的窗口关掉。")
        self.after(500, lambda: self._close_old_session(job))

    def _wait_until(self, pred, then, note, tries=0):
        """等条件成立再往下走，最多 MIGRATE_WAIT_TRIES 轮（每轮 MIGRATE_POLL_MS）。

        到上限就不再等，把 note 摆到进度条上照常往下走——各步自己都有兜底，卡死
        在这儿比早走一步更糟。
        """
        done = pred()
        if done or tries >= MIGRATE_WAIT_TRIES:
            if not done:
                self._task_step(note)
            then()
            return
        self.after(MIGRATE_POLL_MS,
                   lambda: self._wait_until(pred, then, note, tries + 1))

    def _close_old_session(self, job):
        item = job["item"]
        if job["was_embedded"] and self.embedded is not None:
            # 句柄得在 close_terminal 把 self.embedded 清掉之前先拿住。它只给个
            # WM_CLOSE，三秒还没退就硬掐（见 close_terminal），所以这一等有底。
            process = getattr(self.embedded, "process", None)
            self.close_terminal()
            if process is None:
                self._launch_migrated(job)
                return
            self._task_step("等旧的那个会话退出去，退了就用新模型重开。")
            self._wait_until(lambda: process.poll() is not None,
                             lambda: self._launch_migrated(job),
                             "旧会话没退利索，先往下走。")
            return
        hwnd = item.get("hwnd")
        item["hwnd"] = None
        if hwnd and ctypes.windll.user32.IsWindow(hwnd):
            close_window(hwnd)
            # 窗口是异步退的。等它真没了再开新的，免得两扇撞在同一块地方。
            u = ctypes.windll.user32
            self._task_step("等旧窗口关掉，关了就用新模型重开。")
            self._wait_until(lambda: not u.IsWindow(hwnd),
                             lambda: self._launch_migrated(job),
                             "旧窗口没关利索，先往下走。")
            return
        self._task_step("旧的关掉了，准备用新模型重开。")
        self._launch_migrated(job)

    def _launch_migrated(self, job):
        item = job["item"]
        self._task_step("用新模型重开「{}」。".format(item["name"]))
        entry = self.launch_workspace(item, cont=True, prompt=READ_HANDOFF_PROMPT)
        # 下一轮开新窗口前要先拍一份窗口名单、再比出哪扇是新开的。刚开出去那扇要是
        # 还没认出来，两扇新窗口会挤进同一次比对里，句柄认串。等它认出来再往下走。
        # 内嵌那条路不用等：那扇是本窗口里的一栏、不参与比对，而且 _run_next_task
        # 自己会躲着 _pending。
        if entry is None or entry.get("watched"):
            self._run_next_task()
            return
        self._wait_until(
            lambda: entry.get("watched") or entry["proc"].poll() is not None,
            self._run_next_task,
            "新窗口还没认出来，先接着往下走。")

    def _finish_tasks(self):
        self._task_running = False
        self._task_queue = []
        self._hide_task_area()
        self.feedback_var.set(
            "换模型这件活干完了：{} 个会话都带着交接文档用新模型重开了。".format(
                self._task_total))

    def remove_model(self, name, path):
        message = "删除模型预设「{}」？\n\n{}".format(name, path)
        if name == self.active_name:
            message += "\n\n它正是当前生效的模型，删掉后 settings.json 保持原样。"
        if not messagebox.askyesno("删除模型", message):
            return
        try:
            os.remove(path)
        except Exception as e:
            messagebox.showerror("删除失败", "删不掉 {}：\n{}".format(path, e))
            return
        self.feedback_var.set("已删除模型预设 {}。".format(name))
        self.refresh_models()


    # ── 工作区 ──

    def refresh_workspaces(self):
        self.ws_list.clear()
        inner = self.ws_list.inner
        workspaces = self.config_data["workspaces"]
        needle = self.ws_filter.get().strip().lower()

        # 筛选后编号会变，所以快捷键按的是"屏幕上第几个"，不是配置里的下标。
        view = [item for item in workspaces
                if needle in item["name"].lower() or needle in item["path"].lower()] \
            if needle else list(workspaces)
        self.ws_view = view

        if not workspaces:
            # 跟模型那边一个路数：先把"工作区"这个词解释掉，再说去哪儿加。原来只有
            # 一句"点右上角"，可"工作区"本身对没上手的人就不是个自明的词。
            tk.Label(inner, text="还没有工作区。", bg=PAGE_BG, fg=TEXT,
                     font=font(10, True)).pack(anchor="w", padx=6, pady=(12, 3))
            tk.Label(inner, text="工作区 = 一个项目文件夹，claude 就在那儿读写文件。\n"
                                 "右上角「＋ 添加工作区」既能新建一个文件夹，"
                                 "也能把已经有的目录加进来。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                     ).pack(anchor="w", padx=6, pady=(0, 9))
            # 上面那个分支（筛不出来）有 fit()，这条原来漏了——空列表时滚动区的高度
            # 还停在上一次 fit 的值上，跟"没有工作区"该占的高度对不上。
            self.ws_list.fit()
            return
        if not view:
            tk.Label(inner, text="没有匹配「{}」的工作区。".format(self.ws_filter.get().strip()),
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
            self.ws_list.fit()
            return

        for position, item in enumerate(view):
            path = item["path"]
            exists = os.path.isdir(path)
            # 右侧那格放"这行现在什么状态"：有没有交接文档、上次聊是多久以前。
            # 不往副标题里塞是有原因的——副标题是路径，长了会被省略号吃掉尾巴，
            # 而"上次聊"正好就是被吃掉的那部分，等于白写。右侧这格是右对齐的，
            # 永远不会被截断。
            if not exists:
                note, note_color = "目录不存在", WARN
            else:
                stamp = last_chat_time(path)
                parts = []
                if os.path.isfile(os.path.join(path, HANDOFF_FILE)):
                    parts.append("有交接文档")
                if stamp is not None:
                    parts.append("上次聊 " + humanize_ago(stamp))
                # 有交接文档是"现在能接着干"的信号，用强调色；只是时间就是中性灰。
                note = " · ".join(parts)
                note_color = ACCENT if parts[:1] == ["有交接文档"] else MUTED
            # actions 是从右往左摆的（下标 0 在最右边），所以这里的顺序要倒着念：
            # 屏幕上从左到右是 打开 ↑ ↓ 改名 移除。上移/下移用箭头不用词，是因为
            # 一行里塞五个两字词会糊成一片，而箭头没有认不出来的风险。
            Row(inner, title=item["name"], subtitle=path, height=52,
                warn=note, warn_color=note_color,
                # 行号就是 Ctrl+行号。9 以后没有号（按不到），但位置留着，
                # 免得后几行的标题跟前面错开一格。
                badge=str(position + 1) if position < 9 else "",
                on_click=lambda it=item: self.open_workspace(it),
                actions=[("移除", lambda it=item: self.remove_workspace(it)),
                         ("改名", lambda it=item: self.rename_workspace(it)),
                         ("↓", lambda it=item: self.move_workspace(it, 1)),
                         ("↑", lambda it=item: self.move_workspace(it, -1)),
                         ("开目录", lambda p=path: self.open_folder(p))],
                ).pack(fill="x", pady=3)
        self.ws_list.fit()

    def move_workspace(self, item, step):
        """把工作区在列表里挪一格。step 是 -1 上移、+1 下移。

        挪的是配置里 workspaces 的顺序——Ctrl+1~9 选的就是屏幕上第几个，而屏幕
        顺序就是这个数组的顺序。所以挪完立刻落盘，下次打开还是这个次序。

        用 is 而不是 == 找位置：配置里出现两条内容完全一样的工作区时，== 会一直
        认成第一个，按下去就没反应了。
        """
        workspaces = self.config_data["workspaces"]
        index = next((i for i, w in enumerate(workspaces) if w is item), None)
        if index is None:
            return
        target = index + step
        if not 0 <= target < len(workspaces):
            self.feedback_var.set("「{}」已经在{}了。".format(
                item["name"], "最上面" if step < 0 else "最下面"))
            return
        workspaces[index], workspaces[target] = workspaces[target], workspaces[index]
        save_config(self.config_data)
        self.refresh_workspaces()


    def launch_workspace(self, item, cont=False, prompt=None, autopilot=False):
        """真正把 claude 拉起来，新窗口还是塞进本窗口看那个勾。

        autopilot=True 是「AI 托管」那条路：这次会话无条件挂上自动继续那个 hook，
        不看底下那个勾——用户是在托管对话框里当场选的档，那个选择就该管这一次。
        「自动刷交接文档」是个独立的全局偏好，托管时照旧跟着勾走，两者取并集。
        """
        path = item["path"]
        permission = workspace_permission(item)
        settings = None
        # 两个行为共用一个 hook 入口，任何一个开着就得把 settings 挂上去；具体
        # 干哪几件事由命令行上的开关带过去，选择就此固化进这一份。
        handoff = self.auto_handoff_var.get()
        auto_continue = self.auto_continue_var.get() or autopilot
        if handoff or auto_continue:
            try:
                settings = ensure_hook_settings(handoff=handoff,
                                                auto_continue=auto_continue)
            except OSError as e:
                messagebox.showerror("挂 hook 失败",
                                     "写不了 hook 配置，这次就不挂了：\n{}".format(e))

        if self.embed_var.get():
            self.embed_workspace(item, cont, prompt, settings, permission)
            return None
        # 拍快照得赶在启动之前：窗口是 claude 那边异步建出来的，等它冒出来再
        # 去数，就分不清哪扇是这次新开的、哪扇是上一轮留下的了。
        known = terminal_windows()
        try:
            process = launch(path, cont, prompt, settings, permission)
        except Exception as e:
            messagebox.showerror("启动失败", "启动 claude 失败：\n{}".format(e))
            return
        # hook= 只在这条会话真挂上了 Stop hook 时才是真：没挂的话它自己那份
        # handoff_state.json 里永远不会有新记录，盯它就是白盯。
        entry = self.track_running(item["name"], path, process,
                                   hook=settings is not None)
        self._watch_terminal(entry, known)
        if autopilot:
            self.feedback_var.set(
                "已托管「{}」：它停下问你话时会自己接着说，权限 {}。关掉那扇窗口"
                "就结束。".format(item["name"], permission_option(permission)))
        else:
            self.feedback_var.set("已在新窗口启动{}（权限：{}）：{}".format(
                "（接着上次聊）" if cont else "", permission_option(permission), path))
        return entry

    def start_autonomy(self, item, task, tier=1):
        """「AI 托管」按下去之后：在那个工作区起个新会话，把任务当开场白。

        新会话（不 --continue）是故意的：托管是丢一件新活进去，不是接着上一段
        聊天。不带 --continue 时 claude 把命令行上那个位置参数当第一条用户消息
        送进去，这条路线上文书探针验过。
        """
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        if tier != 1:
            # 第 2、3 档在界面上是灰的，按不到；走到这儿说明配置被人手改成
            # 2 或 3 了。与其按一个没实现的东西开出去，不如什么都不开。
            messagebox.showinfo(
                "这一档还没做",
                "第 {} 档还没实现。现在能用的是第 1 档「让它自己定」。".format(tier))
            return
        # 先把 hook 配置文件写出来试一次：写不了就别开——开出去一个没挂上 hook
        # 的会话，用户以为托管着呢，其实它停下来就在那儿干等。
        try:
            ensure_hook_settings(handoff=self.auto_handoff_var.get(),
                                 auto_continue=True)
        except OSError as e:
            messagebox.showerror("挂 hook 失败",
                                 "写不了 hook 配置，这次没法托管：\n{}".format(e))
            return

        self.config_data["autonomy"] = tier
        self.config_data["autonomy_workspace"] = path
        save_config(self.config_data)
        self.launch_workspace(item, cont=False, prompt=task, autopilot=True)

    def launch_nth(self, number):
        """Ctrl+1~9：和鼠标点一样，也弹那个选择框。"""
        if 1 <= number <= len(self.ws_view):
            self.open_workspace(self.ws_view[number - 1])
            return "break"

    def _focus_filter(self, _event=None):
        self.filter_entry.focus_set()
        self.filter_entry.select_range(0, "end")
        return "break"

    # ── 内嵌终端 ──

    def _on_embed_toggle(self):
        """这个勾是"新开的会话塞不塞进本窗口"的偏好，跟自动刷文档那个一样记下来。

        取消勾选只是把当前嵌着的那个放回独立窗口，不打断它——所以这不是"关掉
        终端"，下一次启动照样按这个勾决定。
        """
        self.config_data["embed"] = self.embed_var.get()
        save_config(self.config_data)
        if not self.embed_var.get():
            self.detach_terminal()

    def _on_auto_handoff_toggle(self):
        self.config_data["auto_handoff"] = self.auto_handoff_var.get()
        save_config(self.config_data)
        if self.auto_handoff_var.get():
            # 这行是单行不折的，长了会被窗口边裁掉，所以说不了 hook 文件在哪；
            # 文件在配置目录的 hooks\ 下面，顶栏那个按钮一步就到。
            self.feedback_var.set(
                "已挂上 Stop hook：聊够 {} 轮且隔 {} 分钟，新开的会话会自己把 {} "
                "刷一遍，多用掉的 token 算在你账上。".format(
                    HANDOFF_MIN_TURNS, HANDOFF_COOLDOWN // 60, HANDOFF_FILE))
        else:
            self.feedback_var.set(
                "新开的会话不再自动刷交接文档（「自动继续」那个勾不受影响）。")

    def _on_auto_continue_toggle(self):
        self.config_data["auto_continue"] = self.auto_continue_var.get()
        save_config(self.config_data)
        if self.auto_continue_var.get():
            self.feedback_var.set(
                "已挂上 Stop hook：它停下问话时替它接一句「接着干，自己定」，连着推 "
                "{} 轮就放行；隔 {} 分钟重新数，它自己说「已完成」也会停。".format(
                    AUTO_CONTINUE_MAX, AUTO_CONTINUE_RESET // 60))
        else:
            self.feedback_var.set(
                "新开的会话不再自动继续（「自动刷交接文档」那个勾不受影响）。")

    def embed_workspace(self, item, cont=False, prompt=None, settings=None,
                        permission=None):
        """把 claude 塞进本窗口。已经有一个的话先把它放出去，不打断它。"""
        if self._pending:
            self.feedback_var.set("上一个还在启动，稍等一下。")
            return
        self.detach_terminal()
        try:
            process, known = spawn_console(item["path"], cont, prompt, settings,
                                           permission)
        except Exception as e:
            messagebox.showerror("启动失败", "启动 claude 失败：\n{}".format(e))
            return
        self._pending = (process, known, item, settings, 0)
        self.feedback_var.set("正在把 claude 装进窗口…")
        self.after(80, self._poll_console)

    def _poll_console(self):
        """控制台窗口是异步建的，拿到之前一直轮询，别把界面卡住。"""
        process, known, item, settings, tries = self._pending
        hwnd = fresh_console(known)
        if hwnd is None:
            if tries >= 75:
                self._pending = None
                self.feedback_var.set("等不到控制台窗口，claude 可能已经退出了。")
                return
            self._pending = (process, known, item, settings, tries + 1)
            self.after(80, self._poll_console)
            return

        self._pending = None
        self.term_name_var.set(item["path"])
        self.term_head.pack(fill="x", pady=(0, 6))
        self.term_holder.pack(fill="both", expand=True)
        self._room_for_terminal(True)
        self.update_idletasks()
        self.embedded = EmbeddedConsole(process, hwnd, self.term_holder)
        self.track_running(item["name"], item["path"], process,
                           hook=settings is not None)
        self.feedback_var.set(
            "claude 已内嵌在 {}。conhost 没有字体回退，个别符号会是方框。"
            .format(item["path"]))

    def _room_for_terminal(self, want):
        """内嵌时右边多长出一栏，左边那列一个像素都不动；退出去还原。

        早先的写法是把左列收窄到 SIDE_WIDTH 再让终端占剩下的，结果一内嵌，
        主界面就被重新排了一遍版：工作区那几行本来挤着路径和四个按钮，一窄
        全被截成「De..上次聊…」。终端要地方就从右边往外长、窗口跟着变宽，
        前头那半张脸原样留着——这才是用户要的「不影响本来的布局」。

        宽度这块只有一个出处：先摆好栏，再让 _apply_min_size 把它算进下限，窗口
        就照那个下限变宽/还原。这样手动缩窗口也缩不过去，左列始终是原来那么宽。
        """
        if want:
            self._width_before_embed = self.winfo_width()
            self.panel.pack(side="left", fill="both", expand=False,
                            padx=(SIDE_GAP, 0))
            self._apply_min_size()
            self._widen(self.minsize()[0])
        else:
            was = self._width_before_embed
            self.panel.pack_forget()
            self._width_before_embed = None
            self._apply_min_size()
            if was:
                self._widen(was)
        self.model_list.fit()
        self.ws_list.fit()

    def _widen(self, width):
        """改宽度，并保证整扇窗口还留在虚拟桌面内——贴到右边缘就往左挪。"""
        if not width:
            return
        u = ctypes.windll.user32
        left = u.GetSystemMetrics(76)
        right = left + u.GetSystemMetrics(78)
        width = min(width, right - left)
        x, y = window_position(self)
        if x + width > right:
            x = max(left, right - width)
        self.geometry("{}x{}".format(width, self.winfo_height()))
        self.update_idletasks()
        place_window(self, x, y)

    def detach_terminal(self):
        """把内嵌的控制台放回独立窗口，进程不动。"""
        if not self.embedded:
            return
        try:
            self.embedded.detach()
        except Exception:
            pass
        self.embedded = None
        self._hide_terminal()

    def close_terminal(self):
        """关掉内嵌的这个会话——等同点它标题栏的 X。

        先好好说：给控制台发 WM_CLOSE，claude 有工夫把手头的活收尾。三秒还没
        退就掐掉 conhost，那里面的 cmd 和 claude 会跟着一起走；不然按钮按下去
        可能半天没动静，看着像坏的。
        """
        embedded, self.embedded = self.embedded, None
        if embedded is None:
            return
        try:
            embedded.close()
        except Exception:
            pass
        process = getattr(embedded, "process", None)
        self._hide_terminal()
        self.feedback_var.set("正在关掉这个会话…")
        if process is not None:
            self.after(3000, lambda: process.poll() is None and process.terminate())

    def _hide_terminal(self):
        """把右边那栏收回去，宽度还原。进程的事调用方自己管。"""
        self.term_head.pack_forget()
        self.term_holder.pack_forget()
        self.term_name_var.set("")
        self._room_for_terminal(False)

    def _on_term_resize(self, _event):
        if self.embedded and not self._placing:
            self._placing = True
            try:
                self.embedded.place()
            finally:
                self._placing = False

    def open_folder(self, path):
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        os.startfile(path)

    def _open_tool_dir(self):
        """顶栏那个按钮：把 ~/.claude_tool 用资源管理器打开。

        配置、模型预设、交接文档的记账本都在这儿，手改的时候比在界面里点来点去快。
        目录不存在就先建出来——首次启动本来就该有，但用户手删了也别报个错就完事。
        """
        try:
            os.makedirs(TOOL_DIR, exist_ok=True)
            os.startfile(TOOL_DIR)
        except OSError as exc:
            messagebox.showerror("打不开", "打开 {} 失败：\n{}".format(TOOL_DIR, exc))

    def write_handoff(self, item, done=None):
        """在那个目录里 fork 一份会话，让它写 handoff.md。

        done 传了的话，写完会叫它一声 done(是否成功, 原因)。换模型那条流水线
        靠这个回调接着往下走——它是"写完 → 关旧的 → 重开"里的第一步。
        """
        path = item["path"]
        if self._hook_locked(path):
            # 这个目录的 handoff.md 正被它自己会话的 Stop hook 写着。两个进程往
            # 同一份文件上写，出来的是两份搅在一起的东西，只能等。手动那条路要
            # 把话说全：这会儿接着说下去的话，不在这份文档的材料里。
            if done is None:
                messagebox.showinfo(
                    "正在交接",
                    "「{}」的会话自己正在刷交接文档（Stop hook 刚被触发），"
                    "这一份先不写了。\n\n"
                    "等它刷完再点一次；刷的这会儿在那个会话里接着说的话，"
                    "不会进这份文档，要的话得重新交接一次。".format(item["name"]))
            else:
                done(False, HANDOFF_LOCKED_WHY)
            return
        if self._task_running and done is None:
            # 手动点的（流水线自己会带 done）。流水线两步之间有空档，这会儿再插
            # 一份进去，两边会抢进度区和那一个 claude 进程。
            self.feedback_var.set("正在换模型的流水线上，等它跑完再单独整理。")
            return
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            if done is not None:
                done(False, "目录不存在")
            return
        if self._handoff_proc is not None and self._handoff_proc.poll() is None:
            self.feedback_var.set("上一份交接文档还在写，等它写完。")
            if done is not None:
                done(False, "上一份还在写")
            return

        target = os.path.join(path, HANDOFF_FILE)
        try:
            before = os.path.getmtime(target)
        except OSError:
            before = 0.0

        # 输出导到临时文件而不是管道：没人读的管道写满会把子进程卡死。
        log = tempfile.NamedTemporaryFile(prefix="handoff_", suffix=".log",
                                          delete=False)
        try:
            self._handoff_proc = subprocess.Popen(
                [claude_exe(), "-c", "--fork-session", "-p", HANDOFF_PROMPT,
                 "--allowedTools", HANDOFF_TOOLS],
                cwd=path, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            log.close()
            os.remove(log.name)
            messagebox.showerror("启动失败", "叫不起 claude：\n{}".format(e))
            if done is not None:
                done(False, "叫不起 claude")
            return
        log.close()

        self._handoff_ctx = (item, target, before, done)
        self._handoff_log = log.name
        # 摆出进度区。流水线里进这一步时它已经在摆着了，_show_task_area 不会
        # 重置秒表——那个"已用多久"得从这一趟活的开头算起，不是从每小步算起。
        if not self._task_running:
            self.task_head_var.set("正在整理交接文档")
            self.task_name_var.set(item["name"])
        self._show_task_area()
        if not self._task_running:
            self._task_started = time.time()
        self._task_step("正在读这个目录的会话记录，写出 {}".format(HANDOFF_FILE))
        self.after(400, self._poll_handoff)

    def _poll_handoff(self):
        """claude 是另一个进程，只能轮询着等它。"""
        proc = self._handoff_proc
        if proc is None:
            return
        if proc.poll() is None:
            self.after(500, self._poll_handoff)
            return

        self._handoff_proc = None
        item, target, before, done = self._handoff_ctx
        self._handoff_ctx = None
        detail = self._read_handoff_log()

        if os.path.exists(target) and os.path.getmtime(target) > before:
            ok, why = True, "写好了：{}".format(target)
            # 这份刚写完，20 分钟内别让那个会话的 hook 再刷一遍——不然它下一停
            # 就够条件，往同一份文件上又写一遍，两份搅在一起。
            self._note_handoff_written(item["path"])
        elif proc.returncode != 0:
            ok, why = False, detail or "退出码 {}".format(proc.returncode)
        else:
            ok = False
            why = "跑完了但没生成 {}——这个目录可能还没聊过，没有会话可以交接。".format(
                HANDOFF_FILE)

        self.feedback_var.set("「{}」的交接文档{}".format(
            item["name"], why if ok else "没写成：" + why))
        if done is None:
            # 手动点那个按钮：没有下一步，把进度区收回去。
            self._hide_task_area()
            return
        done(ok, why)

    def _read_handoff_log(self, limit=160):
        """把 claude 吐的最后一句拿来做失败原因，然后清掉临时文件。"""
        text = ""
        try:
            with open(self._handoff_log, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError:
            pass
        try:
            os.remove(self._handoff_log)
        except OSError:
            pass
        self._handoff_log = None
        return " ".join(text.split())[-limit:]

    def rename_workspace(self, item):
        new_name = simpledialog.askstring("重命名工作区", "显示名称：",
                                          initialvalue=item["name"], parent=self)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showerror("名称为空", "显示名称不能为空。")
            return
        item["name"] = new_name
        save_config(self.config_data)
        self.refresh_workspaces()
        self.feedback_var.set("已重命名为 {}。".format(new_name))

    def remove_workspace(self, item):
        if not messagebox.askyesno(
                "移除工作区",
                "把「{}」从列表里拿掉？\n目录本身不会被删除。".format(item["name"])):
            return
        index = next((i for i, w in enumerate(self.config_data["workspaces"])
                      if w is item), -1)
        if index < 0:
            return
        del self.config_data["workspaces"][index]
        save_config(self.config_data)
        self.refresh_workspaces()
        self.feedback_var.set("已移除 {}。".format(item["name"]))

    def _set_workplace(self, base):
        """换默认工作区目录：老的那个从扫描列表里换掉，别的扫描目录留着。"""
        old = self.config_data.get("workplace")
        self.config_data["workplace"] = base
        roots = list(self.config_data["roots"])
        if old:
            roots = [r for r in roots if path_key(r) != path_key(old)]
        if all(path_key(r) != path_key(base) for r in roots):
            roots.insert(0, base)
        self.config_data["roots"] = roots


    def rescan(self):
        added = merge_scanned(self.config_data)
        save_config(self.config_data)
        self.refresh_workspaces()
        if added:
            self.feedback_var.set("新发现 {} 个工作区：{}".format(len(added), "、".join(added)))
        else:
            self.feedback_var.set("扫描目录下没有新工作区。")
