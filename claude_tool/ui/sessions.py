"""会话：开出去的那些 claude 窗口、「正在跑」那块，以及交接文档那条流水线。

从 launcher.py 里按域切出来的一块（见 设计说明-0.4.md 第三节）。工作区列表
（有哪些书）在 ui/workspaces.py，把书塞进窗口看的那套在 ui/terminal.py——这里
管的是"把书翻开"，以及翻开之后那些活。

以 mixin 的形式挂在 Launcher 上——主窗那些实例状态（running、_task_queue、
_handoff_proc、ws_view、version_var…）都还在它的 __init__ 里，一个字没动。
"""
import os
import queue
import subprocess
import tempfile
import time
import tkinter as tk
from tkinter import messagebox, ttk

from claude_tool import versions
from claude_tool.claude import CREATE_NO_WINDOW, claude_exe
from claude_tool.config import save_config
from claude_tool.handoff import (
    HANDOFF_FILE,
    HANDOFF_TOOLS,
    autonomy_caption,
    ensure_hook_settings,
    handoff_prompt,
    is_git_repo,
    tier_flags,
)
from claude_tool.host import (
    WINDOW_CONTROL,
    bring_next_to,
    close_window,
    fresh_terminal,
    spawn_terminal,
    terminal_windows,
    toggle_topmost,
    window_alive,
    window_position,
)
from claude_tool.permissions import permission_option, workspace_permission
from claude_tool.theme import (
    ACCENT,
    BORDER,
    MUTED,
    PAGE_BG,
    PANEL_BG,
    SIDE_GAP,
    TEXT,
    ellipsize,
    font,
)
from claude_tool.widgets import PillButton


class SessionsMixin:

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
        # 摆在 notice_area 里排在进度区后面（先摆的在上面，进度区用 before= 插队）。
        # 0.4 之前锚的是 self.model_list.head ——「模型」区现在在设置窗里，主窗上
        # 没有那个锚点了，改由这块容器自己定顺序。
        self.running_frame.pack(fill="x")
        self._running_shown = True
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
            # 锚在「正在跑」上面；没有「正在跑」就自己占头一格。两者都在
            # notice_area 里，容器自己没摆出来时这块也不会出现（父控件没映射）。
            if self._running_shown:
                self.jobs_frame.pack(fill="x", pady=(16, 0),
                                     before=self.running_frame)
            else:
                self.jobs_frame.pack(fill="x", pady=(16, 0))
        else:
            self.jobs_frame.pack_forget()
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
        """Ctrl+1~9：选中第 n 个，然后开。

        以前是"直接开、不选中"。0.4 把选中和工作分成了两件事（左栏点一下只
        选中，开会话是右栏按钮的事），但这条快路是给熟手用的，多一步选中没有
        意义——选中和开一起做，手感不变。

        开会话本身还是走 open_workspace 那个对话框（权限等级只有那儿能改），
        跟鼠标点右栏「新会话」一样。
        """
        if 1 <= number <= len(self.ws_view):
            item = self.ws_view[number - 1]
            self.select_workspace(item["path"])
            self.open_workspace(item)
            return "break"

    def _focus_filter(self, _event=None):
        self.filter_entry.focus_set()
        self.filter_entry.select_range(0, "end")
        return "break"

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