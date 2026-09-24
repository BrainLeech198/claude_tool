"""后台跑 GUI 探针：把窗口摆到可视桌面外面，屏幕上不出现东西。

    python _probe_run.py <探针.py> [探针自己的参数...]
    python _probe_run.py --onscreen <探针.py> [...]    # 需要窗口真在屏幕上的那几条

## 为什么

GUI 探针要真建窗口才量得准（`winfo_width` / `winfo_reqwidth` 都得窗口 map 出来
才有数），但窗口一冒出来就打扰在电脑前的人。这里在探针 import tkinter 之前先动
两处手脚：

1. 每个新窗口（`tk.Tk` 和 `tk.Toplevel`）建完就挪到 `+30000+30000`——在可视
   桌面之外，人看不见；窗口照样 map、照样渲染，所以量出来的数和截图都不受影响。
2. `place_window` 换成空操作：不然 `_restore_geometry` 会照着存下来的坐标把
   窗口摆回屏幕里（`_widen` 那条路也走它）。

Windows 下 `IsWindowVisible` 对屏幕外的窗口仍然为真，所以探针里那些
「枚举本进程的可见窗口」找窗口、`PrintWindow` 截图的路子都不受影响。

## 有一条不能踩：别提前 import 任何 claude_tool.*

`paths.py` 是在**它自己 import 那一下**把 `~` 展开成模块级常量的：

    TOOL_DIR = os.path.join(os.path.expanduser("~"), ".claude_tool")
    CONFIG_FILE = os.path.join(TOOL_DIR, "launcher.json")

探针全靠「先把 USERPROFILE / HOME 指到沙箱、再 import claude_tool」拿到隔离，
最晚也得赶在 `claude_tool.paths` 第一次被 import 之前。所以这个启动器要动
`place_window` 只能在模块**加载完那一刻**去动——不能图省事 `import
claude_tool.host` 先摸一把。

踩了会怎样（0.4 拆模块那轮真踩了）：`paths` 被提前冻在真实家目录上，之后探针
再把 USERPROFILE 指到沙箱也没用——`CONFIG_FILE` 那个常量已经定了。表现是探针写
的配置落到**用户真身上**，读的却是沙箱路径。`_probe_winsize` 是当场露馅的那个：
它读沙箱里那份 launcher.json 想确认"开窗时 window 那格空着"，文件压根不存在、
直接 FileNotFoundError；而窗口量出来 836x890，正是用户真配置里存的那个尺寸——
两边一对照就知道配置串了。

## 什么时候要 --onscreen

量的东西跟窗口**位置**有关的探针——比如 `_probe_winctl`（验「移过来」「置顶」
这类真的会挪窗口的操作）。屏幕外跑它，验的就不是那回事了。
"""

import importlib.machinery
import os
import runpy
import sys

OFFSCREEN = "+30000+30000"

# 这三块里哪一块会被加载，取决于平台（win32 走 winhost，别处走 nixhost），
# 上层的 host 再把它那几个名字抄一遍。三个都要盯。
_TARGETS = ("claude_tool.host", "claude_tool.winhost", "claude_tool.nixhost")


def _noop(*args, **kwargs):
    return None


def _patch_module(module):
    """把 place_window 换成空操作。没有这个名字的模块跳过。

    统一用同一个 `_noop` 对象（不是每个模块各造一个 lambda）：这样 host 那次
    「从 winhost 抄一遍」抄到的、和后来直接补上去的，是同一个东西，查起来好对。
    """
    if hasattr(module, "place_window"):
        module.place_window = _noop


class _PlaceWindowUnhooker:
    """`claude_tool.host` / `winhost` / `nixhost` 一加载完就把 place_window 抹掉。

    走 `sys.meta_path` 而不是 `import claude_tool.host`：后者会把 `paths` 提前
    冻在真实家目录上，探针的沙箱就废了（见文件头那段）。这里只在**探针自己**
    触发 import 的时候插手，顺序全由探针对。
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _TARGETS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _Loader(spec.loader)
        return spec


class _Loader:
    """套在真 loader 外面，加载完顺手打补丁。

    不动真 loader 自己的属性——不同的 loader 类有的带 __slots__，塞不进去。
    """

    def __init__(self, inner):
        self._inner = inner

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        _patch_module(module)


def _install():
    import tkinter as tk

    def off(self):
        self.geometry(OFFSCREEN)

    real_tk = tk.Tk.__init__

    def tk_init(self, *args, **kwargs):
        real_tk(self, *args, **kwargs)
        off(self)

    tk.Tk.__init__ = tk_init

    real_top = tk.Toplevel.__init__

    def top_init(self, *args, **kwargs):
        real_top(self, *args, **kwargs)
        off(self)

    tk.Toplevel.__init__ = top_init

    sys.meta_path.insert(0, _PlaceWindowUnhooker())


def main(argv):
    args = list(argv)
    onscreen = False
    if args and args[0] == "--onscreen":
        onscreen = True
        args.pop(0)
    if not args:
        print(__doc__)
        return 2
    target, rest = args[0], args[1:]
    if not os.path.isfile(target):
        print("找不到探针：{}".format(target))
        return 2
    if not onscreen:
        _install()
    sys.argv = [target] + rest
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    sys.exit(code)
