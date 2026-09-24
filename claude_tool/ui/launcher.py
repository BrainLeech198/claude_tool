"""主窗口。

左右两栏：左边模型，右边工作区；底下一排开关，中间一条"正在跑"。
对话框（加模型、加工作区、点工作区那次询问）在 ui/dialogs.py，以 mixin
的形式挂在这个类上——它们跟主窗口共享 config_data / feedback_var 那些状态。
"""
import os
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from claude_tool.paths import (
    CONFIG_FILE,
    ICON_FILE,
    PRESET_DIR,
    TOOL_DIR,
)
from claude_tool import install
from claude_tool import mover
from claude_tool import versions
from claude_tool.theme import (
    ACCENT,
    ACCENT_SOFT,
    ALERT_BG,
    BORDER,
    HOVER_BG,
    MUTED,
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
from claude_tool.presets import migrate_presets
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
    CREATE_NO_WINDOW,
    claude_exe,
    find_claude,
)
from claude_tool.handoff import (
    AUTO_CONTINUE_MAX,
    AUTO_CONTINUE_RESET,
    HANDOFF_FILE,
    HANDOFF_TOOLS,
    autonomy_caption,
    ensure_hook_settings,
    handoff_prompt,
    is_git_repo,
    tier_flags,
)
from claude_tool.host import (
    EMBED_SUPPORTED,
    WINDOW_CONTROL,
    EmbeddedConsole,
    bring_next_to,
    close_window,
    fresh_console,
    fresh_terminal,
    open_path,
    open_url,
    place_window,
    screen_bounds,
    spawn_console,
    spawn_terminal,
    terminal_windows,
    toggle_topmost,
    trash_path,
    window_alive,
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
from claude_tool.ui.models import ModelsMixin
from claude_tool.ui.update import UpdateMixin


MODEL_MAX, WORKSPACE_MAX = 300, 480
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
MIN_HEIGHT_FLOOR = 660
# 关窗时留给内嵌会话收尾的时间（毫秒）。过了这个点还赖着，销毁父窗口自然会
# 把它连窗口带进程一起带走。
CLOSE_GRACE_MS = 800

# 搬工作区时主线程去后台线程那儿收进度的节奏。比上面那个松一点：复制是 IO
# 活，一次复制几百个小文件也就几毫秒，追得太紧是白烧 CPU；而几百毫秒的延迟
# 摆在"已经复制了 N 个"那行字上，人眼看不出来。
MOVE_POLL_MS = 250

def default_window_size(screen_w, screen_h):
    """没存过尺寸时窗口开多大：写死 900x950。

    中间试过一版按屏幕比例算（宽 0.36、高 0.72，两头卡区间），理由是免得在
    768 高的笔记本上顶到屏幕外。但那版让"多大合适"变成了跟屏幕有关的事——
    同一份界面在不同机器上开出来不一样大，而这里其实就那么两块列表，该由里
    面的内容定。所以退回写死。

    screen_w/screen_h 还是要的：屏幕本身就比 900x950 小的，按屏幕收一下，不
    然会有一截落在屏幕外够不着。位置那边另有 place_window 管。
    """
    return min(900, screen_w - 20), min(950, screen_h - 100)


class Launcher(UpdateMixin, ModelsMixin, LauncherDialogs, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude 启动器")
        self.geometry("{}x{}".format(*default_window_size(
            self.winfo_screenwidth(), self.winfo_screenheight())))
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
        self._handoff_ctx = None          # (工作区, 目标文件, 写之前的 mtime)
        self._handoff_log = None
        # 这个启动器开出去、还开着的 claude：[{name, path, proc, hwnd}]
        self.running = []
        self._running_shown = False       # 「正在跑」那块现在摆没摆出来
        # 换模型时那条流水线：关旧的 → 用新模型接着重开，一个接一个。队列就是
        # "还没轮到的"，从头取，所以 _task_total 是当初选了几个、_task_queue 是
        # 还剩几个，第几个 = 总数 - 剩的 + 1。
        self._task_queue = []
        self._task_total = 0
        self._task_running = False
        self._task_visible = False
        self._task_text = None
        self._task_started = 0.0
        self._task_timer = None
        # 搬工作区：整个目录复制 → 对账 → 旧的丢回收站。跟换模型那条流水线共用
        # 同一块进度区，所以两者互斥（见 _busy）。复制跑在后台线程里，进度走
        # 队列回主线程——几个 G 的目录在主线程上复制，窗口会僵在那儿不动。
        self._move_running = False
        self._move_job = None
        self._move_queue = queue.Queue()
        self._jobs_shown = False          # 顶上那块进度容器现在摆没摆出来
        self.version_queue = queue.Queue()   # 后台问出来的 claude 版本号，主线程来取
        # 本机版本号拿到手之后，"要不要更新"那一问的结果走这条队列回主线程。
        # 队列里每项是 (是不是手动点的, versions.check() 的返回值或 None)。
        self.version_check_queue = queue.Queue()
        self._local_version = ""     # 顶栏那个版本号的原文，比大小时拿它跟网上比
        self._update_pill = None     # 「有新版」那个胶囊，第一次查出落后才建
        # 用户从「有新版」那颗胶囊点进安装面板、真把 claude 升级成功了的话，这一格
        # 就是 True：_poll_running 下次读到版本号（升完那一下会重问一遍，见
        # _recheck_claude）时，就算"自动查新版"没勾也得去网上问一次——刚升完，顶上
        # 那个版本号和那颗「有新版」总得跟着变，不然用户以为白升了。
        self._force_version_check = False
        # 启动器**自己**有没有新版，跟上面那套是分开的两件事（一个问 npm，一个问
        # 我们自己的官网）。这条队列里跑三种消息（见 _poll_self_update）：
        #   ("check", 是不是手动点的, versions.launcher() 的结果或 None)
        #   ("progress", 已下字节, 总字节或 None)
        #   ("done"/"failed", 落盘的路径 / 出错说明)
        self.self_queue = queue.Queue()
        self._self_pill = None       # 「下载最新版 X」那颗，查出落后才建
        self._self_release = None    # 那颗胶囊对应的清单记录，下载要用
        self._downloading = False    # 下载进行中：那颗胶囊点第二下不理它
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
        # 勾着才问官网。这条不依赖本机 claude 的版本号（问的是我们自己），所以
        # 不跟 claude 那条一起挂在 version_queue 上，直接起。
        if self.auto_self_var.get():
            self._start_self_check()
        self.after(1000, self._poll_running)
        self.ws_filter.trace_add("write", lambda *_: self.refresh_workspaces())
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Control-f>", self._focus_filter)
        self.bind_all("<Control-F>", self._focus_filter)
        for number in range(1, 10):
            self.bind_all("<Control-Key-{}>".format(number),
                          lambda _e, n=number: self.launch_nth(n))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 内嵌的那个是贴在终端栏上的独立窗口，本窗口自身的挪动它跟不动——挪窗口
        # 时 Tk 只给顶层窗口发 <Configure>，终端栏那边一个都不发，_on_term_resize
        # 那个绑定管不着（量过）。
        self.bind("<Configure>", self._on_window_move)
        self._restore_geometry()

    def _apply_min_size(self):
        """窗口下限按内容量出来，别写死。

        底下那排开关（内嵌终端 + 两个挂 Stop hook 的行为 + 自动查新版，分三行摆）
        是最宽的一块，几段文字加上间距比左列那几块都宽；原先写死的下限 560
        （默认宽 640）都装不下，右边那个勾的标题会被切掉一截，字号再大点的机器
        切得更多。

        量的只有那排开关和左列这两块**定死**的东西，不去拿整窗的 reqwidth：顶栏
        的模型名、底下那行反馈都是会变长的字符串，它们一长整窗的自然宽度就跟着
        跳，下限跟着跳，用户就会看到窗口自己忽大忽小。

        高度**不**跟着算：中间那两块列表是可滚动的，它们的自然高度不该拿来当下限
        ——而且这个数还跟问的时机有关，在 _build_ui() 刚建完时问是 823，等列表
        fit() 完再问是 410，差一倍。高度就守 MIN_HEIGHT_FLOOR 这个可用底线。

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
        """把上次关窗时的位置和尺寸摆回去，存多少就用多少。

        早先高度是"只往上记、不往下记"（比默认矮就按默认开），当时的理由是
        用户嫌过窗口矮。实践下来这条是错的：他手调的那个高度才是他的偏好，
        启动器拿默认值去盖，出来的效果就是"有点太高了，稍微矮一点"。真存了
        个矮到没法用的，minsize 那道下限也兜得住（见 _apply_min_size）。
        """
        saved = self.config_data["window"]
        if not saved:
            return
        self.geometry("{}x{}".format(saved["w"], saved["h"]))
        self.update_idletasks()
        place_window(self, saved["x"], saved["y"])

    def _busy(self):
        """启动器手上有活占着进度区没有。

        换模型那条流水线和搬工作区共用同一块进度区和同一条"正在干什么"，同一
        时刻只能有一个在跑——两个都上，进度条和那几行字会被两边抢着改。
        """
        return self._task_running or self._move_running

    def _on_close(self):
        if self._busy() and not messagebox.askyesno(
                "还有活没干完",
                "启动器手上的活还没弄完（换模型那条流水线，或者正在搬工作区）。\n\n"
                "现在关掉的话，可能正好卡在中间那一步：某个会话停在「旧的已经关"
                "了、新的还没开」，那一轮上下文就断了；搬工作区停在复制了一半，"
                "新位置留下半份、旧的还在。\n\n真要现在关吗？"):
            return
        if self.embedded is not None:
            # 内嵌的那个认了本窗口当 owner，窗口一销毁它跟着一起没。所以不能只是
            # "放它走"（那等于连窗口带会话一起抽掉），也不能说完就立刻销毁（等于
            # 拔电）。先正经道个别——等同点它标题栏的 X，给 claude 一点时间把手
            # 上的活收尾——歇一下再关自己。
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
        # 手动问一次。底下那个勾管的是"开启动器时自动问一次"，这个按钮管的是
        # "我现在就想知道"，不受那个勾影响。
        check = PillButton(line, "查更新", self.check_claude_update, bg=PANEL_BG)
        check.pack(side="left", padx=(10, 0))
        Tip(check, "问一次网上 claude 出到哪一版了，跟本机这个比一比")
        # 查出本机落后了才摆出来的胶囊，平时不占地方：顶栏就一条，多挂一个常驻
        # 控件就会把版本号挤掉（早先往里塞字就是这么把版本号截了半截的）。
        self.top_line = line
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
        # 内嵌终端只在 Windows 上有（见 host.EMBED_SUPPORTED）。别处这个勾一律
        # 当没勾——配置文件是跟着用户走的，从 Windows 拷过来的 launcher.json
        # 里很可能留着 embed=true。
        self.embed_var = tk.BooleanVar(
            value=EMBED_SUPPORTED and bool(self.config_data.get("embed")))
        # 会花用户 token、会替用户拍板的功能，默认关；这几个勾只影响新开的会话，
        # 不动已经跑着的
        self.auto_continue_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_continue")))
        self.auto_version_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_version_check")))
        # 查的是启动器自己。跟旁边那条一样默认关：都联网。
        self.auto_self_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_self_update")))
        # 括号里写的是各自的真名：内嵌那条走的是 conhost（不是默认的 Windows
        # Terminal，字形回退差些），另一条挂的是 Claude Code 的 Stop hook。
        # 熟练用户要的是这几个词，好去翻文档、翻配置文件；只写大白话他就得猜。
        #
        # 拆几行摆，不挤一行：窗口下限是按这排开关的自然宽度量的（见
        # _apply_min_size），挤成一行会把下限顶宽一大截。行按功能分——上排是终端
        # 怎么开，中排是挂 Stop hook 那个行为，最后一行是联网那件事。
        #
        # 每行都从第 0 列起头。第 0 列空着、第 1 列有东西的那种摆法（"自动继续"
        # 先前就落在 base 行第 1 列）看着像缩进了一格，四个勾的左边缘参差不齐——
        # 只有最后一行那两个"联网"是特意并排的，第 1 列才该有东西。
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
            (self.auto_self_var, "自动查启动器新版（联网）",
             self._on_self_check_toggle, base + 1, 1),
        ]
        for var, text, command, row, col in rows:
            tk.Checkbutton(switches, text=text, variable=var, command=command,
                           bg=PAGE_BG, fg=TEXT, font=font(9), activebackground=PAGE_BG,
                           selectcolor=PANEL_BG, highlightthickness=0, bd=0,
                           ).grid(row=row, column=col, sticky="w",
                                  padx=(0, 18), pady=(0, 2))
        # 上面那行括号里说的是"勾上会怎么样"，可 conhost、Stop hook 这两个词本身
        # 还是黑话。一行注解把词解释掉，术语照留——熟手认词，新手读注解。
        # 单独一行摆（不塞进勾的标题里）：窗口下限量的是 switches 那块的自然宽度
        # （见 _apply_min_size），注解放进去会把下限再顶宽一截。
        #
        # conhost 那句只在 Windows 上留着：别处没有那个勾，解释了也无处可指。
        note = "Stop hook 是 claude 干完活停下来时触发的动作。"
        if EMBED_SUPPORTED:
            note = "conhost 是 Claude Code 自带的终端窗口；" + note
        tk.Label(footer, text=note, bg=PAGE_BG, fg=MUTED, font=font(9),
                 anchor="w").pack(fill="x", padx=20, pady=(4, 0))
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
        # 非 Windows 上干脆不建：那一栏从头到尾没有会摆出来的时候。
        self.panel = None

        # 先建好但不 pack——没会话在跑的时候，界面上不该看出有这么一块。
        # 一有活的会话，_render_running() 就把它插到模型区上面。
        self._build_running(self.side)
        # 交接进度那块也先建好不摆出来，有活的时候插到「正在跑」上面
        self._build_tasks(self.side)

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
            search=self.ws_filter, top=self._build_workspace_rows)

        if EMBED_SUPPORTED:
            self.panel = tk.Frame(body, bg=PAGE_BG)
            self._build_terminal(self.panel)

    def _build_claude_banner(self):
        """找不到 claude 时在顶上挂一条。找到就什么都不摆。

        右起第一颗是「帮我装 claude」，开那个安装面板（见 dialogs 那边的
        _open_install_dialog）：哪几条装法这台机器走得通、每条要跑什么命令、
        跑起来的输出，都在那一个窗口里。早先这儿直接挂一颗 winget 一键装，
        别的平台还挂不出来——面板把这些都收进去了，这条横幅只留"进去看看"
        和"再看看装上了没"。
        """
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
        install_pill = PillButton(inner, "帮我装 claude", self._open_install_dialog,
                                  primary=True, bg=ALERT_BG)
        install_pill.pack(side="right")
        Tip(install_pill, "照这台机器能走的装法一条条摆出来，挑一条就能装")
        tk.Frame(banner, bg=BORDER, height=1).pack(fill="x", side="bottom")

    def _recheck_claude(self):
        self.claude_path = find_claude()
        if self.claude_path is None:
            self.feedback_var.set("还是没找到。装完可能要重开一次启动器，PATH 才会刷新。")
            return
        self.banner.pack_forget()
        # 顺手把版本号重问一遍：刚装上的那个 claude 是什么版本，顶栏该跟着变。
        # 勾着"自动查新版"的话，这一问带出来的那次联网查也就跟着跑了。
        self._probe_claude_version()
        self.feedback_var.set("找到 claude 了：{}".format(self.claude_path))

    def _open_install_page(self):
        """开官方那份说明。安装面板里那颗「打开官网说明」走这儿。

        跟面板里是同一个地址（install.DOCS_URL），别在这儿另写一份——这个地址
        官方是会挪的，两处各存一份迟早对不上。
        """
        try:
            open_url(install.DOCS_URL)
        except OSError as e:
            messagebox.showerror("打不开", "拉不起浏览器：\n{}".format(e))
            return
        self.feedback_var.set("已在浏览器里打开安装说明。")

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

    def _build_workspace_rows(self, parent):
        """「工作区」标题底下那两行：托管入口，和默认工作区在哪儿。

        托管摆在这儿而不是顶栏，是因为托管要挑的就是一个工作区——「托管哪个目录、
        用哪一档」跟在哪儿挑工作区是同一件事，凑在一起看才顺。

        默认工作区那行同理：它管的就是底下这张列表里的东西从哪儿冒出来的（新建
        文件夹建在哪儿、扫描扫哪儿）。这行早先撤过一版，结果这个设置只剩「新建
        文件夹」对话框里一条路能改，而且改完不点「创建」还会白改——所以请回来了，
        让它跟它管的那张列表挨着。
        """
        line = tk.Frame(parent, bg=PAGE_BG)
        line.pack(fill="x", pady=(0, 8))
        PillButton(line, "AI 托管", self._open_autonomy_dialog, primary=True,
                   bg=PAGE_BG).pack(side="left")
        tk.Label(line, text="布置一个任务，交给它自己跑", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(10, 0))

        base = tk.Frame(parent, bg=PAGE_BG)
        base.pack(fill="x", pady=(0, 8))
        tk.Label(base, text="默认工作区", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left")
        # 两个按钮先占右边，路径再铺在剩下的地方。反过来 pack 的话，路径一长
        # Tk 不会自己裁，按钮会被顶出框外。
        PillButton(base, "打开",
                   lambda: self.open_folder(self.config_data["workplace"]),
                   bg=PAGE_BG).pack(side="right")
        PillButton(base, "更改目录", lambda: self.pick_workplace(self), bg=PAGE_BG,
                   ).pack(side="right", padx=(6, 0))
        self.workplace_var = tk.StringVar(value=self.config_data["workplace"])
        tk.Label(base, textvariable=self.workplace_var, bg=PAGE_BG, fg=TEXT,
                 font=font(9), anchor="w").pack(side="left", padx=(8, 8),
                                                fill="x", expand=True)

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
                # 这几样只有独立窗口有——内嵌那个本来就长在本窗口里，挪位置和
                # 压顶层对它没意义（那一栏自己还有「放到独立窗口」「关掉」）。
                #
                # 挪位置、压顶层要能指挥别人的窗口，非 Windows 上没这回事（见
                # host.WINDOW_CONTROL），那两个按钮就不摆；「关掉」那边做得到
                # ——按进程树发 SIGTERM，所以照摆。
                if WINDOW_CONTROL:
                    ops += [("移过来", lambda it=item: self.bring_running(it)),
                            ("置顶", lambda it=item: self.top_running(it))]
                ops.append(("关掉", lambda it=item: self.close_running(it)))
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

        现在装的是启动器自己起的活：换模型那条流水线、手动点出来的「整理交接
        文档」。都是"跑好几秒往上"的事。不摆个一直在动的东西，用户会以为窗口
        卡死了、把正写到一半的 claude 关掉。
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
        """进度那一块摆不摆，以及容器该不该跟着出现。"""
        self.task_frame.pack_forget()
        if self._task_visible:
            self.task_frame.pack(fill="x")

        showing = self._task_visible
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
        """每秒一趟：窗口还开着没、版本号回来了没。

        都走这条心跳是因为它是现成的、启动时就起来的定时器；再造几个不值当。
        """
        try:
            while True:
                text = self.version_queue.get_nowait()
                self.version_var.set(text)
                self._local_version = text
                # 勾着"自动查新版"才联网问一次；不勾就只把版本号贴上顶栏。没读到
                # 版本号（没装 claude）就别去联那次网了，比不出什么来。刚在安装
                # 面板里升成功的（_force_version_check）是个例外：用户自己点的
                # 升级，顶上这几处总得跟着变。
                if versions.parse(text) and (self.auto_version_var.get()
                                             or self._force_version_check):
                    self._force_version_check = False
                    self._start_version_check()
        except queue.Empty:
            pass
        self._poll_version_check()
        self._poll_self_update()

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
        self.after(1000, self._poll_running)

    def track_running(self, name, path, process, hwnd=None):
        """把一个刚开出去的会话记进名单，界面立刻多出一行。返回那一行。"""
        item = None
        for known in self.running:
            if known["path"] == path:
                # 同一个目录又开了一个，只留最新那个，免得同一行重复
                known.update({"name": name, "proc": process, "hwnd": hwnd,
                              "watched": False})
                item = known
                break
        if item is None:
            item = {"name": name, "path": path, "proc": process, "hwnd": hwnd,
                    "watched": False}
            self.running.append(item)
        self._render_running()
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
        if hwnd and window_alive(hwnd):
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
            has_handoff = exists and os.path.isfile(os.path.join(path, HANDOFF_FILE))
            if not exists:
                note, note_color = "目录不存在", WARN
            else:
                stamp = last_chat_time(path)
                parts = []
                if has_handoff:
                    parts.append("有交接文档")
                if stamp is not None:
                    parts.append("上次聊 " + humanize_ago(stamp))
                # 有交接文档是"现在能接着干"的信号，用强调色；只是时间就是中性灰。
                note = " · ".join(parts)
                note_color = ACCENT if parts[:1] == ["有交接文档"] else MUTED
            # actions 是从右往左摆的（下标 0 在最右边），所以这里的顺序要倒着念：
            # 屏幕上从左到右是 搬迁 开目录 ↑ ↓ 改名 移除。上移/下移用箭头不用词，
            # 是因为一行里塞六个两字词会糊成一片，而箭头没有认不出来的风险。
            actions = [("移除", lambda it=item: self.remove_workspace(it)),
                       ("改名", lambda it=item: self.rename_workspace(it)),
                       ("↓", lambda it=item: self.move_workspace(it, 1)),
                       ("↑", lambda it=item: self.move_workspace(it, -1)),
                       ("开目录", lambda p=path: self.open_folder(p)),
                       ("搬迁", lambda it=item: self.open_move_dialog(it))]
            # 「删交接文档」挂在列表最末 = 屏幕最左边。动作是从右边开始排的，加在
            # 末尾意味着上面那六个格子一个都不挪窝——只有真有文档的行，最左边才多
            # 冒出来这一颗。没有文档的行不摆它，免得按下去只得到一句"没有"。
            if has_handoff:
                actions.append(("删交接文档",
                                lambda it=item: self.remove_handoff(it)))
            Row(inner, title=item["name"], subtitle=path, height=52,
                warn=note, warn_color=note_color,
                # 行号就是 Ctrl+行号。9 以后没有号（按不到），但位置留着，
                # 免得后几行的标题跟前面错开一格。
                badge=str(position + 1) if position < 9 else "",
                on_click=lambda it=item: self.open_workspace(it),
                actions=actions,
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

    # ── 搬工作区 ──

    def move_workspace_to(self, item, target):
        """把整个工作区目录搬到新位置，会话记录那份跟着搬。

        顺序是死的，mover 那边定下了：检查 → 复制 → 对账 → 改配置 → 丢回收站。
        这里只负责在开工前问清两件只有界面知道的事、摆进度、把结果说给用户听；
        中间一步都不自己动手，全都交给 mover，免得两头各有一套判断标准。

        先问的两件事比 mover 里任何一条都要紧：文件夹正被会话写的时候复制，复制
        出来的是半截的东西，对账也可能通不过——而那两个 mover 从文件系统上看不
        出来。
        """
        if self._busy():
            self.feedback_var.set("启动器手上有活，等它跑完再搬。")
            return
        if self._handoff_proc is not None and self._handoff_proc.poll() is None:
            # 手动整理交接文档不算 _busy（那条路不占 _task_running），但它也占着
            # 同一块进度区，写完还会把它收回去——这会儿插一个搬迁进去，进度区会
            # 被它收走。写的正好是正要搬的这个目录时更得等：文件夹正被写。
            self.feedback_var.set("正有一份交接文档在写，等它写完再搬。")
            return
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        key = path_key(path)
        if any(path_key(s["path"]) == key for s in self.running):
            messagebox.showerror(
                "那个会话还开着",
                "「{}」这个工作区的会话正开着。\n\n"
                "先把它关掉再搬——文件夹正被写的时候复制，复制出来的是半截的"
                "东西，对账也会对不上。".format(item["name"]))
            return

        project_from, project_to = mover.project_paths(path, target)
        try:
            target = mover.check(path, target, project_from, project_to)
        except mover.MoveError as e:
            messagebox.showerror("搬不了", str(e))
            return

        self._move_running = True
        self._move_job = {"item": item, "source": path, "target": target,
                          "project_from": project_from, "project_to": project_to}
        self.task_head_var.set("正在搬工作区")
        self.task_count_var.set("")
        self.task_name_var.set(item["name"])
        self._show_task_area()
        self._task_started = time.time()
        self._task_step("正在复制整个目录……")
        threading.Thread(target=self._move_worker, args=(self._move_job,),
                         daemon=True).start()
        self.after(100, self._poll_move)

    def _move_worker(self, job):
        """复制 + 对账跑在后台线程里，进度和结果都走队列回主线程。

        两棵树共用同一个回调，靠 phase 那个格子区分现在在复制哪一份——会话记录
        通常小得多，不分开说清楚的话，用户会以为进度条退回去了。
        """
        queue_ = self._move_queue
        phase = ["正在复制整个目录"]

        def note(count):
            queue_.put(("count", count, phase[0]))

        try:
            mover.copy_tree(job["source"], job["target"], progress=note)
            phase[0] = "正在复制会话记录"
            mover.copy_project(job["project_from"], job["project_to"], progress=note)
        except mover.MoveError as e:
            queue_.put(("fail", str(e)))
            return
        except Exception as e:                       # noqa: BLE001
            queue_.put(("fail", "复制的时候出了岔子：{}".format(e)))
            return
        queue_.put(("copied", None))

    def _poll_move(self):
        """主线程收后台线程的进度：Tk 控件只能主线程碰。"""
        event = None
        try:
            while True:
                event = self._move_queue.get_nowait()
        except queue.Empty:
            pass
        if event is None:
            self.after(MOVE_POLL_MS, self._poll_move)
            return
        if event[0] == "count":
            self._task_step("{}……已经复制了 {} 个文件".format(event[2], event[1]))
            self.after(MOVE_POLL_MS, self._poll_move)
            return
        if event[0] == "fail":
            # 复制这一层没成。旧的没动、新的可能留了半截在目标位置上——mover
            # 那头的话里已经把这两句说了，原样摆出来。
            self._finish_move(None, event[1])
            return
        self._commit_move(self._move_job)

    def _commit_move(self, job):
        """复制对过账了，改配置——这一步得在主线程做，界面手里那份就是配置。

        配置写不成就不丢旧的：新的那份留在那儿（大不了用户自己删），旧的原封不
        动、配置也还指着旧的，至少系统是自洽的。反过来先丢旧的再存配置，中间一
        失败就是两边都没了。
        """
        item = job["item"]
        old_name, old_path = item["name"], item["path"]
        new_path = job["target"]
        # 名字跟着搬：没被用户改过（正等于旧文件夹名）的就换成新文件夹名，这样
        # 顺手改名那一下才有用；用户自己起的名字是人给的名字，照留。
        if old_name == os.path.basename(old_path.rstrip("/\\")):
            item["name"] = os.path.basename(new_path.rstrip("/\\"))
        item["path"] = new_path
        try:
            save_config(self.config_data)
        except OSError as e:
            item["name"], item["path"] = old_name, old_path
            self._finish_move(None, "配置写不成（{}），旧的一个没动，"
                                    "新位置那份你自己删一下。".format(e))
            return
        self._task_step("配置改好了，正在把旧的那份丢进回收站。")
        self.after(MOVE_POLL_MS, lambda: self._discard_move(job))

    def _discard_move(self, job):
        """新的对过账、配置也指着新的了，这才轮到旧的。

        丢不成【不回滚】——这时候新的那份已经在用了，把旧的搬回来才是真乱。
        丢不成的如实报出来让用户自己处理。
        """
        stuck = mover.discard(job["source"], job["project_from"])
        self._finish_move(job, None, stuck)

    def _finish_move(self, job, error, stuck=()):
        self._move_running = False
        self._move_job = None
        self._hide_task_area()
        if error is not None:
            self.feedback_var.set("没搬成：{}".format(error))
            return
        name = job["item"]["name"]
        if stuck:
            self.feedback_var.set(
                "「{}」搬到 {} 了，但旧的那份没能丢进回收站（{}），得你自己删。"
                .format(name, job["target"], "、".join(stuck)))
        else:
            self.feedback_var.set("「{}」搬到 {} 了，旧的进了回收站。"
                                  .format(name, job["target"]))
        self.refresh_workspaces()

    def launch_workspace(self, item, cont=False, prompt=None, autopilot=False,
                         hook_flags=None):
        """真正把 claude 拉起来，新窗口还是塞进本窗口看那个勾。

        autopilot=True 是「AI 托管」那条路：这次会话无条件挂上 hook，不看底下那个
        勾——用户是在托管对话框里当场选的档，那个选择就该管这一次。

        hook_flags 是托管那次要挂的具体开关（哪个档、指挥模型是谁），由
        start_autonomy 按档位算好传进来。**不能在这儿另算一份**：底下那个勾只代表
        "挂上自动继续"，托管选了第 2、3 档时它算不出该多挂什么，再写一次配置就把
        刚写好的那份覆盖回第 1 档了。
        """
        path = item["path"]
        permission = workspace_permission(item)
        settings = None
        auto_continue = self.auto_continue_var.get() or autopilot
        if auto_continue:
            try:
                settings = ensure_hook_settings(**(hook_flags
                                                   or {"auto_continue": True}))
            except OSError as e:
                messagebox.showerror("挂 hook 失败",
                                     "写不了 hook 配置，这次就不挂了：\n{}".format(e))

        if self.embed_var.get():
            self.embed_workspace(item, cont, prompt, settings, permission)
            return None
        # 拍快照得赶在启动之前：窗口是 claude 那边异步建出来的，等它冒出来再
        # 去数，就分不清哪扇是这次新开的、哪扇是上一轮留下的了。
        known = terminal_windows()
        # 摆到旁边这件事两个平台的时机不一样：Windows 是起完窗再按句柄挪
        # （bring_next_to，用户自己点），非 Windows 没这一手，只能趁起窗那一刻
        # 把坐标告诉终端（-geometry）。坐标这会儿就得算好，晚了窗口已经开在别处。
        beside = None
        if not WINDOW_CONTROL:
            x, y = window_position(self)
            beside = (x + self.winfo_width() + SIDE_GAP, y)
        try:
            process = spawn_terminal(path, cont, prompt, settings, permission,
                                     beside=beside)
        except Exception as e:
            messagebox.showerror("启动失败", "启动 claude 失败：\n{}".format(e))
            return
        entry = self.track_running(item["name"], path, process)
        self._watch_terminal(entry, known)
        if autopilot:
            self.feedback_var.set(
                "已托管「{}」（{}）：它停下问你话时会自己接上，权限 {}。关掉那扇"
                "窗口就结束。".format(item["name"],
                                  autonomy_caption(hook_flags),
                                  permission_option(permission)))
        else:
            self.feedback_var.set("已在新窗口启动{}（权限：{}）：{}".format(
                "（接着上次聊）" if cont else "", permission_option(permission), path))
        return entry

    def start_autonomy(self, item, task, tier=1, commander=""):
        """「AI 托管」按下去之后：在那个工作区起个新会话，把任务当开场白。

        新会话（不 --continue）是故意的：托管是丢一件新活进去，不是接着上一段
        聊天。不带 --continue 时 claude 把命令行上那个位置参数当第一条用户消息
        送进去，这条路线上文书探针验过。

        commander 只有第 2 档用得上，是那个替用户拍板的模型的预设名。
        """
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        flags = tier_flags(tier, commander)
        # 先把 hook 配置文件写出来试一次：写不了就别开——开出去一个没挂上 hook
        # 的会话，用户以为托管着呢，其实它停下来就在那儿干等。
        # 也算好这次的开关一起交给 launch_workspace，别让它自己再算一遍。
        try:
            ensure_hook_settings(**flags)
        except OSError as e:
            messagebox.showerror("挂 hook 失败",
                                 "写不了 hook 配置，这次没法托管：\n{}".format(e))
            return

        self.config_data["autonomy"] = tier
        self.config_data["autonomy_workspace"] = path
        self.config_data["autonomy_commander"] = flags["commander"]
        save_config(self.config_data)
        self.launch_workspace(item, cont=False, prompt=task, autopilot=True,
                              hook_flags=flags)

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

    def _on_auto_continue_toggle(self):
        self.config_data["auto_continue"] = self.auto_continue_var.get()
        save_config(self.config_data)
        if self.auto_continue_var.get():
            self.feedback_var.set(
                "已挂上 Stop hook：它停下问话时替它接一句「接着干，自己定」，连着推 "
                "{} 轮就放行；隔 {} 分钟重新数，它自己说「已完成」也会停。".format(
                    AUTO_CONTINUE_MAX, AUTO_CONTINUE_RESET // 60))
        else:
            self.feedback_var.set("新开的会话不再自动继续。")

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
        self.track_running(item["name"], item["path"], process)
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
        left, _top, right, _bottom = screen_bounds(self)
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

    def _on_window_move(self, event):
        """本窗口自己挪了：把内嵌的那个重新贴到终端栏上。

        子控件的事件也会流到这儿（顶层窗口在 bindtags 里占一环），所以拿
        event.widget 挡一下——只有它就是本窗口时才是真挪了窗口，不然列表里
        随便哪个控件动一下都要白贴一遍。
        """
        if event.widget is not self or self.embedded is None or self._placing:
            return
        self._placing = True
        try:
            self.embedded.place()
        finally:
            self._placing = False

    def _on_term_resize(self, _event):
        """终端栏自己变了尺寸（缩放窗口、或者右边这栏刚长出来）。"""
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
        open_path(path)

    def _open_tool_dir(self):
        """顶栏那个按钮：把 ~/.claude_tool 用文件管理器打开。

        配置、模型预设、交接文档的记账本都在这儿，手改的时候比在界面里点来点去快。
        目录不存在就先建出来——首次启动本来就该有，但用户手删了也别报个错就完事。
        """
        try:
            os.makedirs(TOOL_DIR, exist_ok=True)
            open_path(TOOL_DIR)
        except OSError as exc:
            messagebox.showerror("打不开", "打开 {} 失败：\n{}".format(TOOL_DIR, exc))

    def write_handoff(self, item, ignore_git=None):
        """在那个目录里 fork 一份会话，让它写 handoff.md。

        只有手动那一条路（工作区行上那颗「整理交接文档」）。

        ignore_git 传 None 是"还没定"：这个目录是 git 仓库时，先弹个框问一句
        要不要让 handoff.md 进版本管理（见 ask_handoff_git），答完由那个框回头
        再调一次、带着选好的值过来。
        """
        path = item["path"]
        if self._busy():
            self.feedback_var.set("启动器手上有活，等它跑完再单独整理。")
            return
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        if self._handoff_proc is not None and self._handoff_proc.poll() is None:
            self.feedback_var.set("上一份交接文档还在写，等它写完。")
            return
        # 弹框这一问排在上面几道检查之后：这次要是本来就写不了，先告诉他写不了，
        # 别让人选完一遍才发现白选。
        if ignore_git is None:
            if is_git_repo(path):
                self.ask_handoff_git(item)
                return
            ignore_git = False

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
                [claude_exe(), "-c", "--fork-session", "-p",
                 handoff_prompt(ignore_git),
                 "--allowedTools", HANDOFF_TOOLS],
                cwd=path, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            log.close()
            os.remove(log.name)
            messagebox.showerror("启动失败", "叫不起 claude：\n{}".format(e))
            return
        log.close()

        self._handoff_ctx = (item, target, before)
        self._handoff_log = log.name
        self.task_head_var.set("正在整理交接文档")
        self.task_name_var.set(item["name"])
        self._show_task_area()
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
        item, target, before = self._handoff_ctx
        self._handoff_ctx = None
        detail = self._read_handoff_log()

        if os.path.exists(target) and os.path.getmtime(target) > before:
            ok, why = True, "写好了：{}".format(target)
        elif proc.returncode != 0:
            ok, why = False, detail or "退出码 {}".format(proc.returncode)
        else:
            ok = False
            why = "跑完了但没生成 {}——这个目录可能还没聊过，没有会话可以交接。".format(
                HANDOFF_FILE)

        self.feedback_var.set("「{}」的交接文档{}".format(
            item["name"], why if ok else "没写成：" + why))
        self._hide_task_area()

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

    def remove_handoff(self, item):
        """把工作区里那份 handoff.md 丢进回收站。

        交接文档是别人（claude）在用户目录里留下的一份文件，删它得让用户知道
        自己删的是什么、以及还能不能捞回来——所以先问一句，再走回收站而不是
        直接 unlink。删完刷新列表，"有交接文档"那行小字跟着消失。
        """
        target = os.path.join(item["path"], HANDOFF_FILE)
        if not os.path.isfile(target):
            self.feedback_var.set("「{}」里已经没有交接文档了。".format(item["name"]))
            self.refresh_workspaces()
            return
        if not messagebox.askyesno(
                "删除交接文档",
                "把「{}」里的 {} 丢进回收站？\n\n"
                "删掉之后，下次开这个工作区时「先读交接文档」那一步就没得读了。\n"
                "只是丢进回收站，还能找回来。".format(item["name"], HANDOFF_FILE)):
            return
        if trash_path(target):
            self.feedback_var.set(
                "「{}」的交接文档已丢进回收站。".format(item["name"]))
        else:
            messagebox.showerror(
                "删不掉",
                "没能把 {} 丢进回收站。\n可以手动去这个目录里删：\n{}".format(
                    HANDOFF_FILE, target))
        self.refresh_workspaces()

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

    def pick_workplace(self, parent):
        """弹目录选择框换默认工作区，选中就当场落盘。返回换没换。

        「换默认目录」本身不是创建动作，不该挂在哪次"建文件夹"上——早先只有
        「新建文件夹」对话框里能改，而且得点「创建」才生效，选完点「取消」就
        白选一场。所以提出来单独一件事，主界面那行和对话框那行都走这儿。

        parent 传对话框的话，选择框会挂在那个对话框上，不会被它的 grab 挡住。
        """
        chosen = filedialog.askdirectory(
            parent=parent, title="选择默认工作区目录",
            initialdir=self.config_data["workplace"] or TOOL_DIR)
        if not chosen:
            return False
        self._set_workplace(os.path.normpath(chosen))
        save_config(self.config_data)
        self.refresh_workplace_label()
        self.feedback_var.set(
            "默认工作区已改成 {}。这个目录下的子文件夹，点「重新扫描」能拉进列表。"
            .format(self.config_data["workplace"]))
        return True

    def refresh_workplace_label(self):
        self.workplace_var.set(self.config_data["workplace"])


    def rescan(self):
        added = merge_scanned(self.config_data)
        save_config(self.config_data)
        self.refresh_workspaces()
        if added:
            self.feedback_var.set("新发现 {} 个工作区：{}".format(len(added), "、".join(added)))
        else:
            self.feedback_var.set("扫描目录下没有新工作区。")
