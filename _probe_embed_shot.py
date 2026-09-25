"""内嵌状态截一张图，看左列有没有被挤窄。"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

ROOT = os.path.dirname(os.path.abspath(__file__))
from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("embed_shot")
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE
sys.path.insert(0, ROOT)
from _probe_common import rebind  # noqa: E402

from claude_tool import claude as C
from claude_tool import winhost as W
from claude_tool.paths import WORKPLACE_DIR
from claude_tool.ui import launcher as L

os.makedirs(WORKPLACE_DIR, exist_ok=True)

OUT = sys.argv[1] if len(sys.argv) > 1 else "embed_shot.png"
TITLE = "Claude 启动器"

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

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


def spawn(workdir, cont=False, prompt=None, settings=None, permission=None):
    known = {hwnd for hwnd, _ in W.console_windows()}
    cmd = "title {} & echo fake claude & ping -n 600 127.0.0.1 > nul".format(
        W.TITLE_TAG)
    proc = _track(subprocess.Popen(
        ["conhost.exe", "cmd", "/k", cmd],
        cwd=workdir, creationflags=C.CREATE_NEW_CONSOLE))
    return proc, known


rebind("spawn_console", spawn)
app = L.Launcher()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


pump(2.5)
app.embed_workspace(app.config_data["workspaces"][0])
pump(4)

hwnd = user32.FindWindowW(None, TITLE)
user32.SetWindowPos(hwnd, 0, 60, 60, 0, 0, 0x0001 | 0x0004)
pump(1.0)
r = wintypes.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))
img = ImageGrab.grab(bbox=(r.left + 8, r.top + 8, r.right - 8, r.bottom - 8))
img.save(OUT)
print("saved", OUT, img.size)
left = sweep_spawned()
if left:
    print("  收掉了 {} 个漏下的假窗口".format(left))
app.destroy()
shutil.rmtree(PROFILE, ignore_errors=True)
