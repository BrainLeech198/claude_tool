"""插件的发现、信任记录与加载。

## 信任模型（本文件唯一要紧的事）

同进程加载 = 插件能在宿主进程里跑任意代码，**没有隔离**。所以：

- **首次见到一律未启用** —— 白名单，不是黑名单：新东西默认不给跑。
- 用户点过「启用」才 import。
- **版本变了要重新问一次** —— 不然一个插件第一次被审过之后，后面每个版本都能为
  所欲为。实现上不存单独的"信任版本"字段：状态文件里直接记「启用的那个版本号」，
  读到的版本跟它对不上就当作没启用——"要重新确认"是自然结果，不是额外逻辑。

状态文件 `~/.claude_tool/plugins.json`：

    {"enabled": {"novel_assistant": "0.1.0"}}

**坏插件不写进去、也不抛异常**：一条清单读不成不该影响别条（理由见 manifest.py
开头那两档）。加载失败同理——记在插件自己身上，照实显示。
"""
import importlib.util
import json
import os
import sys

from claude_tool import __version__, paths
from claude_tool.plugins import manifest as manifest_mod


class Plugin:
    """发现出来的一个插件。

    三个字段决定它现在是什么状态，互斥：

    | `manifest` | `error` | `blocked` | 状态 |
    |---|---|---|---|
    | 有 | — | — | 能用；启不启用再看 `enabled` |
    | — | 有 | — | **坏插件**：清单就读不成，要用户去改文件 |
    | 有 | — | 有 | **门禁不过**：清单没毛病，是这台宿主编不动 |

    `enable_error` / `module` 是加载那一刻才填的（用户在插件管理里点「启用」时）。
    """

    def __init__(self, path, folder_name, builtin=False):
        self.path = path
        self.folder_name = folder_name
        self.builtin = builtin     # 随启动器发出去的那份；用户自己导入的是 False
        self.manifest = None
        self.error = None          # 坏插件的原因（清单层）
        self.blocked = None        # 门禁不过的原因
        self.enabled = False       # 用户点过「启用」、且版本对得上
        self.stale = False         # 信任过，但版本变了 -> 要重新确认
        self.load_error = None     # import 那一步炸了
        self.module = None

    @property
    def id(self):
        """认人用的名字。清单读不成时退回目录名——插件管理里总得有个标题。"""
        return self.manifest.id if self.manifest else self.folder_name

    @property
    def name(self):
        return self.manifest.name if self.manifest else self.folder_name

    @property
    def version(self):
        return self.manifest.version if self.manifest else ""

    @property
    def usable(self):
        """清单没问题、门禁也过了。**不代表已启用。**"""
        return (self.manifest is not None and self.error is None
                and self.blocked is None)

    @property
    def problem(self):
        """一条给用户看的、它现在为什么不能用/没在跑的话。能用就是空串。"""
        return self.error or self.blocked or self.load_error or ""

    def __repr__(self):
        return "<Plugin {} v{} enabled={}>".format(self.id, self.version,
                                                   self.enabled)


class Registry:
    """扫插件目录、记住信过谁、把启用过的加载起来。

    **扫两个目录**（见 paths）：随启动器发出去的内置插件，加上用户自己导入的那份。
    `plugin_dir` / `plugin_dirs` / `state_file` 可注入是为了让探针跑在沙箱里——
    **不注入就是用户的真目录**，探针用之前务必看 `_probe_common.sandbox`。
    """

    def __init__(self, plugin_dir=None, state_file=None, plugin_dirs=None):
        if plugin_dirs is not None:
            self.plugin_dirs = [d for d in plugin_dirs if d]
        elif plugin_dir is not None:
            # 单目录注入（老调用点与探针走这条）。
            self.plugin_dirs = [plugin_dir]
        else:
            # 内置那份排前、用户那份排后——后扫到的盖掉先扫到的，于是"用户用一个
            # 自己改过的版本顶掉内置的同名插件"天然生效（见 discover）。
            self.plugin_dirs = [paths.PLUGIN_DIR, paths.USER_PLUGIN_DIR]
        self.plugin_dir = self.plugin_dirs[0] if self.plugin_dirs else paths.PLUGIN_DIR
        self.state_file = (state_file if state_file is not None
                           else paths.PLUGIN_STATE_FILE)
        self.plugins = []

    # ── 状态文件 ────────────────────────────────────────────────
    def _read_state(self):
        if not os.path.isfile(self.state_file):
            return {}
        try:
            with open(self.state_file, encoding="utf-8") as handle:
                data = json.load(handle)
        except (ValueError, OSError):
            # 状态文件坏了就当作"什么都没有"——大不了所有插件重新问一遍。
            # 这里**不报错**：这份文件不是用户手写的，为它挡住整个插件页不值得。
            return {}
        if not isinstance(data, dict):
            return {}
        enabled = data.get("enabled")
        return enabled if isinstance(enabled, dict) else {}

    def _write_state(self, enabled):
        folder = os.path.dirname(self.state_file)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"enabled": enabled}, handle, ensure_ascii=False, indent=2)

    def trusted_version(self, pid):
        """记下的"信过哪个版本"；没信过是 None。"""
        return self._read_state().get(pid)

    def is_enabled(self, plugin):
        """启用没有。

        **版本对不上就当没启用**——这就是"插件升级要重新确认"的实现方式，
        不是额外逻辑，是这一条比较。
        """
        if not plugin.usable:
            return False
        return self.trusted_version(plugin.id) == plugin.version

    def set_enabled(self, plugin, on):
        """记下/取消信任。`on=True` 时记的是**当前这个版本**。"""
        enabled = dict(self._read_state())
        if on:
            enabled[plugin.id] = plugin.version
        else:
            enabled.pop(plugin.id, None)
        self._write_state(enabled)
        self.refresh_flags()

    def refresh_flags(self):
        """按状态文件把每个插件的 `enabled` / `stale` 重算一遍。"""
        trusted = self._read_state()
        for plugin in self.plugins:
            if not plugin.usable:
                plugin.enabled = False
                plugin.stale = False
                continue
            remembered = trusted.get(plugin.id)
            plugin.enabled = remembered == plugin.version
            plugin.stale = remembered is not None and not plugin.enabled

    # ── 发现 ────────────────────────────────────────────────────
    def discover(self, host_version=None):
        """扫一遍所有插件目录，返回 `Plugin` 列表。

        **不 import 任何插件代码**——这一步只读 `plugin.json`。信任确认要看的东西
        必须能在"还没跑过别人代码"的前提下摆出来（见 manifest.py 开头）。

        目录按 `plugin_dirs` 的先后扫，**同 id 时后扫到的盖掉先扫到的**：内置那份
        在前、用户那份在后，于是用户能用自己的版本顶掉内置同名插件。被顶掉的那份
        不出现在结果里——否则同一个 id 会出现两张卡，还各说各的状态。
        """
        host_version = host_version or __version__
        found = {}
        for index, base in enumerate(self.plugin_dirs):
            if not os.path.isdir(base):
                continue
            # `plugin_dirs` 的第一项＝"随启动器发出去的内置那份"（见 __init__）。
            # 这个标记只用来在插件管理里分「内置」和「用户导入」，不影响加载，也不
            # 影响信任——用户自己导入的插件一样要过一遍启用。
            builtin = index == 0
            for folder_name in sorted(os.listdir(base)):
                path = os.path.join(base, folder_name)
                if not os.path.isdir(path):
                    continue
                # 下划线/点开头的一律不当插件：`__pycache__` 之类，以及"我暂时
                # 不想让它被扫到"这种意图。
                if folder_name.startswith(("_", ".")):
                    continue
                plugin = Plugin(path, folder_name, builtin=builtin)
                try:
                    plugin.manifest = manifest_mod.read(path)
                except manifest_mod.BadManifest as exc:
                    plugin.error = str(exc)
                else:
                    plugin.blocked = manifest_mod.gate(plugin.manifest,
                                                       host_version)
                found[plugin.id] = plugin
        self.plugins = sorted(found.values(), key=lambda p: p.id)
        self.refresh_flags()
        return self.plugins

    def get(self, pid):
        for plugin in self.plugins:
            if plugin.id == pid:
                return plugin
        return None

    # ── 加载 ────────────────────────────────────────────────────
    def load(self, plugin, host):
        """真去 import 插件的代码、把 `host` 交给它。

        **整个宿主里唯一执行别人代码的地方。** 所以它只做这一件事，且由用户点
        「启用」才被调用；调用方拿到异常要照实显示、不能吞掉。
        """
        if not plugin.usable:
            raise RuntimeError("{} 不可加载：{}".format(plugin.id, plugin.problem))
        entry_file = plugin.manifest.entry_file
        entry_path = os.path.join(plugin.path, entry_file)
        if not os.path.isfile(entry_path):
            raise RuntimeError("入口文件找不到：{}".format(entry_file))

        # 插件自己那一层的同级 import（`from x import y`）能用。
        if plugin.path not in sys.path:
            sys.path.insert(0, plugin.path)

        mod_name = "claude_tool_plugin_" + plugin.id
        spec = importlib.util.spec_from_file_location(mod_name, entry_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("加载不了 {}".format(entry_path))
        module = importlib.util.module_from_spec(spec)
        # 先塞进 sys.modules 再 exec：插件里用 dataclass / 相对 import 时，
        # 解释器要能按名字找到自己。
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
            entry = getattr(module, plugin.manifest.entry_func, None)
            if not callable(entry):
                raise RuntimeError("{} 里没有 {} 这个函数".format(
                    entry_file, plugin.manifest.entry_func))
            entry(host)
        except Exception:
            sys.modules.pop(mod_name, None)
            raise
        plugin.module = module
        plugin.load_error = None
        return module

    def load_enabled(self, host):
        """把所有「已启用且能用」的插件加载起来。

        单个插件炸了只记在它自己身上（`load_error`），继续加载下一个——一个坏插件
        不该让别的插件也跟着不跑。返回 `(成功列表, 失败列表)`。
        """
        ok, failed = [], []
        for plugin in self.plugins:
            if not (plugin.usable and plugin.enabled):
                continue
            try:
                self.load(plugin, host)
            except Exception as exc:                      # noqa: BLE001
                plugin.load_error = "{}: {}".format(type(exc).__name__, exc)
                failed.append(plugin)
            else:
                ok.append(plugin)
        return ok, failed
