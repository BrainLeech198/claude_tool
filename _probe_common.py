"""探针公用的小工具。

## 为什么需要它

0.4 那轮重构把 `claude_tool/ui/launcher.py` 按域拆成了几个 mixin 文件。拆之前
探针们习惯这么打猴补丁：

    import claude_tool.ui.launcher as L
    L.spawn_console = fake_spawn

拆之后就不灵了：方法挪到别的模块之后，它调的是**自己那个模块里**那份
（`from claude_tool.host import spawn_console` 在 import 那一刻复制过来的），
launcher 命名空间里那份跟它已经没有关系。`L.spawn_console = ...` 落了空——
**而且大多不会报错**，探针照跑照绿，实际什么都没验到。比报错麻烦得多。

`_probe_workplace` 是最显眼的一例：它 `L.filedialog = picker` 想替掉文件选择框，
代替品没装上，真的对话框当着用户的面弹出来了。

## 用法

    from _probe_common import rebind

    rebind("spawn_console", fake_spawn)     # 替掉所有 claude_tool.* 里那一份
    rebind("find_claude", lambda: None)

只认「本来就有这个名字」的模块，不会往无关模块上乱塞；一个落点都找不着时直接
抛错，不会静默变成空操作。要连 stdlib 那几份也一起替（比如 tkinter 的
`filedialog`），传 `prefix=""`——但那只在明确知道要连带替换时才用，默认的
`prefix="claude_tool"` 已经够 `_probe_workplace` 那种情况了。
"""

import os
import sys


# 探针沙箱的根目录。
#
# **别写回用户的桌面。** 以前每个探针各自硬写 "D:/Desktop/tmp/<自己的名字>"，跑一轮
# 全量回归就在桌面上摊出二三十个目录（用户抱怨过"你放的到处都是"），而且**只清不
# 治**——下次跑还长出来。现在统一指到仓库内的 temp/probes/sandboxes/（temp/ 已进
# .gitignore）。**谁都不许再往仓库外写沙箱。**
SANDBOX_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "temp", "probes", "sandboxes")


def sandbox(name):
    """第 name 号探针自己的沙箱目录。

    只算路径、**不建目录**——建不建由各探针自己决定（有的探针要验"目录不存在时
    怎么办"，替它建了就把被测的行为改掉了）。
    """
    return os.path.join(SANDBOX_ROOT, name)


def rebind(name, value, prefix="claude_tool"):
    """把某个全局名在所有 import 过它的模块里一起换掉，返回换过哪些模块。

    prefix 限定只动哪棵模块树。默认只动 claude_tool.*（我们的代码）；要连
    tkinter 里的 `filedialog` 那种 stdlib 名字一起换，传 prefix=""。
    """
    hits = []
    for module_name, module in list(sys.modules.items()):
        if module is None or not module_name.startswith(prefix):
            continue
        if not hasattr(module, name):
            continue
        setattr(module, name, value)
        hits.append(module_name)
    if not hits:
        raise AssertionError(
            "没有任何模块有 {!r} 这个名字——补丁没地方落，探针验的东西是假的"
            .format(name))
    return hits
