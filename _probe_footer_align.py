"""量那排勾选框摆得齐不齐。

用户报的是"四个勾选框没有对齐"。原因是「自动继续 Stop hook」被 grid 到了第 1 列，
第 0 列空着——grid 里这一行就跟着第 0 列（别的行那条最长的标题）的宽度往后缩，
于是它的左边缘跟另外三个错开一格，看着像凭空缩进。

（0.4 把「自动查启动器新版」那个勾去掉了，Windows 上现在只剩**三个**，见下面
EXPECTED 那句。第 1 列已经没人占，但那条断言留着——防的是以后有人再往第 1 列
塞东西却不给第 0 列填。）

**0.4 起这排勾搬进了设置窗「行为开关」页**（原来平铺在主窗底栏）。要量它得先把
设置窗开起来、切到那一页——页面是懒建的，不开窗 `app.switches` 这个属性根本
不存在（Task 8 那轮就是 AttributeError 红在这儿的）。量的对象换了地方，但"左边缘
齐不齐、有没有左边空一格的行"这两条要盯的事一条没变。

要钉住两条：
  1. 第 0 列的勾左边缘全在同一个 x（都贴着左边缘）。
  2. 哪一行占了第 1 列，那一行的第 0 列就得有东西——不许留那种"左边空一格"的行。

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool（0.4 启动路径会落一次盘，
不沙箱就会把用户的 launcher.json 改掉）。
"""
import os
import shutil
import sys
import tkinter as tk

PROFILE = "D:/Desktop/tmp/footer_align"
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.host import EMBED_SUPPORTED                  # noqa: E402
from claude_tool.ui.launcher import Launcher                  # noqa: E402

# Windows 上多一个「内嵌终端 conhost」，别的平台没有那个勾。
EXPECTED = 3 if EMBED_SUPPORTED else 2

OK = [True]


def check(label, passed, detail=""):
    OK[0] = OK[0] and passed
    print("{} {}{}".format("OK  " if passed else "FAIL", label,
                           "  " + str(detail) if detail else ""))


def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(PROFILE, exist_ok=True)

    app = Launcher()
    app.update_idletasks()
    app.update()

    # 0.4：那排勾在设置窗里，先开窗再切页。
    app.open_settings("行为开关")
    app.update_idletasks()
    app.update()

    cells = {}
    for child in app.switches.winfo_children():
        if not isinstance(child, tk.Checkbutton):
            continue
        info = child.grid_info()
        cells[(int(info["row"]), int(info["column"]))] = child

    print("那排勾（行, 列 -> x, 标题）:")
    for (row, col) in sorted(cells):
        widget = cells[(row, col)]
        print("   row={} col={}  x={:5d}  {}".format(
            row, col, widget.winfo_x(), widget.cget("text")))
    print()

    check("勾都在（这台机器上）", len(cells) == EXPECTED,
          "{} 个（期望 {}）".format(len(cells), EXPECTED))

    left = [w.winfo_x() for (r, c), w in cells.items() if c == 0]
    check("第 0 列那几个的左边缘齐（同一个 x）", len(set(left)) == 1, sorted(set(left)))

    holes = sorted(r for (r, c) in cells if c == 1 and (r, 0) not in cells)
    check("没有「左边空一格」的行", not holes, holes)

    app.destroy()

    print()
    print("结果:", "全过" if OK[0] else "有失败")
    return 0 if OK[0] else 1


if __name__ == "__main__":
    sys.exit(main())
