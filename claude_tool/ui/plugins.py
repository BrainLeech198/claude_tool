"""插件宿主接进主窗的那一层：挂载点怎么画、插件管理窗、左栏右键菜单。

三个挂载点长什么样由这儿定（能挂哪儿见 plugins/host.py 的 MOUNTS）：

  toolbar           顶栏右侧一排胶囊，插件的全局入口
  workspace_detail  右栏「插件挂载区」，拿到当前选中那本
  workspace_row     左栏某一行的右键菜单，针对某一行、**不一定先选中**

## 为什么改插件状态时要重建**整份** host

插件在 host 上能挂动作、订阅工作区、加设置页，还能自己留别的引用（定时器、缓存、
闭包）。想从外面把"某一个插件留下的东西"摘干净是做不到的——它注册的回调长什么样
宿主根本不知道。所以这里不摘，改状态就**重新造一份 host、把还启用的插件重放一遍**：
新 host 上天然只有还活着的那几个。

代价是每个插件的 `entry` 会被**再调一次**，所以插件作者得保证 `entry` 是幂等的
（在同一个 host 上重复调用 = 重新注册一遍，不留重复的东西）。这一条跟着
`host.register_action` 返回的撤销函数一起，写进插件作者要看的契约里。

## 目录在哪儿

插件就是 `claude_tool/plugins/` 下的一个文件夹（打包后在 exe 旁边的 `_internal/`
里，见 paths.PLUGIN_DIR）。**用户自己写的插件也丢这儿**——没有第二个目录。
"""
import os
import tkinter as tk

from claude_tool.paths import PLUGIN_DIR
from claude_tool.theme import (
    ACCENT,
    BORDER,
    MUTED,
    PAGE_BG,
    PANEL_BG,
    TEXT,
    WARN,
    font,
)
from claude_tool.widgets import PillButton

# 插件管理窗的大小。够摆三四个插件卡片，再多往下滚。
PLUGINS_W, PLUGINS_H = 560, 460


class PluginsMixin:

    # ── 起来 ────────────────────────────────────────────────────

    def _setup_plugins(self):
        """扫一遍插件、把用户启用过的加载起来。`__init__` 里 `_build_ui` 之后调。

        **不在这里问用户任何东西**：没启用过的插件一个字节的代码都不跑（见
        plugins/registry.py 开头那段信任模型）。想启用得去插件管理窗点一下。
        """
        from claude_tool.plugins.registry import Registry
        self.plugin_registry = Registry()
        self.plugin_host = None
        self._plugins_win = None
        self._plugins_body = None
        self._reload_plugins()
        # 起来那会儿还没有插件订阅工作区（插件是刚加载的，订阅在上面这步里建好
        # 了），所以直接把当前的选中推一次，让订阅者拿到初值。之后由
        # select_workspace 负责推。
        self._notify_plugin_workspace()

    def _reload_plugins(self):
        """重建一份 host、把所有「已启用且能用」的插件加载一遍。

        单个插件炸了不影响别的（见 registry.load_enabled）。返回 `(成功, 失败)`。
        """
        from claude_tool.plugins.host import Host
        self.plugin_registry.discover()
        host = Host(self)
        host.set_mount_hook(self._render_plugin_mounts)
        self.plugin_host = host
        ok, failed = self.plugin_registry.load_enabled(host)
        self._render_plugin_mounts()
        if failed:
            names = "、".join(p.id for p in failed)
            self.feedback_var.set("插件加载失败：{}（进「插件」看原因）".format(names))
        return ok, failed

    def _notify_plugin_workspace(self):
        """把"当前选中那本"推给插件。没加载过插件（或还没有 host）就当没事。"""
        host = getattr(self, "plugin_host", None)
        if host is not None:
            host.notify_workspace_change(self.selected_entry())

    # ── 画挂载点 ────────────────────────────────────────────────

    def _render_plugin_mounts(self):
        """重建两处界面挂载点。挂载点变了（插件注册了动作）由 host 回调过来。

        早于 `_build_ui` 被调到就直接返回——那会儿 `toolbar_actions` 还没建。
        """
        if getattr(self, "plugin_host", None) is None:
            return
        self._render_plugin_toolbar()
        self._render_plugin_detail()

    def _render_plugin_toolbar(self):
        row = getattr(self, "toolbar_actions", None)
        if row is None:
            return
        for child in row.winfo_children():
            child.destroy()
        for text, callback in self.plugin_host.actions("toolbar"):
            PillButton(row, text, lambda cb=callback: self._run_plugin(cb),
                       bg=PANEL_BG).pack(side="right", padx=(0, 6))

    def _render_plugin_detail(self):
        area = getattr(self, "detail_plugin_area", None)
        if area is None:
            return
        for child in area.winfo_children():
            child.destroy()
        actions = self.plugin_host.actions("workspace_detail")
        if not actions:
            return
        tk.Label(area, text="插件", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(anchor="w")
        line = tk.Frame(area, bg=PAGE_BG)
        line.pack(fill="x", pady=(6, 0))
        for text, callback in actions:
            PillButton(line, text, lambda cb=callback: self._run_plugin(cb),
                       bg=PAGE_BG, height=26).pack(side="left", padx=(0, 6))

    def _plugin_row_menu(self, event, item):
        """左栏某一行的右键菜单。没有插件挂 `workspace_row` 就什么都不弹。"""
        host = getattr(self, "plugin_host", None)
        actions = host.actions("workspace_row") if host is not None else []
        if not actions:
            return
        menu = tk.Menu(self, tearoff=0)
        for text, callback in actions:
            menu.add_command(
                label=text,
                command=lambda cb=callback, it=item: self._run_plugin(cb, it))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _run_plugin(self, callback, *args):
        """跑一个插件动作。插件炸了只写状态行，**不能把宿主带崩**。"""
        try:
            callback(*args)
        except Exception as exc:                          # noqa: BLE001
            self.feedback_var.set("插件动作出错：{}: {}".format(
                type(exc).__name__, exc))

    # ── 插件管理窗 ──────────────────────────────────────────────

    def open_plugins(self):
        if self._plugins_win is None or not self._plugins_win.winfo_exists():
            self._build_plugins_window()
        else:
            self._plugins_win.deiconify()
            self._plugins_win.lift()
        self._render_plugins_window()

    def _build_plugins_window(self):
        win = tk.Toplevel(self)
        win.title("插件")
        win.configure(bg=PAGE_BG)
        win.protocol("WM_DELETE_WINDOW", self._close_plugins)
        self._plugins_win = win

        # 顶上那行说清"插件放哪儿"——不然用户拿着一个插件文件夹不知道往哪儿搁。
        head = tk.Frame(win, bg=PAGE_BG)
        head.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(head, text="插件目录", bg=PAGE_BG, fg=MUTED,
                 font=font(9)).pack(anchor="w")
        tk.Label(head, text=PLUGIN_DIR, bg=PAGE_BG, fg=TEXT, font=font(9),
                 anchor="w", justify="left", wraplength=PLUGINS_W - 60,
                 ).pack(anchor="w", pady=(2, 0))
        tk.Label(head, text="每个插件是这里面的一个文件夹，里面必须有 plugin.json。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                 justify="left", wraplength=PLUGINS_W - 60).pack(anchor="w")

        tk.Frame(win, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(12, 0))

        self._plugins_body = tk.Frame(win, bg=PAGE_BG)
        self._plugins_body.pack(fill="both", expand=True, padx=16, pady=(12, 0))

        foot = tk.Frame(win, bg=PAGE_BG)
        foot.pack(fill="x", padx=16, pady=12)
        PillButton(foot, "重新扫描", self._rescan_plugins, bg=PAGE_BG).pack(side="left")
        PillButton(foot, "打开插件目录", self._open_plugin_dir,
                   bg=PAGE_BG).pack(side="left", padx=(6, 0))

        win.minsize(PLUGINS_W, PLUGINS_H)
        win.geometry("{}x{}".format(PLUGINS_W, PLUGINS_H))
        self._center(win)

    def _close_plugins(self):
        if self._plugins_win is not None and self._plugins_win.winfo_exists():
            self._plugins_win.withdraw()

    def _rescan_plugins(self):
        self._reload_plugins()
        self._render_plugins_window()
        self.feedback_var.set("插件目录重扫过了")

    def _open_plugin_dir(self):
        from claude_tool.host import open_path
        if not os.path.isdir(PLUGIN_DIR):
            os.makedirs(PLUGIN_DIR, exist_ok=True)
        open_path(PLUGIN_DIR)

    def _render_plugins_window(self):
        body = self._plugins_body
        if body is None or not body.winfo_exists():
            return
        for child in body.winfo_children():
            child.destroy()
        plugins = self.plugin_registry.plugins
        if not plugins:
            tk.Label(body, text="还没有插件。", bg=PAGE_BG, fg=TEXT,
                     font=font(10, True), anchor="w").pack(anchor="w", pady=(6, 4))
            tk.Label(body, text="把插件文件夹放进上面那个目录，再按「重新扫描」。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                     justify="left", wraplength=PLUGINS_W - 60).pack(anchor="w")
            return
        for plugin in plugins:
            self._render_plugin_card(body, plugin)

    def _render_plugin_card(self, parent, plugin):
        """一个插件一张卡：名字 / 版本 / 作者 / 状态 / 一颗按钮。"""
        card = tk.Frame(parent, bg=PAGE_BG)
        card.pack(fill="x", pady=(0, 12))

        head = tk.Frame(card, bg=PAGE_BG)
        head.pack(fill="x")
        tk.Label(head, text=plugin.name, bg=PAGE_BG, fg=TEXT,
                 font=font(11, True)).pack(side="left")
        if plugin.version:
            tk.Label(head, text="v" + plugin.version, bg=PAGE_BG, fg=MUTED,
                     font=font(9)).pack(side="left", padx=(8, 0))
        author = plugin.manifest.author if plugin.manifest else ""
        if author:
            tk.Label(head, text="· " + author, bg=PAGE_BG, fg=MUTED,
                     font=font(9)).pack(side="left", padx=(8, 0))

        if plugin.manifest and plugin.manifest.description:
            tk.Label(card, text=plugin.manifest.description, bg=PAGE_BG,
                     fg=MUTED, font=font(9), anchor="w", justify="left",
                     wraplength=PLUGINS_W - 60).pack(anchor="w", pady=(2, 0))

        note, color = self._plugin_status(plugin)
        tk.Label(card, text=note, bg=PAGE_BG, fg=color, font=font(9),
                 anchor="w", justify="left", wraplength=PLUGINS_W - 60,
                 ).pack(anchor="w", pady=(4, 0))

        row = tk.Frame(card, bg=PAGE_BG)
        row.pack(fill="x", pady=(6, 0))
        if plugin.usable:
            if plugin.enabled:
                PillButton(row, "停用", lambda p=plugin: self._toggle_plugin(p, False),
                           bg=PAGE_BG, height=26).pack(side="left")
            elif plugin.stale:
                PillButton(row, "重新确认", lambda p=plugin: self._toggle_plugin(p, True),
                           primary=True, bg=PAGE_BG, height=26).pack(side="left")
            else:
                PillButton(row, "启用", lambda p=plugin: self._toggle_plugin(p, True),
                           primary=True, bg=PAGE_BG, height=26).pack(side="left")
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(0, 12))

    def _plugin_status(self, plugin):
        """给用户看的一行状态 + 用什么颜色。"""
        if plugin.error:
            return "清单有问题：" + plugin.error, WARN
        if plugin.blocked:
            return plugin.blocked, WARN
        if plugin.enabled:
            if plugin.load_error:
                return "已启用，但加载失败：" + plugin.load_error, WARN
            return "已启用", ACCENT
        if plugin.stale:
            return "插件更新过了（{}），需要重新确认才继续跑".format(plugin.version), WARN
        return "未启用", MUTED

    def _toggle_plugin(self, plugin, on):
        """启用 / 停用，然后重建整份 host（理由见文件开头那段）。"""
        pid, name = plugin.id, plugin.name
        self.plugin_registry.set_enabled(plugin, on)
        _, failed = self._reload_plugins()
        if on and pid in [p.id for p in failed]:
            # 起来了又摔了：关回去，别让它每次开机都再摔一遍。
            self.plugin_registry.set_enabled(plugin, False)
            self._reload_plugins()
            self.feedback_var.set("「{}」加载失败，已关回去：{}".format(
                name, plugin.load_error or ""))
        elif on:
            self.feedback_var.set("已启用「{}」".format(name))
        else:
            self.feedback_var.set("已停用「{}」".format(name))
        self._render_plugins_window()
