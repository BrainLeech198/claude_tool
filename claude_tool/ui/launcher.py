"""主窗口。

左右两栏：左边模型，右边工作区；底下一排开关，中间一条"正在跑"。
对话框（加模型、加工作区、点工作区那次询问）现在也还长在这个类里。
"""
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from claude_tool.paths import (
    CONFIG_FILE,
    ILLEGAL_CHARS,
    PRESET_DIR,
    SETTINGS_FILE,
    TOOL_DIR,
    WORKPLACE_DIR,
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
    PERMISSION_VALUES,
    permission_hint,
    permission_option,
    workspace_permission,
)
from claude_tool.presets import (
    PROVIDERS,
    active_preset,
    describe_preset,
    discover_presets,
    host_of,
    migrate_presets,
    preset_path,
    read_env,
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
    READ_HANDOFF_PROMPT,
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
    finish_form,
    make_entry,
    make_form,
)


MODEL_MAX, WORKSPACE_MAX = 200, 320
# 内嵌终端时：左列固定这么宽，终端从右边长出来，窗口不够宽就往右撑
SIDE_WIDTH = 360
TERMINAL_MIN_WIDTH = 900


class Launcher(tk.Tk):
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

    def _open_model_dialog(self, edit=None):
        """edit 传 (名称, 路径) 是编辑，传 None 是新增，两者共用一套表单。"""
        old_name, old_path = edit or (None, None)
        is_edit = old_path is not None
        env = read_env(old_path) if is_edit else {}

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "编辑模型" if is_edit else "添加模型")

        fields = [("预设名称", "name", False), ("Base URL", "base_url", False),
                  ("API Token", "token", True), ("模型名", "model", False)]
        values = {"name": old_name or "", "base_url": env.get("ANTHROPIC_BASE_URL", ""),
                  "token": env.get("ANTHROPIC_AUTH_TOKEN", ""),
                  "model": env.get("ANTHROPIC_MODEL", "")}

        row = 0
        hint_var = tk.StringVar(
            value="从下拉里挑一个供应商，地址和模型名会自动填好，你只要贴自己的 key。")

        # 常用供应商：选一个就把 Base URL 和模型名填好，用户只剩贴 key 这件事。
        picked = tk.Frame(body, bg=PAGE_BG)
        picked.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(picked, text="常用供应商", bg=PAGE_BG, fg=MUTED, font=font(9),
                 ).pack(side="left", padx=(0, 6))
        entries = {}
        provider_var = tk.StringVar()
        combo = ttk.Combobox(picked, textvariable=provider_var, state="readonly",
                             width=30, font=font(10),
                             values=["（自己填）"] + [p[0] for p in PROVIDERS])
        combo.current(0)
        combo.pack(side="left")

        # 记住名字框里那个名字是不是下拉给填的：是的话换一家就跟着换，
        # 用户自己敲过的就不动。免得挑完 DeepSeek 改挑 OpenRouter，名字还留着 DeepSeek。
        auto_name = [""]

        def pick(_event=None):
            """选中哪个就把那家的地址/模型名灌进对应的框。"""
            for short, label, base, model in PROVIDERS:
                if short != provider_var.get():
                    continue
                entries["base_url"][0].set(base)
                entries["model"][0].set(model)
                current = entries["name"][0].get().strip()
                if not current or current == auto_name[0]:
                    entries["name"][0].set(label)
                    auto_name[0] = label
                entries["token"][1].focus_set()
                hint_var.set(
                    "已填好 {} 的地址（{}）和模型名 {}。贴上你的 key；"
                    "模型名各家会变，不对就改一下再点「测试」验。"
                    .format(short, host_of(base), model))
                return

        combo.bind("<<ComboboxSelected>>", pick)
        if is_edit:
            # 编辑已有预设时，地址对得上哪家就把下拉停在哪家
            for index, provider in enumerate(PROVIDERS, start=1):
                if values["base_url"].rstrip("/") == provider[2]:
                    combo.current(index)
                    break
        row += 1

        for label, key, secret in fields:
            tk.Label(body, text=label, bg=PAGE_BG, fg=MUTED, font=font(10),
                     anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=values[key])
            entry = make_entry(body, var, width=40, secret=secret)
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)
            entries[key] = (var, entry)
            if key == "token":
                shown = tk.BooleanVar(value=False)

                def toggle(v=shown, e=entry):
                    e.configure(show="" if v.get() else "*")

                tk.Checkbutton(body, text="显示", variable=shown, command=toggle,
                               bg=PAGE_BG, fg=MUTED, font=font(9),
                               activebackground=PAGE_BG, selectcolor=PANEL_BG,
                               highlightthickness=0, bd=0,
                               ).grid(row=row, column=2, sticky="w", padx=(8, 0))
            row += 1
        body.columnconfigure(1, weight=1)

        tk.Label(body, textvariable=hint_var, bg=PAGE_BG, fg=MUTED, font=font(9),
                 justify="left", anchor="w", wraplength=470,
                 ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        row += 1

        # 改一个当前没在用的预设时，默认不去动正在生效的 settings.json
        apply_now = tk.BooleanVar(value=(not is_edit) or old_name == self.active_name)
        tk.Checkbutton(body, text="保存后立即启用" if is_edit else "添加后立即启用",
                       variable=apply_now, bg=PAGE_BG, fg=TEXT, font=font(10),
                       activebackground=PAGE_BG, selectcolor=PANEL_BG,
                       highlightthickness=0, bd=0,
                       ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row += 1

        note = "改名会一并重命名 claude_settings\\<名称>.json；" if is_edit else \
               "设置会存成 .claude_tool\\claude_settings\\<名称>.json；"
        tk.Label(body, text=note + "env 之外的公共设置沿用原文件。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                 ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))

        save = lambda: self._save_model(dialog, entries, apply_now, edit)
        finish_form(dialog, [("保存", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        entries["name"][1].focus_set()
        self._center(dialog)

    def _save_model(self, dialog, entries, apply_now, edit):
        _old_name, old_path = edit or (None, None)
        name = entries["name"][0].get().strip()
        base_url = entries["base_url"][0].get().strip()
        token = entries["token"][0].get().strip()
        model = entries["model"][0].get().strip()

        if not all([name, base_url, token, model]):
            messagebox.showerror("缺少信息", "四个字段都要填。", parent=dialog)
            return
        if re.search(ILLEGAL_CHARS, name):
            messagebox.showerror("名称非法",
                                 "名称不能包含 < > : \" / \\ | ? * 和空格。", parent=dialog)
            return

        target = preset_path(name)
        renamed = old_path is not None and os.path.normcase(target) != os.path.normcase(old_path)
        if (old_path is None or renamed) and os.path.exists(target):
            messagebox.showerror("名称冲突", "预设「{}」已存在。".format(name), parent=dialog)
            return

        preset = self._preset_template()
        if old_path is not None:
            try:
                with open(old_path, "r", encoding="utf-8") as f:
                    preset = json.load(f)
            except Exception:
                pass
        preset["env"] = {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_MODEL": model,
        }

        try:
            os.makedirs(PRESET_DIR, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(preset, f, ensure_ascii=False, indent=2)
            if renamed:
                os.remove(old_path)
        except Exception as e:
            messagebox.showerror("保存失败", "写入 {} 失败：\n{}".format(target, e), parent=dialog)
            return

        dialog.destroy()
        if apply_now.get():
            self.switch_model(name, target)
        else:
            self.feedback_var.set(
                "{}模型：{}".format("已更新" if old_path else "已新增", name))
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

    def _preset_template(self):
        """从现有预设继承 env 之外的公共设置，作为新预设的模板。"""
        presets = discover_presets()
        candidates = []
        if self.active_name and self.active_name in presets:
            candidates.append(presets[self.active_name])
        candidates.extend(presets.values())
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {k: v for k, v in data.items() if k != "env"}
            except Exception:
                continue
        return {
            "enabledPlugins": {},
            "language": "简体中文",
            "skipDangerousModePermissionPrompt": True,
        }

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

    def open_workspace(self, item):
        """点工作区先问一句：新开一个还是接着上次聊，要不要先读交接文档。"""
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return

        handoff = os.path.join(path, HANDOFF_FILE)
        has_handoff = os.path.isfile(handoff)

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, item["name"])

        tk.Label(body, text=path, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                 justify="left", wraplength=430).grid(row=0, column=0, columnspan=2,
                                                      sticky="w")

        read_var = tk.BooleanVar(value=has_handoff)
        if has_handoff:
            tk.Checkbutton(body, text="先读交接文档 " + HANDOFF_FILE + "，接着上次的进度干",
                           variable=read_var, bg=PAGE_BG, fg=TEXT, font=font(10),
                           activebackground=PAGE_BG, selectcolor=PANEL_BG,
                           highlightthickness=0, bd=0, anchor="w",
                           ).grid(row=1, column=0, columnspan=2, sticky="w",
                                  pady=(12, 0))
        row_after = 2 if has_handoff else 1

        # 权限等级：预选这个工作区上次用的那档，选了就记回工作区条目里。
        # 按下的永远是下拉里那一行的下标，真正的值（claude 要的那串英文）从
        # PERMISSION_VALUES 现取——界面上摆的字和传给 claude 的字是两回事。
        perm_row = tk.Frame(body, bg=PAGE_BG)
        perm_row.grid(row=row_after, column=0, columnspan=2, sticky="ew",
                      pady=(12, 0))
        tk.Label(perm_row, text="权限", bg=PAGE_BG, fg=MUTED,
                 font=font(10)).pack(side="left")
        perm_var = tk.StringVar(value=permission_option(workspace_permission(item)))
        perm_combo = ttk.Combobox(
            perm_row, textvariable=perm_var, state="readonly", width=30,
            font=font(10), values=[permission_option(mode)
                                  for mode in PERMISSION_VALUES])
        perm_combo.pack(side="left", padx=(10, 0))
        # 命令行参数和解释分两行：熟练用户认的是 --permission-mode 后面那串英文，
        # 中文那句他只当注释看，两行各给各的。合在一行会超宽折行，换档时对话框
        # 还会跟着跳高度。
        perm_flag = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w")
        perm_flag.grid(row=row_after + 1, column=0, columnspan=2, sticky="w",
                       pady=(4, 0))
        # wraplength 给到 470：最长那句（auto）量出来 435px，卡在 430 上就会把
        # 句末那个句号甩到第二行去。
        perm_hint = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9),
                             justify="left", anchor="w", wraplength=470)
        perm_hint.grid(row=row_after + 2, column=0, columnspan=2, sticky="w",
                       pady=(2, 0))

        def show_permission(value):
            perm_flag.configure(text="命令行参数 --permission-mode " + value)
            perm_hint.configure(text=permission_hint(value))

        def pick_permission(_event=None):
            show_permission(PERMISSION_VALUES[perm_combo.current()])

        show_permission(workspace_permission(item))
        perm_combo.bind("<<ComboboxSelected>>", pick_permission)
        row_after += 3

        tk.Label(body, text="没读过的目录就选「开新会话」。想回到上次那段对话就选"
                            "「接着上次聊」——它接的是这个话题里最近的一次会话。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                 wraplength=430,
                 ).grid(row=row_after, column=0, columnspan=2, sticky="w",
                        pady=(12, 0))

        def go(cont):
            prompt = READ_HANDOFF_PROMPT if read_var.get() else None
            # 记下这次挑的权限等级，下次点这个工作区预选它。变了才落盘。
            chosen = PERMISSION_VALUES[perm_combo.current()]
            if chosen != item.get("permission"):
                item["permission"] = chosen
                if item in self.config_data["workspaces"]:
                    save_config(self.config_data)
            dialog.destroy()
            self.launch_workspace(item, cont=cont, prompt=prompt)

        finish_form(dialog, [("接着上次聊", lambda: go(True), True),
                             ("开新会话", lambda: go(False), False)])
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dialog.bind("<Return>", lambda e: go(True))
        self._center(dialog)

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

    def _open_workplace_dialog(self):
        """改默认工作区目录。选完就落盘，以后「＋ 新建文件夹」都建在这儿。"""
        current = self.config_data["workplace"]
        chosen = filedialog.askdirectory(
            parent=self, title="选择默认工作区目录",
            initialdir=current if os.path.isdir(current) else TOOL_DIR)
        if not chosen:
            return
        base = os.path.normpath(chosen)
        if path_key(base) == path_key(current):
            return
        self._set_workplace(base)
        save_config(self.config_data)
        self.feedback_var.set(
            "默认工作区目录改成 {}，以后「＋ 新建文件夹」就建在这儿。".format(base))

    def _open_quick_workspace_dialog(self):
        """「＋ 新建文件夹」：在默认工作区目录下面建一个新文件夹，当工作区用。"""
        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "新建工作区文件夹")

        tk.Label(body, text="文件夹名称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        folder_var = tk.StringVar()
        folder_entry = make_entry(body, folder_var, width=32)
        folder_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        tk.Label(body, text="简称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        name_var = tk.StringVar()
        make_entry(body, name_var, width=32).grid(row=1, column=1, sticky="ew",
                                                  padx=(12, 0), pady=4)
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="简称留空就用文件夹名。", bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").grid(row=2, column=0, columnspan=2,
                                                sticky="w", pady=(2, 0))

        base_var = tk.StringVar(value=self.config_data["workplace"])
        line = tk.Frame(body, bg=PAGE_BG)
        line.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        tk.Label(line, text="建在", bg=PAGE_BG, fg=MUTED, font=font(9),
                 ).pack(side="left")
        tk.Label(line, textvariable=base_var, bg=PAGE_BG, fg=TEXT, font=font(9),
                 anchor="w").pack(side="left", padx=(6, 8), fill="x", expand=True)

        def choose_base():
            chosen = filedialog.askdirectory(parent=dialog, title="选择默认工作区目录",
                                             initialdir=base_var.get() or TOOL_DIR)
            if chosen:
                base_var.set(os.path.normpath(chosen))

        PillButton(line, "更改目录", choose_base, bg=PAGE_BG).pack(side="right")

        save = lambda: self._save_quick_workspace(dialog, folder_var, name_var, base_var)
        finish_form(dialog, [("创建", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        folder_entry.focus_set()
        self._center(dialog)

    def _save_quick_workspace(self, dialog, folder_var, name_var, base_var):
        folder = folder_var.get().strip()
        if not folder:
            messagebox.showerror("缺少名称", "请填写文件夹名称。", parent=dialog)
            return
        if re.search(ILLEGAL_CHARS, folder):
            messagebox.showerror(
                "名称非法",
                "文件夹名称不能包含 < > : \" / \\ | ? * 和空格。", parent=dialog)
            return

        base = os.path.normpath(base_var.get().strip() or WORKPLACE_DIR)
        target = os.path.join(base, folder)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            messagebox.showerror("创建失败", "建不了目录：\n{}".format(e), parent=dialog)
            return
        if not os.path.isdir(target):
            messagebox.showerror("创建失败", "目录没建起来：\n{}".format(target),
                                 parent=dialog)
            return

        name = name_var.get().strip() or folder
        known = {path_key(w["path"]) for w in self.config_data["workspaces"]}
        if path_key(target) not in known:
            self.config_data["workspaces"].append({"name": name, "path": target})

        self._set_workplace(base)
        save_config(self.config_data)
        dialog.destroy()
        self.refresh_workspaces()
        self.feedback_var.set("已建好工作区 {}：{}".format(name, target))

    def _open_add_workspace_dialog(self):
        """「＋ 选已有目录」：挑一个已经存在的目录，加进列表里当工作区。"""
        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "选已有目录当工作区")

        tk.Label(body, text="显示名称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        name_var = tk.StringVar()
        name_entry = make_entry(body, name_var, width=38)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        tk.Label(body, text="目录路径", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        path_var = tk.StringVar()
        make_entry(body, path_var, width=38).grid(row=1, column=1, sticky="ew",
                                                  padx=(12, 0), pady=4)

        def browse():
            chosen = filedialog.askdirectory(parent=dialog, title="选择工作区目录")
            if chosen:
                chosen = os.path.normpath(chosen)
                path_var.set(chosen)
                if not name_var.get().strip():
                    name_var.set(os.path.basename(chosen))

        PillButton(body, "浏览…", browse, bg=PAGE_BG).grid(row=1, column=2, padx=(8, 0))
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="显示名称留空就用文件夹名。", bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").grid(row=3, column=0, columnspan=3,
                                                sticky="w", pady=(10, 0))

        save = lambda: self._save_new_workspace(dialog, name_var, path_var)
        finish_form(dialog, [("保存", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        name_entry.focus_set()
        self._center(dialog)

    def _save_new_workspace(self, dialog, name_var, path_var):
        path = os.path.normpath(path_var.get().strip())
        if not path or path == ".":
            messagebox.showerror("缺少路径", "请填写目录路径。", parent=dialog)
            return
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path), parent=dialog)
            return

        name = name_var.get().strip() or os.path.basename(path)
        known = {os.path.normcase(os.path.normpath(w["path"]))
                 for w in self.config_data["workspaces"]}
        if os.path.normcase(path) in known:
            messagebox.showerror("重复添加", "这个目录已经在列表里了。", parent=dialog)
            return

        self.config_data["workspaces"].append({"name": name, "path": path})
        save_config(self.config_data)
        dialog.destroy()
        self.refresh_workspaces()
        self.feedback_var.set("已添加工作区 {}。".format(name))

    def rescan(self):
        added = merge_scanned(self.config_data)
        save_config(self.config_data)
        self.refresh_workspaces()
        if added:
            self.feedback_var.set("新发现 {} 个工作区：{}".format(len(added), "、".join(added)))
        else:
            self.feedback_var.set("扫描目录下没有新工作区。")

    def _center(self, dialog):
        """把对话框摆到主窗口中间。"""
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry("+{}+{}".format(x, y))
