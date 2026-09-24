"""量主窗口开多大、各块分到多少，并出图。

    python _probe_size_dump.py <出图前缀>

沙箱 USERPROFILE（借 _probe_ui_shot 那份），绝不碰用户真实的 ~/.claude。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _probe_ui_shot as S                                  # noqa: E402
from claude_tool.ui.launcher import Launcher, default_window_size   # noqa: E402

PREFIX = sys.argv[1] if len(sys.argv) > 1 else "_dump"
FAILED = []


def pump(app, seconds):
    """跑几轮事件循环，让窗口那一次 resize 真的落到布局上。

    只 update_idletasks() 不够：它会重算布局，但窗口自己那次 resize 还在队列里，
    量出来的子控件高度是上一轮的。
    """
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.03)


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<36} got={!r} want={!r}".format("ok  " if ok else "FAIL",
                                                label, got, want))


def dump(app, tag):
    app.update_idletasks()
    print("== {} ==".format(tag))
    print("   窗口 {}x{}   下限 {}".format(
        app.winfo_width(), app.winfo_height(), app.minsize()))
    # 0.4：原来这儿量的是「模型区 / 工作区 / 底部开关」三块。那三块都搬走了——
    # 模型区和那排开关进了设置窗（那是另一个 Toplevel，尺寸不参与主窗下限），
    # 工作区列表变成左导航。所以改量现在这三块。
    print("   左导航 {} / 右详情 {} / 通知带 {}".format(
        app.side.winfo_width(), app.detail_area.winfo_width(),
        app.notice_area.winfo_height()))


def main():
    app = Launcher()
    pid = os.getpid()
    screen_w, screen_h = app.winfo_screenwidth(), app.winfo_screenheight()
    print("屏幕 {}x{}".format(screen_w, screen_h))
    width, height = default_window_size(screen_w, screen_h)
    check("默认尺寸写死 900x950（屏幕装不下才收）", (width, height),
          (min(900, screen_w - 20), min(950, screen_h - 100)))

    def step():
        pump(app, 0.6)
        dump(app, "默认（没存过尺寸）")
        check("没存过就按默认开", (app.winfo_width(), app.winfo_height()),
              (width, height))
        check("高度下限抬到 660", app.minsize()[1], 660)
        S.grab(pid, PREFIX + "_default.png", "Claude 启动器")

        # 存了个比默认矮的：用户手调的高度就是他的偏好，照记（只守 minsize 那道
        # 下限，见 _restore_geometry 的说明——早先"不比默认矮就抬回默认"那版是错的）
        app.config_data["window"] = {"x": 120, "y": 90, "w": 848, "h": 700}
        app._restore_geometry()
        pump(app, 0.6)
        dump(app, "存成 848x700")
        check("矮的照记（不抬回默认）", app.winfo_height(), 700)
        check("宽度照记", app.winfo_width(), 848)
        S.grab(pid, PREFIX + "_700.png", "Claude 启动器")

        # 存了个更高的：照记
        app.config_data["window"] = {"x": 120, "y": 90, "w": 900, "h": 1400}
        app._restore_geometry()
        pump(app, 0.6)
        dump(app, "存成 900x1400")
        check("比默认高就照记", app.winfo_height(), 1400)
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
