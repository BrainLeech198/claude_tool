"""没存过尺寸时，窗口是不是真按 default_window_size 说的那个数开出来。

顺手验两件连带的事：这个尺寸下窗口下限（minsize）没把它顶大；关窗时存回去的
也是这个数，下次开还是它。

不截图。USERPROFILE/HOME 先指到临时目录——paths.py 是在 import 那一下就把
CLAUDE_DIR expanduser 出来的，所以必须赶在 import claude_tool 之前改。

    python _probe_winsize.py
"""
import json
import os
import shutil
import sys
import tempfile

SANDBOX = os.path.join(tempfile.gettempdir(), "winsizeprobe")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX)
os.environ["USERPROFILE"] = SANDBOX
os.environ["HOME"] = SANDBOX

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_tool.ui.launcher import Launcher, default_window_size  # noqa: E402

bad = []


def check(label, ok, detail=""):
    print("  {} {}{}".format("ok  " if ok else "FAIL", label,
                             "  " + str(detail) if detail else ""), flush=True)
    if not ok:
        bad.append(label)


print("== 公式本身 ==")
check("普通屏幕上是 964x950", default_window_size(2560, 1440) == (964, 950),
      default_window_size(2560, 1440))
check("超宽屏上还是 964x950", default_window_size(5120, 2160) == (964, 950),
      default_window_size(5120, 2160))
small = default_window_size(1366, 768)
check("768 高的本子上收进屏幕里", small[1] < 768 and small[0] <= 1366, small)

print()
print("== 真开一扇 ==")
app = Launcher()
app.update()
w, h = app.winfo_width(), app.winfo_height()
check("开出来就是 964x950", (w, h) == (964, 950), "{}x{}".format(w, h))
check("minsize 没把它顶大", (app.minsize()[0], app.minsize()[1]) <= (964, 950),
      app.minsize())

cfg = os.path.join(SANDBOX, ".claude_tool", "launcher.json")
# 配置文件开窗那会儿就已经有了（_build_ui 里要建 workplace 目录、落一次盘），
# 要看的是 window 那一格空着没有——空着才说明刚才是拿默认值开的。
with open(cfg, encoding="utf-8") as f:
    check("开窗时 window 那格是空的（所以走的才是默认值）",
          not json.load(f).get("window"))

app.after(50, app._on_close)
app.mainloop()

if os.path.isfile(cfg):
    with open(cfg, encoding="utf-8") as f:
        saved = json.load(f).get("window") or {}
    check("关窗把这个尺寸存了回去",
          (saved.get("w"), saved.get("h")) == (964, 950), saved)
else:
    check("关窗时把配置写出来了", False, cfg)

shutil.rmtree(SANDBOX, ignore_errors=True)
print()
print("RESULT " + ("FAIL " + " | ".join(bad) if bad else "OK"))
sys.exit(1 if bad else 0)
