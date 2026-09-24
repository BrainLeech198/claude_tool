"""主窗各功能区的对齐验收（0.4 收尾）。

这个版式的问题不是"崩没崩"，而是"各块摆齐了没有"：

1. **页面空档得一致**。顶栏那条线、正文两栏、状态行文字、没装 claude 时那条告警，
   左右边缘该落在同一条竖线上。0.4 收尾前是这样的：顶栏和状态行用 20、正文用 14
   （图省事复用了 SIDE_GAP）、告警区又是 16——左导航那栏比顶栏和状态行的字凸出去
   6 像素，右栏内容又比顶栏那颗按钮凸出去 6 像素。现在都指 theme.PAGE_GUTTER。

2. **右栏几块得各就各位**。0.4 收尾前右栏是乱的：三颗日常动作按钮直接
   `pack(side="left")` 进了右栏本身（右栏别的孩子都是默认 side="top"），它们各抢
   一条"剩余区域的左边缘"、又没写 fill="y" 就纵向居中——结果那排飘到整栏中间
   （实测 y=553、右栏总共 857 高），后面那些 top 的框（分隔线、「管理」那排、插件
   区）全被挤到右半栏、宽度只剩 339（本该是 616）。「管理」那排最宽状态要 441，
   339 摆不下，一有交接文档那颗「删交接文档」就被裁掉。

这些**量得出来**，而且比肉眼看图可靠——离屏截图（_probe_run.py 把窗口摆到
+30000）里 Canvas 控件和输入框根本不上色，光看图上是一片空白。

所以一律量 winfo_*，全部换算成"相对窗口左上角"的坐标再比。

    python _probe_run.py _probe_align.py

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool。
"""
import os
import sys
import time
import tkinter as tk

PROFILE = "D:/Desktop/tmp/align"
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.handoff import HANDOFF_FILE                # noqa: E402
from claude_tool.theme import PAGE_GUTTER                   # noqa: E402
from claude_tool.ui.detail import DETAIL_MIN_WIDTH          # noqa: E402
from claude_tool.ui.launcher import Launcher, PAGE_PAD      # noqa: E402
from claude_tool.ui.nav import NAV_WIDTH                    # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<46} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))
    return ok


def soak(app, seconds=0.7):
    """跑几轮事件循环，让窗口那次 resize 真的落到布局上。"""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.03)


def rel(app, widget):
    """相对窗口左上角的 (左, 上, 右, 下)。"""
    x = widget.winfo_rootx() - app.winfo_rootx()
    y = widget.winfo_rooty() - app.winfo_rooty()
    return x, y, x + widget.winfo_width(), y + widget.winfo_height()


def walk(node):
    for child in node.winfo_children():
        yield child
        for deeper in walk(child):
            yield deeper


def by_text(root, text):
    """整棵树里第一件字面**正好**写着 text 的控件（Label 或按钮）。

    必须精确匹配、不能用包含：左栏那颗「＋ 添加工作区」里含"工作区"，包含匹配
    会把它当成"工作区"标题交出去，量出来的位置整个是错的。
    """
    for child in walk(root):
        if type(child).__name__ == "PillButton":
            if child._text == text:
                return child
            continue
        if not isinstance(child, tk.Label):
            continue
        try:
            if child.cget("text") == text:
                return child
        except tk.TclError:
            continue
    return None


def by_var(root, variable):
    """整棵树里第一件 textvariable 指着 variable 的控件。

    注意 `cget("textvariable")` 回来的是个 `parsedVarName` 对象、不是字符串，跟
    `str(variable)` 直接比永远不相等——得先 `str()` 一下。
    """
    name = str(variable)
    for child in walk(root):
        try:
            if str(child.cget("textvariable")) == name:
                return child
        except tk.TclError:
            continue
    return None


def dump(app):
    """把各块的位置打成一张表。人看这张表比看断言名快。"""
    body = app.side.master
    rows = []
    for name, widget in (
            ("顶栏 line", app.top_line),
            ("顶栏按钮 打开配置目录", by_text(app.top_line, "打开配置目录")),
            ("notice_area", app.notice_area),
            ("状态行 文字", by_var(app, app.feedback_var)),
            ("body", body),
            ("左导航 side", app.side),
            ("左栏 工作区标题", by_text(app.side, "工作区")),
            ("左栏 rail_footer", app.rail_footer),
            ("右栏 detail_area", app.detail_area),
            ("右栏 当前模型", by_text(app.detail_area, "当前模型")),
            ("右栏 详情标题", by_var(app, app.detail_title_var)),
            ("右栏 日常动作那排", app.detail_daily_row),
            ("右栏 管理那排", app.detail_manage_row),
            ("右栏 插件区", app.detail_plugin_area)):
        if widget is None:
            print("（没找到：{}）".format(name))
            continue
        rows.append((name, *rel(app, widget)))

    width, height = app.winfo_width(), app.winfo_height()
    print()
    print("{:<26}{:>6}{:>6}{:>8}{:>8}{:>8}{:>8}".format(
        "区块", "左", "上", "右", "下", "距右缘", "距下缘"))
    for name, left, top, right, bottom in rows:
        print("{:<26}{:>6}{:>6}{:>8}{:>8}{:>8}{:>8}".format(
            name, left, top, right, bottom, width - right, height - bottom))
    print()
    return rows


def check_gutters(app, body):
    """第 1 题：页面四条边那道空档，各处是不是同一条线。"""
    width = app.winfo_width()
    body_left, _, body_right, _ = rel(app, body)
    check("正文左缘 = 页面空档", body_left, PAGE_GUTTER)
    check("正文左右空档一样", body_left, width - body_right)
    check("顶栏左缘跟正文对齐", rel(app, app.top_line)[0], body_left)
    check("顶栏右缘跟正文对齐", rel(app, app.top_line)[2], body_right)
    check("状态行左缘跟正文对齐",
          rel(app, by_var(app, app.feedback_var))[0], body_left)
    check("notice 左缘跟正文对齐", rel(app, app.notice_area)[0], body_left)


def check_detail_stack(app):
    """第 2 题：右栏那几块各就各位没有。

    「管理」那排和插件区都是 fill="x" 直接挂在右栏下的，就该跟右栏一样宽、一样
    左对齐；日常动作那排在自己的行框里保持原样，位置该在详情标题之下、「管理」
    那排之上。0.4 收尾前它们全错位（见文件头第 2 条）。
    """
    detail_left, _, detail_right, _ = rel(app, app.detail_area)
    detail_width = detail_right - detail_left

    for name, frame in (("日常动作那排", app.detail_daily_row),
                        ("管理那排", app.detail_manage_row),
                        ("插件区", app.detail_plugin_area)):
        left, _, right, _ = rel(app, frame)
        check("{} 左缘跟右栏对齐".format(name), left, detail_left)
        check("{} 撑满右栏宽".format(name), right - left, detail_width)

    # 纵向顺序：详情标题 → 日常动作 → 管理 → 插件区
    tops = [rel(app, w)[1] for w in (
        by_var(app, app.detail_title_var),
        app.detail_daily_row,
        app.detail_manage_row,
        app.detail_plugin_area)]
    check("右栏四块自上而下的顺序对",
          all(a < b for a, b in zip(tops, tops[1:])), True)

    # 那排最宽状态（含删交接文档）得真摆得下，别被裁掉
    row = app.detail_manage_row
    check("「管理」那排摆得下最宽状态",
          row.winfo_width() >= row.winfo_reqwidth(), True)
    check("而且余量 ≥ 20 像素（不是刚好卡住）",
          row.winfo_width() - row.winfo_reqwidth() >= 20, True)
    check("「删交接文档」确实摆着（最宽状态）",
          app._handoff_btn.winfo_manager(), "pack")


def main():
    app = Launcher()
    soak(app)
    print("窗口 {}x{}  正文两栏 {} + {}  下限 {}".format(
        app.winfo_width(), app.winfo_height(), NAV_WIDTH, DETAIL_MIN_WIDTH,
        app.minsize()))
    print("沙箱家目录 =", os.environ.get("USERPROFILE"))

    # 造一本**带交接文档**的工作区并选中它：「管理」那排的最宽状态要有，第 2 节
    # 里"摆得下最宽状态"那条才量得着东西。
    ws = os.path.join(PROFILE, "带交接")
    os.makedirs(ws, exist_ok=True)
    with open(os.path.join(ws, HANDOFF_FILE), "w", encoding="utf-8") as f:
        f.write("# 交接\n")
    app.config_data["workspaces"] = [{"name": "带交接", "path": ws,
                                      "permission": "acceptEdits",
                                      "handoff_ignore_git": False}]
    app.refresh_workspaces()
    app.select_workspace(ws)
    soak(app, 0.5)

    body = app.side.master
    print("\n== 默认窗口尺寸下 ==")
    dump(app)
    check_gutters(app, body)
    check_detail_stack(app)

    print("\n== 缩到下限（{}x{}）==".format(*app.minsize()))
    app.geometry("{}x{}".format(*app.minsize()))
    soak(app, 0.8)
    check("窗口真缩到下限宽", app.winfo_width(), app.minsize()[0])
    dump(app)
    check_gutters(app, body)
    check_detail_stack(app)
    check("缩到下限左导航没被挤扁", app.side.winfo_width(), NAV_WIDTH)
    # 两栏之间那道缝（PAGE_PAD）就是右栏左缘到左栏右缘的距离
    check("两栏之间那道缝是 PAGE_PAD",
          rel(app, app.detail_area)[0] - rel(app, app.side)[2], PAGE_PAD)
    app.destroy()

    print()
    if FAILED:
        print("挂了 {} 条：{}".format(len(FAILED), "、".join(FAILED)))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    sys.exit(code)
