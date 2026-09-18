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
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from claude_tool.paths import (
    CONFIG_FILE,
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
    HANDOFF_COOLDOWN,
    HANDOFF_FILE,
    HANDOFF_MIN_TURNS,
    HANDOFF_PROMPT,
    HANDOFF_TOOLS,
    ensure_handoff_hook_settings,
)
from claude_tool.winhost import (
    EmbeddedConsole,
    fresh_console,
    place_window,
    spawn_console,
    window_position,
)
from claude_tool.widgets import (
    PillButton,
    Row,
    ScrollArea,
    make_entry,
)

from claude_tool.ui.dialogs import LauncherDialogs


MODEL_MAX, WORKSPACE_MAX = 200, 320
# 内嵌终端时：左列固定这么宽，终端从右边长出来，窗口不够宽就往右撑
SIDE_WIDTH = 360
TERMINAL_MIN_WIDTH = 900


class Launcher(LauncherDialogs, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude 启动器")
        self.geometry("640x760")
        self.minsize(560, 520)
        self.configure(bg=PAGE_BG)

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
        # 这个启动器开出去、还开着的 claude：[{name, path, proc}]
        self.running = []
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

    def _restore_geometry(self):
        saved = self.config_data["window"]
        if not saved:
            return
        self.geometry("{}x{}".format(saved["w"], saved["h"]))
        self.update_idletasks()
        place_window(self, saved["x"], saved["y"])

    def _on_close(self):
        # 必须先放出去：父窗口一销毁，挂在它下面的子窗口会跟着被销毁，
        # 那等于把用户正在跑的 claude 一起干掉。
        self.detach_terminal()
        try:
            x, y = window_position(self)
            self.config_data["window"] = {
                "x": x, "y": y,
                "w": self.winfo_width(), "h": self.winfo_height()}
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
        PillButton(line, "打开配置目录", self._open_tool_dir,
                   bg=PANEL_BG).pack(side="right")

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
        switches = tk.Frame(footer, bg=PAGE_BG)
        switches.pack(fill="x", padx=16, pady=(8, 0))
        self.embed_var = tk.BooleanVar(value=False)
        # 会花用户 token 的功能，默认关；这个勾只影响新开的会话，不动已经跑着的
        self.auto_handoff_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_handoff")))
        # 括号里写的是各自的真名：内嵌那条走的是 conhost（不是默认的 Windows
        # Terminal，字形回退差些），自动刷文档靠的是 Claude Code 的 Stop hook。
        # 熟练用户要的是这两个词，好去翻文档、翻配置文件；只写大白话他就得猜。
        for var, text, command in (
                (self.embed_var, "内嵌终端 conhost（不勾就在新窗口里开）",
                 self._on_embed_toggle),
                (self.auto_handoff_var, "自动刷交接文档 Stop hook（会多用 token）",
                 self._on_auto_handoff_toggle)):
            tk.Checkbutton(switches, text=text, variable=var, command=command,
                           bg=PAGE_BG, fg=TEXT, font=font(9), activebackground=PAGE_BG,
                           selectcolor=PANEL_BG, highlightthickness=0, bd=0,
                           ).pack(side="left", padx=(0, 18))
        self.feedback_var = tk.StringVar(
            value="点工作区选「新会话」或「接着上次聊」；Ctrl+1~9 一样，Ctrl+F 跳到筛选框。")
        tk.Label(footer, textvariable=self.feedback_var, bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").pack(fill="x", padx=20, pady=(2, 8))

        # footer 先 pack 是为了让它先把自己的高度要走——窗口被拉矮时该挤的是
        # 中间那块列表，不是这行提示。
        body = tk.Frame(self, bg=PAGE_BG)
        body.pack(fill="both", expand=True, padx=14)

        # side 是常年都在的左列；panel 是内嵌终端，平时不摆出来。
        # 两者都 side="left"，终端一出现就从右侧长出来，窗口跟着变宽。
        self.side = tk.Frame(body, bg=PAGE_BG)
        self.side.pack(side="left", fill="both", expand=True)

        # 先建好但不 pack——没会话在跑的时候，界面上不该看出有这么一块。
        # 一有活的会话，_render_running() 就把它插到模型区上面。
        self._build_running(self.side)

        self.model_list = self._build_section(
            self.side, "模型", max_height=MODEL_MAX,
            actions=[("＋ 添加", self._open_model_dialog),
                     ("测试", self.test_all_models),
                     ("刷新", self.refresh_models)])

        self.ws_list = self._build_section(
            self.side, "工作区", max_height=WORKSPACE_MAX, expand=True,
            actions=[("＋ 新建文件夹", self._open_quick_workspace_dialog),
                     ("＋ 选已有目录", self._open_add_workspace_dialog),
                     ("重新扫描", self.rescan)],
            search=self.ws_filter, note=self._build_workplace_note)

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

        self.term_holder = tk.Frame(parent, bg="#1c1c1c", height=240)
        self.term_holder.pack_propagate(False)
        self.term_holder.bind("<Configure>", self._on_term_resize)

    def _build_section(self, parent, title, max_height, actions, expand=False,
                       search=None, note=None):
        head = tk.Frame(parent, bg=PAGE_BG)
        head.pack(fill="x", pady=(16, 6))
        tk.Label(head, text=title, bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        for text, command in reversed(actions):
            PillButton(head, text, command, bg=PAGE_BG).pack(side="right", padx=(6, 0))
        # 搜索框得跟在标题后面、列表前面，不然 pack 顺序会把列表挤到它上面去
        if search is not None:
            box = tk.Frame(parent, bg=PAGE_BG)
            box.pack(fill="x", pady=(0, 6))
            tk.Label(box, text="筛选", bg=PAGE_BG, fg=MUTED,
                     font=font(9)).pack(side="left", padx=(2, 6))
            self.filter_entry = make_entry(box, search, width=10)
            self.filter_entry.pack(side="left", fill="x", expand=True)
        if note is not None:
            note(parent)
        area = ScrollArea(parent, max_height=max_height)
        # 记下标题栏，"正在跑"那块要靠它把自己插到模型区上面
        area.head = head
        if expand:
            area.pack(fill="both", expand=True)
        else:
            area.pack(fill="x")
        return area

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
            # 现在就让它写，读的是硬盘上已经存下来的那份会话记录——所以哪怕
            # 里面那份 claude 还开着也照写不误，只是会 fork 出一份副本。
            PillButton(line, "整理交接文档",
                       lambda it=item: self.write_handoff(it),
                       bg=PANEL_BG).pack(side="right", padx=(8, 10), pady=8)
        self.running_frame.pack(fill="x", before=self.model_list.head)
        self.model_list.fit()
        self.ws_list.fit()

    def _poll_running(self):
        """每秒一趟：看一眼那几扇窗口还开着没，顺手把后台问到的版本号贴上。

        两件事都走这条心跳是因为它是现成的、启动时就起来的定时器；再造一个
        只为取个字符串不值当。
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
        self.after(1000, self._poll_running)

    def track_running(self, name, path, process):
        """把一个刚开出去的会话记进名单，界面立刻多出一行。"""
        for item in self.running:
            if item["path"] == path:
                # 同一个目录又开了一个，只留最新那个，免得同一行重复
                item.update({"name": name, "proc": process})
                self._render_running()
                return
        self.running.append({"name": name, "path": path, "proc": process})
        self._render_running()

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
            tk.Label(inner, text="还没有模型预设，点右上角「＋ 添加」建一个。",
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
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
            tk.Label(inner, text="还没有工作区。右上角「＋ 新建文件夹」会在下面那个目录里"
                                 "建一个新的；已经有目录了就用「＋ 选已有目录」。",
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
            return
        if not view:
            tk.Label(inner, text="没有匹配「{}」的工作区。".format(self.ws_filter.get().strip()),
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
            self.ws_list.fit()
            return

        for item in view:
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


    def launch_workspace(self, item, cont=False, prompt=None):
        """真正把 claude 拉起来，新窗口还是塞进本窗口看那个勾。"""
        path = item["path"]
        permission = workspace_permission(item)
        settings = None
        if self.auto_handoff_var.get():
            try:
                settings = ensure_handoff_hook_settings()
            except OSError as e:
                messagebox.showerror("挂 hook 失败",
                                     "写不了 hook 配置，这次就不自动刷交接文档了：\n{}"
                                     .format(e))

        if self.embed_var.get():
            self.embed_workspace(item, cont, prompt, settings, permission)
            return
        try:
            process = launch(path, cont, prompt, settings, permission)
        except Exception as e:
            messagebox.showerror("启动失败", "启动 claude 失败：\n{}".format(e))
            return
        self.track_running(item["name"], path, process)
        self.feedback_var.set("已在新窗口启动{}（权限：{}）：{}".format(
            "（接着上次聊）" if cont else "", permission_option(permission), path))

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
            self.feedback_var.set("已摘掉 Stop hook，新开的会话不再自动刷交接文档。")

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
        self._pending = (process, known, item, 0)
        self.feedback_var.set("正在把 claude 装进窗口…")
        self.after(80, self._poll_console)

    def _poll_console(self):
        """控制台窗口是异步建的，拿到之前一直轮询，别把界面卡住。"""
        process, known, item, tries = self._pending
        hwnd = fresh_console(known)
        if hwnd is None:
            if tries >= 75:
                self._pending = None
                self.feedback_var.set("等不到控制台窗口，claude 可能已经退出了。")
                return
            self._pending = (process, known, item, tries + 1)
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
        """内嵌时左列收窄、终端从右侧长出来，窗口不够宽就往右撑；退出去还原。"""
        if want:
            self._width_before_embed = self.winfo_width()
            self.panel.pack(side="left", fill="both", expand=True, padx=(14, 0))
            self.side.configure(width=SIDE_WIDTH)
            self.side.pack_propagate(False)
            self.side.pack_configure(fill="y", expand=False)
            self._widen(SIDE_WIDTH + TERMINAL_MIN_WIDTH)
        else:
            self.panel.pack_forget()
            self.side.pack_propagate(True)
            self.side.pack_configure(fill="both", expand=True)
            if self._width_before_embed:
                self._widen(self._width_before_embed)
            self._width_before_embed = None
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

    def write_handoff(self, item):
        """点"正在跑"那块里的按钮：在那个目录里 fork 一份会话，让它写 handoff.md。"""
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        if self._handoff_proc is not None and self._handoff_proc.poll() is None:
            self.feedback_var.set("上一份交接文档还在写，等它写完。")
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
            return
        log.close()

        self._handoff_ctx = (item, target, before)
        self._handoff_log = log.name
        self.feedback_var.set(
            "正在给「{}」整理交接文档，可能要几十秒，别关这个窗口。".format(item["name"]))
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
            self.feedback_var.set(
                "「{}」的交接文档写好了：{}".format(item["name"], target))
            return
        if proc.returncode != 0:
            self.feedback_var.set("写交接文档失败：{}".format(
                detail or "退出码 {}".format(proc.returncode)))
            return
        self.feedback_var.set(
            "跑完了但没生成 {}——这个目录可能还没聊过，没有会话可以交接。".format(HANDOFF_FILE))

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
        self._refresh_workplace_note()

    def _build_workplace_note(self, parent):
        """工作区列表顶上那行：新建的文件夹建在哪儿，以及改它的按钮。"""
        line = tk.Frame(parent, bg=PAGE_BG)
        line.pack(fill="x", pady=(0, 6))
        tk.Label(line, text="新建文件夹建在", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(side="left", padx=(2, 6))
        # 按钮先 pack 把它那份宽度占住，否则长路径会把按钮挤出窗口。
        PillButton(line, "更改目录", self._open_workplace_dialog,
                   bg=PAGE_BG).pack(side="right")
        self.workplace_label = tk.Label(line, bg=PAGE_BG, fg=TEXT, font=font(9),
                                        anchor="w")
        self.workplace_label.pack(side="left", fill="x", expand=True)
        # 宽度是布局给的，只有 Configure 之后才知道，所以每次都按当前宽度重算
        self.workplace_label.bind("<Configure>", lambda _e: self._refresh_workplace_note())
        self._refresh_workplace_note()

    def _refresh_workplace_note(self):
        room = max(self.workplace_label.winfo_width() - 4, 60)
        text = ellipsize(self.config_data["workplace"], room, 9)
        if self.workplace_label.cget("text") != text:
            self.workplace_label.configure(text=text)


    def rescan(self):
        added = merge_scanned(self.config_data)
        save_config(self.config_data)
        self.refresh_workspaces()
        if added:
            self.feedback_var.set("新发现 {} 个工作区：{}".format(len(added), "、".join(added)))
        else:
            self.feedback_var.set("扫描目录下没有新工作区。")
