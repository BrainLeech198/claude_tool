"""验「AI 托管」这个入口，以及顺手把工作区那一块理干净的那几处改动。

会真开窗口、真写配置，所以沙箱在脚本里自己设（不靠调用方记得带环境变量——
漏一次就把用户真实那份 ~/.claude_tool/launcher.json 和 hooks/hook.json 写脏，
写脏过一次）。里面把 claude_tool 真正拉 claude 的那一下换成了假的：只记参数、
不开窗口。

    python _probe_autonomy.py
"""
import json
import os
import shutil
import sys
import tkinter as tk

# 必须在 import claude_tool.paths 之前：TOOL_DIR 是导入时算出来的。
PROFILE = "D:/Desktop/tmp/autonomy"
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from _probe_common import rebind                       # noqa: E402

import claude_tool.ui.launcher as L
from claude_tool.config import CONFIG_FILE
from claude_tool.handoff import HOOK_SETTINGS
from claude_tool.paths import PRESET_DIR, TOOL_DIR
from claude_tool.permissions import workspace_permission
from claude_tool.presets import preset_path
from claude_tool.ui.launcher import Launcher

PASS, FAIL = [], []


def check(label, ok, extra=""):
    (PASS if ok else FAIL).append(label)
    print("  [{}] {}{}".format("ok" if ok else "!!", label,
                               ("  —— " + str(extra)) if extra else ""))


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def load():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def toplevels():
    return [w for w in root_children() if isinstance(w, tk.Toplevel)]


def root_children():
    return app.winfo_children()


def texts(widget):
    try:
        return str(widget.cget("text"))
    except Exception:
        # PillButton 是 Canvas，没有 text 选项，文字存在自己的属性里
        return str(getattr(widget, "_text", ""))


def find_toplevel(title):
    for child in app.winfo_children():
        if isinstance(child, tk.Toplevel) and child.title() == title:
            return child
    return None


def pillows(parent):
    return [w for w in walk(parent) if isinstance(w, L.PillButton)]


def click(parent, caption):
    for button in pillows(parent):
        if button._text == caption:
            button._command()
            return True
    return False


# ── 沙箱 ──────────────────────────────────────────────────────────────────
sandbox = os.path.expanduser("~")
shutil.rmtree(TOOL_DIR, ignore_errors=True)
print("沙箱:", sandbox)
print()

# 摆一个预设进来：第 2 档得有指挥模型才走得通，一个预设都没有就验不了它。
os.makedirs(PRESET_DIR, exist_ok=True)
DEPUTY = "沙箱模型"
with open(preset_path(DEPUTY), "w", encoding="utf-8") as f:
    json.dump({"env": {"ANTHROPIC_BASE_URL": "https://api.example.com",
                       "ANTHROPIC_MODEL": "test-model",
                       "ANTHROPIC_AUTH_TOKEN": "x"}}, f)

captured = {}


def fake_launch(path, cont, prompt, settings, permission, beside=None):
    captured.update({"path": path, "cont": cont, "prompt": prompt,
                     "settings": settings, "permission": permission})
    return type("P", (), {"poll": lambda self: None})()


rebind("terminal_windows", lambda: [])
rebind("spawn_terminal", fake_launch)

app = Launcher()
app.update()
app.track_running = lambda *a, **k: None
app._watch_terminal = lambda *a, **k: None


print("== 1. 「AI 托管」按钮摆在主窗口上、工作区列表上面 ==")
buttons = pillows(app)
托管 = next((b for b in buttons if b._text == "AI 托管"), None)
check("主窗口上有「AI 托管」按钮", 托管 is not None)
if 托管 is not None:
    holder = 托管.master          # 那行 Frame
    check("它挂在工作区那一列里（不是藏在哪个对话框里）",
          holder.master is app.side)
    check("它排在「工作区」标题下面",
          holder.winfo_rooty() >= app.ws_list.head.winfo_rooty(),
          (holder.winfo_rooty(), app.ws_list.head.winfo_rooty()))
    check("它排在筛选框上面",
          holder.winfo_rooty() + holder.winfo_height()
          <= app.filter_entry.winfo_rooty() + 4,
          (holder.winfo_rooty() + holder.winfo_height(),
           app.filter_entry.winfo_rooty()))
    hints = [texts(w) for w in holder.winfo_children()]
    check("旁边带着一句说明", any("交给它自己跑" in t for t in hints), hints)

print()
print("== 2. 工作区标题那排按钮 ==")
head = app.ws_list.head
labels = [texts(w) for w in walk(head) if isinstance(w, L.PillButton)]
check("并成了「＋ 添加工作区」", "＋ 添加工作区" in labels, labels)
check("不再有「＋ 新建文件夹」", "＋ 新建文件夹" not in labels)
check("不再有「＋ 选已有目录」", "＋ 选已有目录" not in labels)
check("「重新扫描」还在", "重新扫描" in labels, labels)
check("那排只剩两个按钮", len(labels) == 2, labels)

print()
print("== 3. 那行重复的「新建文件夹建在 … [更改目录]」撤掉了 ==")
stale = [texts(w) for w in walk(app) if "新建文件夹建在" in texts(w)]
check("界面上找不到那句话了", not stale, stale)
check("workplace_label 这个属性也没了", not hasattr(app, "workplace_label"))
check("_build_workplace_note 没了", not hasattr(app, "_build_workplace_note"))
check("_refresh_workplace_note 没了", not hasattr(app, "_refresh_workplace_note"))
check("_open_workplace_dialog 没了", not hasattr(app, "_open_workplace_dialog"))

print()
print("== 4. 窗口下限没被这一行顶上去 ==")
app.update_idletasks()
check("minsize 宽度还是老样子（<= 760）", app.minsize()[0] <= 760,
      app.minsize())

print()
print("== 5. 「＋ 添加工作区」先问一句 ==")
click(head, "＋ 添加工作区")
app.update()
picker = find_toplevel("添加工作区")
check("弹出的是「添加工作区」", picker is not None)
if picker is not None:
    captions = [b._text for b in pillows(picker)]
    check("两个选项都在", "新建一个文件夹" in captions and "选已有目录" in captions,
          captions)
    check("第一个是新按钮（主按钮）",
          next(b for b in pillows(picker) if b._text == "新建一个文件夹")._primary)
    picker.destroy()
app.update()

# 让第二个工作区成为「上次托管用的那个」，验记忆
workspaces = app.config_data["workspaces"]
if len(workspaces) < 2:
    extra = os.path.join(app.config_data["workplace"], "二号")
    os.makedirs(extra, exist_ok=True)
    app.config_data["workspaces"].append({"name": "二号", "path": extra})
    workspaces = app.config_data["workspaces"]
remembered = workspaces[1]

print()
print("== 6. 托管对话框：记得上次用的工作区和上次选的档 ==")
app.config_data["autonomy_workspace"] = remembered["path"]
app.config_data["autonomy"] = 1
app._open_autonomy_dialog()
app.update()
dialog = find_toplevel("AI 托管")
check("弹出的是「AI 托管」", dialog is not None)
combo = next((w for w in walk(dialog) if w.winfo_class() == "TCombobox"), None)
check("有工作区下拉", combo is not None)
if combo is not None:
    check("预选的是上次那个工作区", combo.get() == remembered["name"],
          combo.get())

paths = [texts(w) for w in walk(dialog) if isinstance(w, tk.Label)]
check("把选中的路径摆出来了", any(remembered["path"] in t for t in paths))
expect_mode = workspace_permission(app.config_data["workspaces"][1])
check("把这次的权限等级也摆出来了",
      any("权限" in t and expect_mode in t for t in paths),
      [t[:60] for t in paths if "权限" in t])
check("说清了权限确认那种停顿 hook 接不了",
      any("接不了" in t for t in paths))

radios = [w for w in walk(dialog) if w.winfo_class() == "Radiobutton"]
check("三档都列出来了", len(radios) == 3, len(radios))
if len(radios) == 3:
    check("三档都能点（第 2、3 档落地了）",
          all(str(r.cget("state")) == "normal" for r in radios),
          [str(r.cget("state")) for r in radios])
    check("三档都写着「第 N 档」", all("第 {} 档".format(i + 1) in texts(r)
                                       for i, r in enumerate(radios)),
          [texts(r)[:8] for r in radios])
    check("没有哪一档还标着「还没做」",
          not any("还没做" in texts(r) for r in radios),
          [texts(r) for r in radios])

    # 指挥模型那一行只有第 2 档该露出来。工作区那个下拉也是 TCombobox，
    # 按 walk 的顺序它在前面，指挥模型这个是第二个。
    combos = [w for w in walk(dialog) if w.winfo_class() == "TCombobox"]
    check("下拉有两个（工作区、指挥模型）", len(combos) == 2, len(combos))
    deputy = combos[1] if len(combos) > 1 else None
    check("停在第 1 档时指挥模型那行藏着",
          deputy is not None and not deputy.winfo_ismapped())
    radios[1].invoke()
    app.update()
    check("点第 2 档才露出来", deputy is not None and deputy.winfo_ismapped())
    check("下拉里是沙箱那个预设",
          deputy is not None and list(deputy.cget("values")) == [DEPUTY],
          deputy.cget("values") if deputy is not None else None)
    check("底下写清了这个预设是哪家的模型",
          any("test-model" in t for t in (texts(w) for w in walk(dialog))))
    radios[2].invoke()
    app.update()
    check("点回第 3 档，那行又藏回去",
          deputy is not None and not deputy.winfo_ismapped())
    # 收尾落回第 1 档：后面要验「按选的那一档记下来」，得是干净的一档。
    radios[0].invoke()
    app.update()

goal = next((w for w in walk(dialog) if w.winfo_class() == "Text"), None)
check("任务目标是个多行框", goal is not None)
if goal is not None:
    # 任务空着点下去得被拦住。它会弹一个模态错误框，那个框会把这个探针卡死，
    # 所以先把 showerror 换成记账的。
    import claude_tool.ui.dialogs as D
    real_showerror = D.messagebox.showerror
    complained = []
    D.messagebox.showerror = lambda *a, **k: complained.append(a)
    click(dialog, "开始托管")
    D.messagebox.showerror = real_showerror
    app.update()
    check("任务空着不给开", bool(complained), complained)
    check("空着点它，托管对话框还开着", find_toplevel("AI 托管") is not None)
    check("空着点它也没真去开会话", not captured)

    goal.insert("1.0", "把 README 里的错别字改一遍，改完告诉我改了哪些。")
    app.update()
    ok = click(dialog, "开始托管")
    app.update()
    check("填了任务就能开", ok)
    check("对话框自己关了", find_toplevel("AI 托管") is None)
    check("走的是新会话", captured.get("cont") is False, captured.get("cont"))
    check("任务目标原样当开场白传下去",
          captured.get("prompt") == "把 README 里的错别字改一遍，改完告诉我改了哪些。",
          repr(captured.get("prompt")))
    check("开在挑的那个工作区", captured.get("path") == remembered["path"])
    check("挂上了 hook 的 settings", captured.get("settings") is not None)

    print()
    print("== 7. 挂上去的那份 hook 配置 ==")
    settings = captured.get("settings")
    if settings:
        with open(settings, encoding="utf-8") as f:
            payload = json.load(f)
        command = payload["hooks"]["Stop"][0]["hooks"][0]["command"]
        check("用的是 --hook 那个入口", "--hook" in command, command)
        check("带上了 --auto-continue", "--auto-continue" in command, command)
        check("写的就是那个共用的文件", settings == HOOK_SETTINGS, settings)

print()
print("== 8. 记忆落盘了 ==")
saved = load()
check("autonomy = 1", saved.get("autonomy") == 1, saved.get("autonomy"))
check("autonomy_workspace 记的是那个工作区",
      saved.get("autonomy_workspace") == remembered["path"],
      saved.get("autonomy_workspace"))

print()
print("== 9. 再开一次：直接停在原处 ==")
app.destroy()
app = Launcher()
app.update()
app.track_running = lambda *a, **k: None
app._watch_terminal = lambda *a, **k: None
app._open_autonomy_dialog()
app.update()
dialog = find_toplevel("AI 托管")
combo = next((w for w in walk(dialog) if w.winfo_class() == "TCombobox"), None)
check("还是停在上次那个工作区", combo is not None and combo.get() == remembered["name"],
      combo.get() if combo else None)
dialog.destroy()
app.update()

print()
print("== 10. autopilot 只影响这一次，不动底下那个勾 ==")
app.auto_continue_var.set(False)
item = app.config_data["workspaces"][0]
captured.clear()
app.launch_workspace(item, cont=False, prompt="干活", autopilot=True)
with open(captured["settings"], encoding="utf-8") as f:
    command = json.load(f)["hooks"]["Stop"][0]["hooks"][0]["command"]
check("勾关着，托管这次也挂上了自动继续", "--auto-continue" in command, command)
check("底下那个勾本身没被改动", app.auto_continue_var.get() is False)
check("配置文件里也没被改动", load().get("auto_continue") is False)

app.auto_continue_var.set(True)
captured.clear()
app.launch_workspace(item, cont=False, prompt="干活")
with open(captured["settings"], encoding="utf-8") as f:
    command = json.load(f)["hooks"]["Stop"][0]["hooks"][0]["command"]
check("不开托管时照旧跟着勾走", "--auto-continue" in command, command)

print()
print("== 11. 档位写坏了回落第 1 档 ==")
app.destroy()
with open(CONFIG_FILE, "r+", encoding="utf-8") as f:
    raw = json.load(f)
    raw["autonomy"] = 9
    raw["autonomy_workspace"] = 12345
    f.seek(0)
    f.truncate()
    json.dump(raw, f, ensure_ascii=False)
app = Launcher()
app.update()
check("档位 9 回落到 1", app.config_data["autonomy"] == 1,
      app.config_data["autonomy"])
check("工作区记成数字时回落到空串", app.config_data["autonomy_workspace"] == "",
      app.config_data["autonomy_workspace"])
app.destroy()

print()
print("== 12. 第 2 档：挑的指挥模型得跟着开出去 ==")
app = Launcher()
app.update()
app.track_running = lambda *a, **k: None
app._watch_terminal = lambda *a, **k: None
app._open_autonomy_dialog()
app.update()
dialog = find_toplevel("AI 托管")
radios = [w for w in walk(dialog) if w.winfo_class() == "Radiobutton"]
radios[1].invoke()
app.update()
next(w for w in walk(dialog) if w.winfo_class() == "Text").insert("1.0", "写个第一章")
app.update()
captured.clear()
ok = click(dialog, "开始托管")
app.update()
check("第 2 档开得出去", ok and find_toplevel("AI 托管") is None)
with open(captured["settings"], encoding="utf-8") as f:
    payload = json.load(f)
command = payload["hooks"]["Stop"][0]["hooks"][0]["command"]
check("命令行里带上了那个预设名", '--command-model "{}"'.format(DEPUTY) in command,
      command)
check("第 2 档不圈 Bash（拦命令是第 3 档的事）",
      payload["hooks"]["PreToolUse"][0]["matcher"] == "AskUserQuestion",
      payload["hooks"]["PreToolUse"][0]["matcher"])
app.destroy()

print()
print("== 13. 第 2 档没预设可挑时不许硬开 ==")
# 把它挪开而不是删掉：一个"没有副手的第 2 档"在行为上就是第 1 档，用户还以为
# 有人在替他拍板呢。宁可拦住让他先去加一个。
spare = preset_path(DEPUTY) + ".spare"
os.replace(preset_path(DEPUTY), spare)
app = Launcher()
app.update()
app.track_running = lambda *a, **k: None
app._watch_terminal = lambda *a, **k: None
app._open_autonomy_dialog()
app.update()
dialog = find_toplevel("AI 托管")
radios = [w for w in walk(dialog) if w.winfo_class() == "Radiobutton"]
radios[1].invoke()
app.update()
check("没预设时下拉是空的",
      not list([w for w in walk(dialog)
                if w.winfo_class() == "TCombobox"][-1].cget("values")))
check("告诉用户先去加一个",
      any("先去" in t and "模型" in t for t in (texts(w) for w in walk(dialog))))
next(w for w in walk(dialog) if w.winfo_class() == "Text").insert("1.0", "写个第一章")
app.update()
import claude_tool.ui.dialogs as D
real_showerror = D.messagebox.showerror
complained = []
D.messagebox.showerror = lambda *a, **k: complained.append(a)
captured.clear()
click(dialog, "开始托管")
D.messagebox.showerror = real_showerror
app.update()
check("点下去被拦住了", bool(complained), complained)
check("没真开出去", not captured)
check("对话框还开着，让用户改", find_toplevel("AI 托管") is not None)
os.replace(spare, preset_path(DEPUTY))
app.destroy()

print()
print("通过 {} 项，失败 {} 项".format(len(PASS), len(FAIL)))
if FAIL:
    print("失败的是：")
    for name in FAIL:
        print("  -", name)
sys.exit(1 if FAIL else 0)
