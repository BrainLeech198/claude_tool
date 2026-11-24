"""系统托盘：关窗后留一个图标住在那儿，右键能把窗口叫回来、开新会话、退出。

后端在 claude_tool/tray.py（纯 ctypes，不引第三方库）。这里只管"菜单上摆什么、
点了做什么"以及关窗/叫回这两个动作和主窗怎么配合。

这个行为默认开着（config 里的 close_to_tray），设置「行为开关」页有勾可以关掉；
关掉之后点 X 就跟以前一样直接退。托盘本身没建起来（explorer 没起、非 Windows）
的话也自动退回"直接退"，不会把窗口藏没了又找不回来。
"""
import os
import tkinter as tk

from claude_tool import agents, host
from claude_tool.agent import agent_path
from claude_tool.config import save_config
from claude_tool.paths import TRAY_ICON_FILE
from claude_tool.tray import TrayIcon, item, separator, submenu


class TrayMixin:

    def _setup_tray(self):
        """建托盘图标。建不起来就当没有这回事，关窗照旧直接退。"""
        if not host.TRAY_SUPPORTED:
            return
        try:
            self._tray = TrayIcon(
                root=self,
                icon_file=TRAY_ICON_FILE,
                tooltip=self._tray_tooltip(),
                on_show=self.show_from_tray,
                on_menu=self._tray_menu,
                on_command=self._tray_command)
        except Exception:
            self._tray = None

    def _tray_alive_count(self):
        """还开着几个会话（进程还活着的那些）。托盘提示和菜单标题都用它。"""
        count = 0
        for entry in self.running:
            process = entry.get("proc")
            if process is not None and process.poll() is None:
                count += 1
        return count

    def _tray_tooltip(self):
        count = self._tray_alive_count()
        if count:
            return "Claude 启动器 · {} 个会话在跑".format(count)
        return "Claude 启动器"

    def _tray_menu(self):
        """右键菜单。每次弹之前现算——正在跑的会话、工作区列表都是会变的。

        勾选项/子菜单项都只放"看着这一项就知道会怎样"的话，真正的动作在
        _tray_command 里。
        """
        entries = [
            item("show", "打开启动器", default=True),
            item("session_center", "会话中心"),
        ]
        self._tray_actions = {}

        running = [entry for entry in self.running
                   if entry.get("proc") is not None
                   and entry["proc"].poll() is None]
        if running:
            children = []
            for index, entry in enumerate(running[:12]):
                cmd = "focus:{}".format(index)
                self._tray_actions[cmd] = entry
                # 内嵌的那个没有自己的窗口，点了只能把启动器叫回来。
                label = entry["name"]
                if not entry.get("hwnd"):
                    label += "（内嵌）"
                children.append(item(cmd, label))
            entries.append(submenu("正在跑（{}）".format(len(running)),
                                   children))

        # 用配置里那份完整列表，不用 ws_view——那个是左栏筛过之后的，用户关窗
        # 之前要是留着一行筛选条件，托盘里就只剩那几个了，看着像工作区丢了。
        workspaces = [workspace
                      for workspace in self.config_data.get("workspaces", [])
                      if os.path.isdir(workspace.get("path") or "")]
        if workspaces:
            children = []
            for index, workspace in enumerate(workspaces[:15]):
                cmd = "launch:{}".format(index)
                self._tray_actions[cmd] = workspace
                children.append(item(cmd, workspace["name"]))
            entries.append(submenu("新会话", children))

        entries += [
            separator(),
            item("tool_dir", "打开配置目录"),
            item("toggle_close_to_tray", "关闭窗口时收进托盘",
                 checked=self.close_to_tray_var.get()),
            separator(),
            item("quit", "退出"),
        ]
        return entries

    def _tray_command(self, cmd):
        if cmd == "show":
            self.show_from_tray()
        elif cmd == "session_center":
            # 用 open 而不是 toggle：这扇可能正是收进托盘时藏起来的，toggle
            # 会把它当成"开着"直接关掉，那一下在托盘菜单里很莫名其妙。
            self.show_from_tray()
            self.open_session_center()
        elif cmd == "tool_dir":
            self._open_tool_dir()
        elif cmd == "toggle_close_to_tray":
            self.close_to_tray_var.set(not self.close_to_tray_var.get())
            self._on_close_to_tray_toggle()
        elif cmd == "quit":
            self.close_for_real()
        elif cmd.startswith("focus:"):
            self._tray_focus(self._tray_actions.get(cmd))
        elif cmd.startswith("launch:"):
            self._tray_launch(self._tray_actions.get(cmd))

    def _tray_focus(self, entry):
        """把某个正在跑的会话叫到前面；找不到窗口就退而叫回启动器。"""
        if entry is None:
            return
        hwnd = entry.get("hwnd")
        if hwnd and host.window_alive(hwnd):
            host.focus_window(hwnd)
            return
        self.show_from_tray()

    def _tray_launch(self, workspace):
        """从托盘直接开一个新会话。

        内嵌的时候会话是长在主窗里的，主窗藏着就什么都看不见，得先叫回来；
        没装命令行的话 launch_workspace 会弹安装面板，也得有个主窗接着。别的
        情况不叫窗口——从托盘开新会话图的就是不打断手头的事。
        """
        if workspace is None:
            return
        if self._busy():
            # 换模型 / 搬工作区的流水线正占着进度区，插一脚进去两边都会乱。
            self.show_from_tray()
            self.feedback_var.set("启动器手上有活，等它跑完再开会话。")
            return
        profile = agents.get(workspace.get("agent"))
        if self.embed_var.get() or agent_path(profile) is None:
            self.show_from_tray()
        self.select_workspace(workspace["path"])
        self.launch_workspace(workspace)

    # ── 关窗 / 叫回 ──

    def _tray_child_windows(self):
        """挂在主窗上的那几扇普通 Toplevel（设置 / 插件 / 会话中心）。

        它们不是 transient，Windows 不会跟着 owner 一起收——主窗进了托盘，它们
        还留在屏幕上，"关掉了"却飘着一个设置窗。所以这里自己收、自己放。
        对话框（grab_set 那些）不算：抓着手柄时主窗的 X 根本点不到。
        """
        for name in ("_settings_win", "_plugins_win", "_session_center"):
            window = getattr(self, name, None)
            try:
                if (window is not None and window.winfo_exists()
                        and window.winfo_viewable()):
                    yield window
            except tk.TclError:
                continue

    def hide_to_tray(self):
        """把窗口收进托盘。会话一个都不动——内嵌的那个跟着窗口一起藏起来，
        claude 还在跑，叫回来接着看。

        托盘没建好（比如 explorer 没起来）时返回 False，调用方该走真退——
        把窗口藏起来又没有图标可以叫回来，那才是真的找不着了。
        """
        if self._tray is None or not self._tray.alive:
            return False
        # 先存几何：withdraw 之后量出来的是外框，按那个存每开关一次会大一圈。
        self._save_geometry()
        # 记着收之前是不是最大化：deiconify 只还原成普通大小，不记的话最大化
        # 的用户每次从托盘回来都要再点一次最大化。
        self._tray_state = str(self.state())
        # 先记下"收之前哪些附属窗是露着的"，叫回来时原样放出去。
        self._tray_hidden = list(self._tray_child_windows())
        for window in self._tray_hidden:
            try:
                window.withdraw()
            except tk.TclError:
                pass
        self.withdraw()
        self._tray.set_tooltip(self._tray_tooltip())
        # 第一次收进去说一声去哪儿找，不然用户以为程序被关掉了。只说一次。
        if not self.config_data.get("tray_hint_shown"):
            self._tray.notify(
                "Claude 启动器",
                "还在后台跑着，会话没断。双击托盘图标叫回来，右键有更多操作。")
            self.config_data["tray_hint_shown"] = True
            save_config(self.config_data)
        return True

    def show_from_tray(self):
        """把窗口从托盘（或最小化）叫回来。"""
        was, self._tray_state = self._tray_state, "normal"
        try:
            if str(self.state()) == "withdrawn":
                self.deiconify()
            self.state("zoomed" if was == "zoomed" else "normal")
            self.lift()
            self.focus_force()
        except tk.TclError:
            return
        # 收进去时一并藏起来的附属窗，原样放回来。
        windows, self._tray_hidden = getattr(self, "_tray_hidden", []), []
        for window in windows:
            try:
                if window.winfo_exists():
                    window.deiconify()
                    window.lift()
            except tk.TclError:
                pass
        if self._tray is not None:
            self._tray.set_tooltip(self._tray_tooltip())
        # 内嵌的控制台是独立窗口、认本窗当 owner：藏起来时它跟着藏，叫回来得
        # 重新贴一遍位置。藏/显这一趟 <Configure> 不一定来，别指望它。
        if getattr(self, "embedded", None) is not None:
            try:
                self.embedded.place()
            except Exception:
                pass
