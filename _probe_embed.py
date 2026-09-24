"""验内嵌终端两件事：右栏出现时左列动不动；「关掉」按钮能不能真把会话关掉。

不跑真 claude——拿个光秃秃的 conhost 冒充，标题照 winhost 的 TITLE_TAG 打，
fresh_console 认的就是这个标记。
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE = "D:/Desktop/tmp/embed"
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE
sys.path.insert(0, ROOT)
from _probe_common import rebind                       # noqa: E402

from claude_tool import claude as C
from claude_tool import winhost as W
from claude_tool.paths import WORKPLACE_DIR
from claude_tool.ui import launcher as L

# 沙箱里没有工作区目录，而假控制台是拿工作区路径当 cwd 起的；没有这一句
# Popen 会直接 FileNotFoundError。真配置下那个目录本来就在，正好把这点盖住了。
os.makedirs(WORKPLACE_DIR, exist_ok=True)

# 见 _probe_migrate.py：假窗口得按进程树收，只杀 cmd.exe 留个 ping 压着目录。
SPAWNED = []


def _track(proc):
    SPAWNED.append(proc)
    return proc


def sweep_spawned():
    killed = 0
    for proc in SPAWNED:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            killed += 1
    return killed


def fake_spawn(workdir, cont=False, prompt=None, settings=None, permission=None):
    known = {hwnd for hwnd, _ in W.console_windows()}
    cmd = "title {} & echo fakeprocess & ping -n 600 127.0.0.1 > nul".format(
        W.TITLE_TAG)
    proc = _track(subprocess.Popen(
        ["conhost.exe", "cmd", "/k", cmd],
        cwd=workdir, creationflags=C.CREATE_NEW_CONSOLE))
    return proc, known


rebind("spawn_console", fake_spawn)

app = L.Launcher()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


app.update()
pump(0.3)

side_before = app.side.winfo_width()
win_before = app.winfo_width()
print("内嵌前   窗口 {}  左列 {}".format(win_before, side_before))

item = app.config_data["workspaces"][0]
app.embed_workspace(item)
pump(5)

print("嵌上了吗 :", app.embedded is not None)
if app.embedded is None:
    print("  反馈栏:", app.feedback_var.get())
else:
    print("内嵌后   窗口 {}  左列 {}".format(app.winfo_width(),
                                             app.side.winfo_width()))
    print("  左列宽度没动 :", app.side.winfo_width() == side_before)
    print("  终端栏宽 {} 摆着没 {}".format(app.panel.winfo_width(),
                                           app.panel.winfo_ismapped()))
    print("  minsize      :", app.minsize())
    proc = app.embedded.process

    app.close_terminal()
    pump(6)
    print("关掉后 进程还活着 :", proc.poll() is None)
    print("  右栏还摆着吗    :", app.panel.winfo_ismapped(),
          " 标题栏:", app.term_head.winfo_ismapped(), " embedded:",
          app.embedded)
    print("  窗口宽还原      :", app.winfo_width(), "(原来是", win_before, ")")
    print("  左列宽还原      :", app.side.winfo_width(), "(原来是", side_before, ")")

left = sweep_spawned()
if left:
    print("  收掉了 {} 个漏下的假窗口".format(left))
app.destroy()
shutil.rmtree(PROFILE, ignore_errors=True)
