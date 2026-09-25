"""notice 区（「正在跑」/ 交接进度）收起来之后，整条高度必须跟着回收。

## 盯的是什么

用户报的一句话：「进行中的 AI 对话关闭之后，顶部会留白，不会恢复」。

成因不在业务逻辑，在 Tk 的 pack：子件 `pack_forget()` 之后，父框「该多大」还
停在**上一次布局算出来的**值上——父框的 reqheight 不会自己退回去。于是
`notice_area` 里最后一块（`running_frame` / `jobs_frame`）摘掉之后，这条带子仍然
占着 116 / 179 / 110 像素，在顶栏和正文之间留一条白，而且**再也收不回来**：
截图量出来 body 只有 682 高（正常 797），少的那 115 就是它。

修法在 `sessions.py` 的 `_notice_fit()`：子件摘干净之后显式
`configure(height=1)` 逼 Tk 重算；有货时 `configure(height=0)` 交还给子件自己顶。

这条很容易再犯——只要有人再写一个「先建好、有活了才 pack」的子块，忘了在收起
那条路上叫 `_notice_fit()`，留白就回来了。所以单独盯一条。

量的是 `winfo_height` / `winfo_reqheight` 和正文高度，全是离屏能拿到的数
（`_probe_run.py` 把窗口摆到 +30000；Canvas 控件和输入框在那种截图里不上色，
所以不靠看图）。

    python _probe_run.py _probe_notice_fit.py

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool。
"""
import os
import sys
import time

from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("notice_fit")
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.ui.launcher import Launcher                # noqa: E402

FAILED = []
# 初始（无会话无进度）时正文的高度。空着的那些状态都该回到这个数。
BASE = {}


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<46} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))
    return ok


class FakeProc:
    """假进程句柄：`_poll_running` 每秒 poll() 一遍，给个永远"还在跑"的。

    真给 None 的话那个心跳会抛 AttributeError——不致命，但日志里混一堆 traceback，
    看着像探针自己挂了。
    """

    def poll(self):
        return None


def item(name, path):
    return {"name": name, "path": path, "proc": FakeProc(), "hwnd": 0}


def soak(app, seconds=0.6):
    """跑几轮事件循环，让那次重排真的落到布局上。

    pack 的收放是排队等空闲的，`pack_forget()` 回来那一刻 `winfo_height` 还是旧
    值——不等这一下，量出来的永远是"上一帧"，这条探针就白写了。
    """
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.03)


def collapsed(app, label):
    """空状态的三条硬指标：高度回 1、reqh 回 1、正文拿回全部高度。"""
    check(label + " · notice 压回 1 像素",
          app.notice_area.winfo_height(), 1)
    check(label + " · reqh 也跟着退回去（这才是卡住的那个数）",
          app.notice_area.winfo_reqheight(), 1)
    check(label + " · 正文拿回被占的那几十像素",
          app.body.winfo_height(), BASE["body"])


def shown(app, label, want_min=40):
    h = app.notice_area.winfo_height()
    ok = h >= want_min
    if not ok:
        FAILED.append(label)
    print("{} {:<46} got={!r} want>={!r}".format(
        "ok  " if ok else "FAIL", label, h, want_min))
    return h


def main():
    app = Launcher()
    app.geometry("836x890")
    soak(app)
    # body 是 _build_ui 里的局部变量，探针从 side 的父控件倒推（跟 temp/running.py
    # 一样；哪天它变成属性了，用属性那份）。
    if not hasattr(app, "body"):
        app.body = app.side.master

    BASE["body"] = app.body.winfo_height()
    collapsed(app, "① 初始")

    # ── 「正在跑」那一路 ──
    app.running = [item("claude_tool", "D:/File/idea/python/claude_tool")]
    app._render_running()
    soak(app)
    one = shown(app, "② 开出一个会话")
    check("② 名单标记跟着置位", app._running_shown, True)

    app.running = []
    app._render_running()
    soak(app)
    collapsed(app, "③ 会话关掉之后")
    check("③ 名单标记跟着复位", app._running_shown, False)

    app.running = [item("a", "P"), item("b", "Q")]
    app._render_running()
    soak(app)
    two = shown(app, "④ 两个会话")
    check("④ 两个会话比一个高", two > one, True)
    app.running = []
    app._render_running()
    soak(app)
    collapsed(app, "⑤ 再关掉")

    # ── 交接进度那一路 ──
    app._show_task_area()
    soak(app)
    jobs = shown(app, "⑥ 进度区摆出来")
    app._hide_task_area()
    soak(app)
    collapsed(app, "⑦ 进度区收起来")

    # ── 现实里最常见的一串：会话开着，中途又点了换模型，两者叠着 ──
    app.running = [item("c", "R")]
    app._render_running()
    app._show_task_area()
    soak(app)
    both = shown(app, "⑧ 会话 + 进度 同时")
    check("⑧ 两块叠起来比单块高", both > jobs, True)

    app.running = []
    app._render_running()
    soak(app)
    # 进度还在，所以这时**不该**回 1，只该退到进度单独占的高度
    only_jobs = app.notice_area.winfo_height()
    check("⑨ 先关会话：只退掉「正在跑」那截，进度还留着",
          only_jobs, jobs)

    app._hide_task_area()
    soak(app)
    collapsed(app, "⑩ 再收进度")

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
