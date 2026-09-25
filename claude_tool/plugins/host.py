"""喂给插件的那个 `host` 对象 —— 插件能碰到的**全部**世界。

## 为什么给一个对象，而不是让插件自己 import 宿主

`import claude_tool.ui.launcher` 一样能拿到一切 —— 但那把插件钉死在宿主的**内部
结构**上：哪天拆个模块、改个方法名，所有插件一起烂。给一个窄接口的意义在于
**这层接口是一句承诺**：`host` 上的东西要改，得先动 `api_version`（见
manifest.API_VERSION）。所以这里刻意**不**暴露 `Launcher` 实例、`config_data`、
以及任何 `_` 开头的私有物。插件要的是能力，不是宿主的内部状态。

## 挂载点是"动作"，不是控件

插件注册的是 `(where, 文字, 回调)`，**长什么样、摆在哪儿由宿主决定**。这样插件
不用知道 `PillButton` 是什么，宿主也能在插件没加载时不留空位——布局契约留在宿主
这边，插件那边零布局知识。

## 谁负责调 notify_*

宿主。`notify_workspace_change()` 由 `select_workspace` 那边调
（见 `ui/nav.py`），`bump_mounts()` 由宿主在重建完挂载点之后调。插件只管
`on_workspace_change(cb)` 订阅。
"""
from claude_tool import __version__
from claude_tool.plugins import manifest as manifest_mod


class Host:
    """插件的全部世界。

    `app` 是主窗（`Launcher`），可传 None —— 那样数据接口照常能用，只是挂载点
    和状态行没人接（探针里就是这么用的）。
    """

    API_VERSION = manifest_mod.API_VERSION

    # 插件能挂的三个地方。加新的要过 api_version。
    #   toolbar           主窗顶栏右侧，插件的全局入口
    #   workspace_detail  右栏详情区，拿到"当前选中那本"
    #   workspace_row     左栏某一行的右键菜单，针对某一本、不一定先选中
    MOUNTS = ("toolbar", "workspace_detail", "workspace_row")

    def __init__(self, app=None):
        self._app = app
        self._actions = {where: [] for where in self.MOUNTS}
        self._pages = []
        self._watchers = []
        self._on_mounts = None      # 宿主挂的"挂载点变了，重建一下"
        self._log_sink = None       # 宿主挂的"往状态行写"

    # ── 身份 ────────────────────────────────────────────────────
    @property
    def version(self):
        """宿主版本，比如 "0.4.0"。插件拿它做功能探测用。"""
        return __version__

    @property
    def api_version(self):
        """接口版本。跟 `plugin.json` 里那个对得上才允许加载（见 manifest.gate）。"""
        return self.API_VERSION

    @property
    def theme(self):
        """`claude_tool.theme` 模块本身（颜色、字体、间距常数都在里面）。"""
        from claude_tool import theme
        return theme

    @property
    def widgets(self):
        """`claude_tool.widgets` 模块本身（`PillButton` 这些画出来的控件）。"""
        from claude_tool import widgets
        return widgets

    # ── 数据 ────────────────────────────────────────────────────
    def workspaces(self):
        """工作区列表。

        优先给宿主**内存里那份**——用户在界面上刚改的名字、刚挪的顺序，立刻就该
        看得到；内存那份没有才回落到读盘。返回列表副本，**里面的 dict 还是宿主
        那一份，插件不要改它**（改它不会落盘，只会让界面跟文件对不上）。
        """
        live = getattr(self._app, "config_data", None)
        if isinstance(live, dict) and isinstance(live.get("workspaces"), list):
            return list(live["workspaces"])
        from claude_tool import config
        return list(config.load_config().get("workspaces") or [])

    def current_workspace(self):
        """左栏当前选中那条的 dict；没选中（或宿主没接界面）就是 None。"""
        picker = getattr(self._app, "selected_entry", None)
        if not callable(picker):
            return None
        return picker()

    # ── 订阅 ────────────────────────────────────────────────────
    def on_workspace_change(self, callback):
        """订阅"用户换了工作区"。`callback(entry_or_None)`。

        订阅那一刻**不**回调一次——插件要初始化自己先调 `current_workspace()`，
        别把这两件事混成一件。
        """
        self._watchers.append(callback)

        def unsubscribe():
            if callback in self._watchers:
                self._watchers.remove(callback)

        return unsubscribe

    def notify_workspace_change(self, entry):
        """宿主用。见模块开头"谁负责调 notify_*"。"""
        for callback in list(self._watchers):
            try:
                callback(entry)
            except Exception as exc:                      # noqa: BLE001
                # 一个插件订阅者炸了不能连累别的订阅者，更不能把宿主带崩。
                self.log("插件回调出错：{}: {}".format(type(exc).__name__, exc))

    # ── 干活 ────────────────────────────────────────────────────
    def open_claude(self, path, prompt=None, cont=False, permission=None,
                    settings=None):
        """在 `path` 下开一个新终端跑 claude，返回那个进程对象。

        `settings` 是模型预设文件的路径，**不传就按 claude 自己的默认来**（插件
        不该假设用户在启动器里选了哪个模型）。`permission` 是权限等级，取值跟
        启动器自己那套一致（`bypassPermissions` / `acceptEdits` / ...）。
        """
        from claude_tool import host as platform
        return platform.spawn_terminal(path, cont=cont, prompt=prompt,
                                      settings=settings, permission=permission)

    def claude_args(self, **kwargs):
        """拼一份 claude 的 argv（不真起进程）。参数跟 `claude.claude_args` 一样。"""
        from claude_tool import claude
        return claude.claude_args(**kwargs)

    def open_dir(self, path):
        """在系统文件管理器里打开一个目录。"""
        from claude_tool import host as platform
        return platform.open_path(path)

    def log(self, text):
        """往主窗底下那行状态写一句话。插件报进度用这个，**别自己 print**——
        打包成 exe 之后没有控制台，print 出去谁也看不见。"""
        text = str(text)
        if self._log_sink is not None:
            self._log_sink(text)
        elif self._app is not None and hasattr(self._app, "feedback_var"):
            self._app.feedback_var.set(text)

    # ── 注册界面 ────────────────────────────────────────────────
    def register_action(self, where, text, callback):
        """往一个挂载点加一个动作。`where` 只能是 `MOUNTS` 里那三个。

        返回一个**撤销函数**：插件重新注册之前应该先撤掉旧的，不然用户点两次
        「启用」就会挂上两份、出现两个同名按钮。
        """
        if where not in self._actions:
            raise ValueError("没有 {!r} 这个挂载点，能用的是 {}".format(
                where, " / ".join(self.MOUNTS)))
        entry = (str(text), callback)
        self._actions[where].append(entry)
        self.bump_mounts()

        def unregister():
            if entry in self._actions[where]:
                self._actions[where].remove(entry)
                self.bump_mounts()

        return unregister

    def register_settings_page(self, title, build):
        """往设置窗加一页。`build(parent)` 收到一个 `Frame`，把界面摆进去就行。

        返回撤销函数，语义跟 `register_action` 一样。
        """
        entry = (str(title), build)
        self._pages.append(entry)
        self.bump_mounts()

        def unregister():
            if entry in self._pages:
                self._pages.remove(entry)
                self.bump_mounts()

        return unregister

    def actions(self, where):
        """宿主用：某个挂载点上现在挂着哪些动作。"""
        return list(self._actions.get(where, ()))

    def settings_pages(self):
        """宿主用：插件加进来的设置页。"""
        return list(self._pages)

    # ── 宿主接进来的两个钩子 ────────────────────────────────────
    def set_mount_hook(self, callback):
        """宿主用：挂载点变了重建界面。"""
        self._on_mounts = callback

    def set_log_sink(self, callback):
        """宿主用：换一种方式写状态行（默认是 `app.feedback_var`）。"""
        self._log_sink = callback

    def bump_mounts(self):
        """告诉宿主"挂载点变了"。插件注册完动作由这里自动触发，插件自己不用调。"""
        if self._on_mounts is not None:
            self._on_mounts()
