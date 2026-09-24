"""左导航 + 右详情的版式验收（0.4 Task 6）。

这个版式的问题不是"崩没崩"，而是"摆得对不对"：左栏有没有真拿到它那份宽度、
右栏那两排按钮有没有被挤到换行、只有选中那一本才高亮。这些**量得出来**，而且
比肉眼看图可靠——离屏截图（_probe_run.py 把窗口摆到 +30000）里 Canvas 控件和
输入框根本不上色，光看图上是一片空白，什么都判断不了。

所以这里一律量 winfo_*：位置（winfo_x/y）、尺寸、映射状态、以及 Row.active。
图只是附带的记录，不作为判据。

    python _probe_navdet.py            # 走 _probe_run.py 后台跑

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool。
"""
import os
import shutil
import sys
import time
import tkinter as tk

PROFILE = "D:/Desktop/tmp/navdet"
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.handoff import HANDOFF_FILE                # noqa: E402
from claude_tool.ui.launcher import Launcher                # noqa: E402
from claude_tool.ui.nav import NAV_WIDTH                    # noqa: E402
from claude_tool.ui.settings import PAGES                   # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<40} got={!r} want={!r}".format("ok  " if ok else "FAIL",
                                                label, got, want))
    return ok


def soak(app, seconds=0.7):
    """跑几轮事件循环，让窗口那次 resize 真的落到布局上。"""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.03)


def ours(widget, cls_name):
    """这个 widget 底下（含自己）所有指定类名的控件，按屏幕位置排好序。"""
    out = []

    def walk(node):
        for child in node.winfo_children():
            if type(child).__name__ == cls_name:
                out.append(child)
            walk(child)

    walk(widget)
    out.sort(key=lambda w: (w.winfo_rooty(), w.winfo_rootx()))
    return out


def pill(widget, text):
    for found in ours(widget, "PillButton"):
        if found._text == text:
            return found
    return None


def rows_in(area):
    return [w for w in area.inner.winfo_children()
            if type(w).__name__ == "Row"]


def same_row(buttons):
    """这几个按钮是不是在同一行（y 相同、x 递增）。

    返回 (行数, 横向排布的对不对)。行数是 set(y) 的大小——被挤到换行就是 2。
    """
    tops = sorted({b.winfo_rooty() for b in buttons})
    ordered = [b.winfo_rootx() for b in buttons]
    return len(tops), ordered == sorted(ordered)


def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(PROFILE, exist_ok=True)

    # 两本工作区，其中甲有交接文档（「删交接文档」那颗按钮摆不摆就靠它）。
    home_a = os.path.join(PROFILE, "甲")
    home_b = os.path.join(PROFILE, "乙")
    for path in (home_a, home_b):
        os.makedirs(path, exist_ok=True)
    with open(os.path.join(home_a, HANDOFF_FILE), "w", encoding="utf-8") as f:
        f.write("# 交接文档\n")

    app = Launcher()
    app.config_data["workspaces"] = [{"name": "甲", "path": home_a},
                                     {"name": "乙", "path": home_b}]
    app.config_data.pop("selected_workspace", None)
    app.refresh_workspaces()

    def step():
        soak(app)

        # ── 左栏 ──
        check("左栏定宽 NAV_WIDTH", app.side.winfo_width(), NAV_WIDTH)
        check("导航区摆出来了", app.nav_area.winfo_ismapped(), True)
        check("导航区拿到那份宽度", app.nav_area.winfo_width() > NAV_WIDTH - 40,
              True)
        check("导航区有高度（不是被压成一条线）",
              app.nav_area.winfo_height() > 60, True)
        check("两本都画出来了", len(rows_in(app.nav_area)), 2)
        check("行没有动作按钮（动作在右栏）",
              all(not row.actions for row in rows_in(app.nav_area)), True)
        check("行上有 Ctrl 编号", [r.badge for r in rows_in(app.nav_area)],
              ["1", "2"])

        # 选中态：只有一本 active，而且是被选中那本
        check("选中在右栏生效前先按第一条",
              app.selected_path, home_a)
        check("只有选中那行高亮",
              [r.active for r in rows_in(app.nav_area)], [True, False])

        # 点第二行 = 选中第二本（行上的 on_click 就是这件事，不该开会话）
        rows_in(app.nav_area)[1].on_click()
        soak(app, 0.2)
        check("单击只换选中，不开会话", app.selected_path, home_b)
        check("高亮跟着挪",
              [r.active for r in rows_in(app.nav_area)], [False, True])
        check("右栏标题跟着换", app.detail_title_var.get(), "乙")

        # 点回第一本（有交接文档那本）
        rows_in(app.nav_area)[0].on_click()
        soak(app, 0.2)

        # ── 左栏底部那条 ──
        for caption in ("＋ 添加工作区", "重新扫描", "⚙ 设置"):
            button = pill(app.rail_footer, caption)
            check("左栏底部有「{}」".format(caption), button is not None, True)
            if button is not None:
                check("「{}」摆在可见处".format(caption),
                      button.winfo_ismapped(), True)

        # ── 右栏日常动作 ──
        daily = [pill(app.detail_area, t)
                 for t in ("新会话", "接着上次", "打开目录")]
        check("三个日常动作都在", all(b is not None for b in daily), True)
        if all(b is not None for b in daily):
            check("日常动作三个都在可见处",
                  all(b.winfo_ismapped() for b in daily), True)
            lines, ordered = same_row(daily)
            check("日常动作在同一行且从左到右", (lines, ordered), (1, True))

        # ── 右栏管理动作 ──
        manage = [pill(app.detail_area, t)
                  for t in ("改名", "搬迁", "↑", "↓", "移除")]
        check("五个管理动作都在", all(b is not None for b in manage), True)
        if all(b is not None for b in manage):
            lines, ordered = same_row(manage)
            check("管理动作在同一行且从左到右", (lines, ordered), (1, True))

        # 有交接文档才摆「删交接文档」
        handoff = pill(app.detail_area, "删交接文档")
        check("有交接文档时摆出「删交接文档」",
              handoff is not None and handoff.winfo_ismapped(), True)
        if handoff is not None and handoff.winfo_ismapped():
            check("它排在管理那一排最右边",
                  handoff.winfo_rootx() > max(b.winfo_rootx() for b in manage
                                              if b is not None), True)
        if all(b is not None for b in manage) and handoff is not None \
                and handoff.winfo_ismapped():
            lines, ordered = same_row(manage + [handoff])
            check("六个管理动作挤在一行（没换行）", (lines, ordered), (1, True))

        # 换到没有交接文档那本：那颗按钮该收回去
        app.select_workspace(home_b)
        soak(app, 0.3)
        check("没交接文档就不摆它", app._handoff_btn.winfo_ismapped(), False)

        # ── 窄屏：缩到下限，两排按钮还不换行 ──
        app.geometry("{}x{}".format(app.minsize()[0], app.minsize()[1]))
        soak(app, 0.8)
        print("   缩到下限 {}x{}".format(app.winfo_width(), app.winfo_height()))
        check("缩到下限后窗口就是下限宽",
              app.winfo_width(), app.minsize()[0])
        app.select_workspace(home_a)
        soak(app, 0.4)
        daily = [pill(app.detail_area, t)
                 for t in ("新会话", "接着上次", "打开目录")]
        manage = [pill(app.detail_area, t)
                  for t in ("改名", "搬迁", "↑", "↓", "移除")]
        handoff = pill(app.detail_area, "删交接文档")
        if all(b is not None for b in daily + manage):
            lines, ordered = same_row(daily)
            check("窄屏下日常动作还在一行", (lines, ordered), (1, True))
            group = manage + ([handoff] if handoff is not None
                              and handoff.winfo_ismapped() else [])
            lines, ordered = same_row(group)
            check("窄屏下管理动作还在一行", (lines, ordered), (1, True))
        check("窄屏下左栏没被挤扁", app.side.winfo_width(), NAV_WIDTH)
        check("窄屏下右栏还有地儿",
              app.detail_area.winfo_width() >= 300, True)

        # ── 设置窗 ──
        app.open_settings()
        soak(app, 0.8)
        win = app._settings_win
        check("设置窗开出来了", win is not None and win.winfo_exists(), True)
        check("三个分页都在", [name for name in PAGES],
              ["模型", "工作区", "行为开关"])
        check("默认停在模型页", app._settings_page, "模型")
        check("模型页有模型列表", app.model_list is not None, True)
        app.open_settings("工作区")
        soak(app, 0.4)
        check("工作区页有 AI 托管入口",
              pill(app._settings_pages["工作区"], "AI 托管") is not None, True)
        check("工作区页有默认工作区那行",
              pill(app._settings_pages["工作区"], "更改目录") is not None, True)
        app.open_settings("行为开关")
        soak(app, 0.4)
        boxes = [w for w in app.switches.winfo_children()
                 if isinstance(w, tk.Checkbutton)]
        check("行为开关页那几个勾还在（少一个自更新）", len(boxes), 3)
        check("那排勾的左边缘对齐",
              len({b.winfo_x() for b in boxes}), 1)

        # 关设置窗是收起来（不是销毁）：模型那页的测试还在跑时要能回填
        app._close_settings()
        soak(app, 0.2)
        check("关掉是收起来，不是销毁", win.winfo_exists(), True)
        check("收起来之后不再可见", win.winfo_ismapped(), False)
        app.open_settings("模型")
        soak(app, 0.4)
        check("再开回来还是那扇窗", app._settings_win is win, True)

        app.after(200, app.destroy)

    app.after(2600, step)
    app.mainloop()

    print()
    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
