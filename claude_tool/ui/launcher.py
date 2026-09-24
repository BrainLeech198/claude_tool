"""主窗口。

左列模型、右列工作区，底下一排开关，中间一条"正在跑"——这是骨架：窗口怎么起、
底部那排开关怎么摆、两栏的装配顺序，以及不属于任何一域的那几个小方法。

各个域按 mixin 拆在 ui/ 底下（见 设计说明-0.4.md 第三节）：
  dialogs.py     加模型、加工作区、点工作区那次询问
  update.py      顶栏版本检查 + 启动器自更新
  models.py      模型列表、测试、换模型那条流水线
  workspaces.py  工作区列表、搬迁改名、默认目录
  sessions.py    开出去的会话、「正在跑」、交接文档
  terminal.py    内嵌终端（只在 Windows 上有）
它们跟主窗口共享 config_data / feedback_var 那些状态，全部留在本文件的 __init__ 里。
"""
import os
import queue
import tkinter as tk
from tkinter import messagebox, ttk

from claude_tool.paths import (
    CONFIG_FILE,
    ICON_FILE,
    PRESET_DIR,
    TOOL_DIR,
)
from claude_tool import install
from claude_tool.theme import (
    ACCENT,
    ACCENT_SOFT,
    ALERT_BG,
    BORDER,
    HOVER_BG,
    MUTED,
    PAGE_BG,
    PANEL_BG,
    SIDE_GAP,
    TEXT,
    WARN,
    font,
)
from claude_tool.presets import migrate_presets
from claude_tool.config import (
    default_config,
    load_config,
    merge_scanned,
    save_config,
)
from claude_tool.claude import find_claude
from claude_tool.host import (
    EMBED_SUPPORTED,
    open_path,
    open_url,
    place_window,
    window_position,
)
from claude_tool.widgets import (
    PillButton,
    ScrollArea,
    Tip,
    make_entry,
)

from claude_tool.ui.dialogs import LauncherDialogs
from claude_tool.ui.models import ModelsMixin
from claude_tool.ui.sessions import SessionsMixin
from claude_tool.ui.terminal import TerminalMixin
from claude_tool.ui.update import UpdateMixin
from claude_tool.ui.workspaces import WorkspacesMixin


MODEL_MAX, WORKSPACE_MAX = 300, 480
# 内嵌终端那一栏的宽度。它从右边长出来，窗口跟着变宽，左列不动。
TERMINAL_MIN_WIDTH = 900
# 正文上下各留的空档。左列和终端栏之间那个 SIDE_GAP 在 theme.py——内嵌终端和
# 会话启动那两处也要用它，搁这儿它们 import 不到。
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


class Launcher(UpdateMixin, ModelsMixin, WorkspacesMixin,
               SessionsMixin, TerminalMixin, LauncherDialogs, tk.Tk):
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
        # 启动器自己有没有新版：0.4 起每次都查，不再由用户开关。查的是我们自己
        # 的官网，不是 npm，量很小。这条不依赖本机 claude 的版本号（问的是我们
        # 自己），所以不跟 claude 那条一起挂在 version_queue 上，直接起。
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

        底下那排开关（内嵌终端、自动继续 Stop hook、自动查 claude 新版）是最宽的
        一块，几段文字加上间距比左列那几块都宽；原先写死的下限 560（默认宽 640）
        都装不下，末尾那个勾的标题会被切掉一截，字号再大点的机器切得更多。

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
        # 启动器自己的新版 0.4 起不在这儿摆勾了：启动即查，见 __init__ 里那句
        # _start_self_check()。配置里的 auto_self_update 键留着读旧配置用，见
        # config.default_config 的注释。
        # 括号里写的是各自的真名：内嵌那条走的是 conhost（不是默认的 Windows
        # Terminal，字形回退差些），另一条挂的是 Claude Code 的 Stop hook。
        # 熟练用户要的是这几个词，好去翻文档、翻配置文件；只写大白话他就得猜。
        #
        # 拆几行摆，不挤一行：窗口下限是按这排开关的自然宽度量的（见
        # _apply_min_size），挤成一行会把下限顶宽一大截。行按功能分——上排是终端
        # 怎么开，中排是挂 Stop hook 那个行为，最后一行是查 claude 新版。
        #
        # 每行都从第 0 列起头，几个勾的左边缘对齐。这里本来还有第 1 列（"自动查
        # 启动器新版"跟上面那条"联网"并排），0.4 把那个勾去掉了，现在整排都是
        # 第 0 列。
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

