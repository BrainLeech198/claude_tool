"""插件面板接进右栏的验收（离屏量几何，不上屏）。

`register_view` 是这一轮新加的能力：插件不只是能加一颗按钮，还能往右栏详情区画
**一整块界面**。这条探针盯四件事：

1. 注册了面板之后，`detail_plugin_area` 里真的出现插件画的东西；
2. 面板压在宿主自己那排（「管理」）**下面**，没插队；
3. 换一本工作区，面板跟着重画，`build` 拿到的 entry 是**新**那条；
4. 撤掉之后那块**收干净**，不留空位。

    python _probe_run.py _probe_plugin_view.py

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool。
"""
import os
import sys
import time
import tkinter as tk

from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("pluginview")
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.theme import PAGE_BG                        # noqa: E402
from claude_tool.ui.launcher import Launcher                  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<46} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))
    return ok


def soak(app, seconds=0.5):
    """跑几轮事件循环，让刚摆上去的东西真的落进布局。"""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.03)


def walk(node):
    for child in node.winfo_children():
        yield child
        for deeper in walk(child):
            yield deeper


def rel_y(app, widget):
    """相对窗口顶边的 y。跨父控件比高低只能这么量（见记忆里那条）。"""
    return widget.winfo_rooty() - app.winfo_rooty()


def by_text(root, text):
    for child in walk(root):
        if isinstance(child, tk.Label):
            try:
                if child.cget("text") == text:
                    return child
            except tk.TclError:
                continue
    return None


def main():
    app = Launcher()
    app.geometry("836x760+30000+30000")
    soak(app, 0.8)

    area = app.detail_plugin_area
    check("没插件时插件区是空的", len(area.winfo_children()), 0)

    # 铺两条假工作区（path 是假的没关系，右栏对不存在的目录是照实显示"目录不存在"）
    app.config_data["workspaces"] = [
        {"name": "甲本", "path": "D:/假路径/甲"},
        {"name": "乙本", "path": "D:/假路径/乙"},
    ]
    app.select_workspace("D:/假路径/甲")
    soak(app)

    seen = []

    def build(parent, entry):
        seen.append(entry)
        tk.Label(parent, text="插件画的记号", bg=PAGE_BG).pack(anchor="w")

    undo = app.plugin_host.register_view("workspace_detail", build)
    soak(app)

    check("注册后面板画了一次", len(seen), 1)
    check("build 拿到的是当前选中那条",
          (seen[-1] if seen else {}).get("name"), "甲本")
    check("插件区里多出了控件", len(area.winfo_children()) > 0, True)

    mark = by_text(area, "插件画的记号")
    check("面板真摆在插件区里", mark is not None, True)
    if mark is not None:
        check("面板压在管理那排下面",
              rel_y(app, mark) > rel_y(app, app.detail_manage_row), True)

    # 换一本：面板得跟着重画，build 拿到的要是新的那条
    app.select_workspace("D:/假路径/乙")
    soak(app)
    check("换一本之后又画了一次", len(seen), 2)
    check("第二次拿到的就是乙本", (seen[-1] if seen else {}).get("name"), "乙本")
    check("换完之后面板还在", by_text(area, "插件画的记号") is not None, True)

    # 撤掉：那块要收干净
    undo()
    soak(app)
    check("撤掉之后插件区空了", len(area.winfo_children()), 0)
    check("记号也没了", by_text(area, "插件画的记号"), None)

    app.destroy()

    print()
    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
