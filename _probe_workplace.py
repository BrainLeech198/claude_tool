"""『默认工作区』那行：看得见吗、改得动吗、改了算数吗。

0.4 起这一行搬进了设置窗「工作区」页（原来长在主窗「工作区」标题底下）。要读到
`workplace_var` 和那两颗按钮，得先把设置窗开起来切到那一页——页面是懒建的，
不开窗这些控件压根不存在（Task 8 那轮就是 AttributeError 红在这儿的）。

四件事：
1. 那行显示的就是配置里的 workplace，两个按钮没被长路径顶出框外。
2. 点「更改目录」选完就当场落盘（workplace 和 roots 一起动），标签跟着变。
3. 新建文件夹对话框里点「更改目录」也当场落盘；而且**接着点取消，改动仍然在**
   ——原先这条路上点取消是会把改动一起吞掉的，这次要验的就是它不再吞。
4. 建文件夹那条路仍然建在 workplace 底下。

不截图。USERPROFILE/HOME 先指到临时目录——paths.py 是 import 那一下就把
CLAUDE_DIR expanduser 出来的，所以必须赶在 import claude_tool 之前改。

    python -X utf8 _probe_workplace.py
"""
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk

SANDBOX = os.path.join(tempfile.gettempdir(), "wpprobe")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX)
os.environ["USERPROFILE"] = SANDBOX
os.environ["HOME"] = SANDBOX

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_tool import ui  # noqa: E402
from claude_tool.ui import launcher as L  # noqa: E402
from _probe_common import rebind  # noqa: E402

bad = []


def check(label, ok, detail=""):
    print("  {} {}{}".format("ok  " if ok else "FAIL", label,
                             "  " + str(detail) if detail else ""), flush=True)
    if not ok:
        bad.append(label)


class FakePicker:
    """替掉 filedialog：问它要目录，它直接报一个早先说好的路径。"""

    def __init__(self):
        self.answer = None
        self.asked = []

    def askdirectory(self, **kw):
        self.asked.append(kw)
        return self.answer


def walk(widget, out):
    out.append(widget)
    for child in widget.winfo_children():
        walk(child, out)
    return out


def find(root, kind, text=None):
    for w in walk(root, []):
        if isinstance(w, kind) and (text is None or getattr(w, "_text", None) == text):
            return w
    return None


def all_of(root, kind):
    return [w for w in walk(root, []) if isinstance(w, kind)]


def dialogs(app):
    """现在开着的对话框。

    得把设置窗摘出去：0.4 起它是个常驻的 Toplevel（关窗只是 withdraw，不销毁），
    第 3、4 节比的"对话框开没开、关没关"不能被它算进来。
    """
    return [w for w in app.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not app._settings_win]


def disk_cfg():
    with open(os.path.join(SANDBOX, ".claude_tool", "launcher.json"),
              encoding="utf-8") as f:
        return json.load(f)


picker = FakePicker()
# 0.4：pick_workplace 搬去了 ui/workspaces.py，它调的是那边自己那份 filedialog，
# `L.filedialog = picker` 改不到它。补丁落空不会报错，只会让**真的**文件选择框
# 当着用户的面弹出来——0.4 重构第一批就撞上过这一下，所以改用 rebind 全换掉。
rebind("filedialog", picker)

app = L.Launcher()
app.update()

# 0.4：这一行在设置窗「工作区」页里，先开窗切页。往后几节里 `app.workplace_var`
# 和那两颗按钮都靠着这一次开窗。
app.open_settings("工作区")
app.update()
page = app._settings_pages["工作区"]

print("== 1. 那行显示的是哪儿的路径 ==")
wanted = app.config_data["workplace"]
check("标签拿的是 workplace_var", app.workplace_var.get() == wanted, wanted)
rows = [w for w in walk(page, []) if isinstance(w, tk.Label)
        and w.cget("text") == "默认工作区"]
check("「默认工作区」这个标题在", len(rows) == 1, len(rows))
row = rows[0].master
for text in ("更改目录", "打开"):
    btn = find(row, L.PillButton, text)
    check("「{}」按钮在那一行里".format(text), btn is not None)
    if btn is not None:
        app.update_idletasks()
        check("「{}」没被路径顶出框外".format(text),
              btn.winfo_x() + btn.winfo_width() <= row.winfo_width(),
              "{} + {} vs {}".format(btn.winfo_x(), btn.winfo_width(),
                                     row.winfo_width()))

print()
print("== 2. 设置窗里改目录：选完就落盘 ==")
newbase = os.path.join(SANDBOX, "新工作区")
os.makedirs(newbase)
picker.answer = newbase
old = app.config_data["workplace"]
check("pick_workplace 报了「换了」", app.pick_workplace(app) is True)
check("内存里 workplace 变了", app.config_data["workplace"] == newbase,
      app.config_data["workplace"])
check("磁盘上 workplace 也变了", disk_cfg()["workplace"] == newbase,
      disk_cfg()["workplace"])
check("roots 里换成了新目录、老的不在了",
      disk_cfg()["roots"] == [newbase], disk_cfg()["roots"])
check("界面上那行跟着变了", app.workplace_var.get() == newbase,
      app.workplace_var.get())
check("没建任何多余的文件夹（改目录本身就只是改目录）",
      sorted(os.listdir(SANDBOX)) ==
      sorted(["新工作区", ".claude_tool"]), os.listdir(SANDBOX))

print()
print("== 3. 对话框里改目录：点取消也不该被吞 ==")
otherbase = os.path.join(SANDBOX, "另一个目录")
os.makedirs(otherbase)
picker.answer = otherbase
app._open_quick_workspace_dialog()
app.update()
dialog = dialogs(app)[-1]
btn = find(dialog, L.PillButton, "更改目录")
check("对话框里那个「更改目录」按钮在", btn is not None)
btn._command()
app.update()
check("点完就落盘了（没等「创建」）", disk_cfg()["workplace"] == otherbase,
      disk_cfg()["workplace"])
# 「建在」那行挂的是 textvariable，cget("text") 是空的，得顺着变量去问。
shown = []
for w in walk(dialog, []):
    if isinstance(w, tk.Label):
        var = str(w.cget("textvariable"))
        if var:
            shown.append(app.getvar(var))
check("对话框里那行「建在」也换成了新目录", otherbase in shown, shown)
cancel = find(dialog, L.PillButton, "取消")
cancel._command()
app.update()
check("接着点取消，改动还在（原先这儿会被吞掉）",
      disk_cfg()["workplace"] == otherbase, disk_cfg()["workplace"])

print()
print("== 4. 建文件夹还是建在 workplace 底下 ==")
picker.answer = otherbase
app._open_quick_workspace_dialog()
app.update()
dialog = dialogs(app)[-1]
entries = all_of(dialog, tk.Entry)
check("对话框里两个输入框都在", len(entries) >= 2, len(entries))
entries[0].insert(0, "探针子目录")
find(dialog, L.PillButton, "创建")._command()
app.update()
target = os.path.join(otherbase, "探针子目录")
check("文件夹建在了 workplace 底下", os.path.isdir(target), target)
check("顺手加进了工作区列表",
      any(w["path"] == target for w in app.config_data["workspaces"]),
      [w["path"] for w in app.config_data["workspaces"]])
check("建完对话框自己关了", not dialogs(app), dialogs(app))

app.destroy()
shutil.rmtree(SANDBOX, ignore_errors=True)
print()
print("RESULT " + ("FAIL " + " | ".join(bad) if bad else "OK"))
sys.exit(1 if bad else 0)
