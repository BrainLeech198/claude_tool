"""量一下各部分要多大，用来定窗口下限。

    python _probe_width.py

**必须有沙箱**：它会真建一个 Launcher，而 0.4 的启动路径会落一次盘
（`_sync_selection` 把「当前工作区」写进 launcher.json）。不沙箱就直接落到用户
真实那份 `~/.claude_tool/launcher.json` 上了——Task 8 那轮真踩过：真实配置被加了
一个 `selected_workspace` 键，md5 前后对不上。

USERPROFILE/HOME 先指到临时目录——paths.py 是 import 那一下就把 CLAUDE_DIR
expanduser 出来的，所以必须赶在 import claude_tool 之前改。
"""
import os
import shutil
import sys
import tempfile

SANDBOX = os.path.join(tempfile.gettempdir(), "widthprobe")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX)
os.environ["USERPROFILE"] = SANDBOX
os.environ["HOME"] = SANDBOX

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.ui.launcher import Launcher                   # noqa: E402

app = Launcher()
app.update_idletasks()
app.update()          # 光 idletasks 的话 winfo_width 还是 1，看不出分配到的实际宽度

# 所有顶层孩子里，谁的自然宽度最大
rows = []
for child in app.winfo_children():
    rows.append((child.winfo_reqwidth(), child.winfo_reqheight(),
                 child.winfo_class()))

print("root  now      :", app.winfo_width(), "x", app.winfo_height())
print("root  req      :", app.winfo_reqwidth(), "x", app.winfo_reqheight())
print("minsize 现在   :", app.minsize())
print("顶层孩子自然尺寸 reqwidth x reqheight:")
for w, h, cls in sorted(rows, reverse=True):
    print("   {:5d} x {:4d}  {}".format(w, h, cls))

# 0.4：下限的基准换对象了。以前量的是底栏那排勾的自然宽度——那排勾整个搬进了
# 设置窗，主窗里没有这个对象可量了（`winfo_children()[-1]` 现在也不是底栏了，
# body 是最后建的那个）。现在量的是正文那两栏：左导航定宽 + 右栏详情那一排。
# `_apply_min_size` 就是拿这两个数算的（见 launcher.py）。
print()
print("左导航 side   req:", app.side.winfo_reqwidth(),
      " 实际:", app.side.winfo_width())
print("右详情 detail req:", app.detail_area.winfo_reqwidth())
print("右栏「管理」那排按钮的自然宽:")
for button in app._detail_manage:
    print("   {:6s} {:4d}".format(button._text, button.winfo_reqwidth()))
print("   （加上条件才摆的「删交接文档」: {}）".format(
    app._handoff_btn.winfo_reqwidth()))
manage_row = app._detail_manage[0].master
print("「管理」那一排（含前面的「管理」两字）:", manage_row.winfo_reqwidth())
print("正文 body     req:", app.winfo_children()[-1].winfo_reqwidth())
# 右栏每个孩子各要多少——`_apply_min_size` 用的**不是** detail_area 的自然宽度，
# 是 DETAIL_MIN_WIDTH 那个常数。把这一列打出来就能看出为什么：里面有跟着数据
# 变长的 Label（路径、meta 那行），拿它当下限会随工作区名的长短飘。下限要的是
# 「管理」那一排的宽度，那是个不随数据变的数。
print("右详情各孩子 reqwidth:")
for child in app.detail_area.winfo_children():
    print("   {:5d}  {}".format(child.winfo_reqwidth(), child.winfo_class()))

app.destroy()
shutil.rmtree(SANDBOX, ignore_errors=True)
