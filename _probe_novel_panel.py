"""小说工作台插件：端到端接进右栏的验收（离屏量几何，不上屏）。

这条探针走的是**真实路径**——让宿主自己扫到内置的 `novel_assistant`、点启用、
加载、渲染，不是手工 `register_view` 糊个假的。盯三件事：

1. 选中**一本小说工程**：面板里真有书卡 / 章节（已发布那两章挂着「已发布·冻结」）
   / 人物 / 本轮观察词，而且排在宿主自己那排（「管理」）**下面**；
2. 选中**不是**小说工程的工作区：整块连「插件」那个标题一起收掉，不留空位；
3. 「按规范审查」按下去，开的是**这本书**的会话，提示词里带书名和「只审不改」。

    python _probe_run.py _probe_novel_panel.py

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool。
"""
import os
import shutil
import sys
import time
import tkinter as tk

from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("novelpanel")
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.ui.launcher import Launcher                  # noqa: E402

import _probe_novel_book as NB                                # noqa: E402

PLUGIN_ID = "novel_assistant"
PLUGIN_DIR = os.path.join(ROOT, "claude_tool", "plugins", PLUGIN_ID)

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<52} got={!r} want={!r}".format(
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
    return widget.winfo_rooty() - app.winfo_rooty()


def labels(root, text=None, contains=None):
    """插件区里文字等于 / 含某个串的 Label。"""
    out = []
    for child in walk(root):
        if not isinstance(child, tk.Label):
            continue
        try:
            value = child.cget("text")
        except tk.TclError:
            continue
        if text is not None and value == text:
            out.append(child)
        elif contains is not None and contains in value:
            out.append(child)
    return out


def button(root, text):
    """按可见文字找一颗胶囊按钮（`PillButton` 是 Canvas，文字不是 cget('text')）。"""
    for child in walk(root):
        if (type(child).__name__ == "PillButton"
                and getattr(child, "_text", "") == text):
            return child
    return None


def fresh_profile():
    """每一轮都从**干净的** profile 开始。

    不清的话，上一轮 `set_enabled` 写下的 `plugins.json` 会留着——插件下次开机就是
    "已启用"，于是头两条断言（"没启用时插件区是空的""首次见到是未启用"）**第二次跑
    就假红**。探针得能反复跑，这一条踩过一次。
    """
    if os.path.isdir(PROFILE):
        shutil.rmtree(PROFILE)


def prepare():
    """在**插件被加载之前**跑。正式跑是空操作。

    `temp/novel_panel_negative.py` 把它换掉：抢在插件 `exec` 之前改掉
    `novel_book.detect`（插件 `__init__` 的 `from novel_book import detect` 是那一刻
    绑定的），于是"非小说工程不出现"那条应当红。
    """


def main():
    fresh_profile()
    app = Launcher()
    app.geometry("836x920+30000+30000")
    soak(app, 0.8)

    root = NB.build_fixture()
    book = os.path.join(root, "甲书")
    area = app.detail_plugin_area

    check("没启用插件时插件区是空的", len(area.winfo_children()), 0)

    plugin = app.plugin_registry.get(PLUGIN_ID)
    check("内置的 novel_assistant 被扫到了", plugin is not None, True)
    check("门禁过了（清单没问题、这台宿主带得动）",
          bool(plugin and plugin.usable), True)
    check("首次见到是未启用（内置也不放松）",
          bool(plugin and plugin.enabled), False)

    prepare()
    app.plugin_registry.set_enabled(plugin, True)
    app._reload_plugins()
    soak(app)
    reloaded = app.plugin_registry.get(PLUGIN_ID)
    check("启用后加载没报错",
          reloaded.load_error if reloaded else "插件不见了", None)

    app.config_data["workspaces"] = [
        {"name": "甲书", "path": book},
        {"name": "随手建的", "path": os.path.join(root, "随手建的")},
        {"name": "作品集", "path": root},
    ]

    # ── 一本书 ──
    app.select_workspace(book)
    soak(app)
    check("一本书 · 书卡画出来了", len(labels(area, "《甲书》")), 1)
    check("一本书 · 章节行在", len(labels(area, contains="盘山道")), 1)
    check("一本书 · 已发布的两章挂着「已发布·冻结」",
          len(labels(area, "已发布·冻结")), 2)
    check("一本书 · 没发布的那章还在、只是没标记",
          len(labels(area, "003  错车")), 1)
    check("一本书 · 人物在", len(labels(area, "许默")), 1)
    check("一本书 · 本轮观察词在",
          len(labels(area, contains="过了一会儿／过了会儿")), 1)
    check("一本书 · 「插件」标题露出来了", len(labels(area, "插件")), 1)

    first = labels(area, contains="小说工作台")
    if first:
        check("一本书 · 面板压在「管理」那排下面",
              rel_y(app, first[0]) > rel_y(app, app.detail_manage_row), True)
    else:
        check("一本书 · 找到面板的第一行", False, True)

    # ── 不是小说工程 ──
    app.select_workspace(os.path.join(root, "随手建的"))
    soak(app)
    check("非小说工程 · 整块不出现（连「插件」标题一起收掉）",
          len(area.winfo_children()), 0)

    # ── 作品集根 ──
    app.select_workspace(root)
    soak(app)
    check("作品集根 · 报出几本", len(labels(area, "作品集 · 2 本")), 1)
    check("作品集根 · 书名列出来", len(labels(area, contains="《乙书》")), 1)

    # ── 「按规范审查」 ──
    app.select_workspace(book)
    soak(app)
    spawns = []
    app.plugin_host.open_claude = lambda path, **kw: spawns.append((path, kw))
    mark = button(area, "按规范审查")
    check("找到「按规范审查」", mark is not None, True)
    if mark is not None:
        mark._click()
    check("按下去开的是**这本书**的会话", [s[0] for s in spawns], [book])
    prompt = spawns[0][1].get("prompt", "") if spawns else ""
    check("提示词里带书名", "《甲书》" in prompt, True)
    check("提示词里写了「只审不改」", "只审不改" in prompt, True)

    app.destroy()

    print()
    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    sys.exit(code)
