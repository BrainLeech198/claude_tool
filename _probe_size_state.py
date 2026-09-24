"""验两件事：窗口下限够不够装内容；那几个勾的状态存不存得住。

**0.4 起下限的基准换了对象**：以前量的是底栏那排勾的自然宽度（那排勾搬进设置窗
了，主窗里根本没这个对象可量，探针原来会拿到 None 再 TypeError），现在量正文
那两栏——左导航定宽 + 右栏详情那一排。`_apply_min_size` 就是拿这两个数算的。

**量右栏那排要按最宽状态量**：摆了「删交接文档」时才是最宽的（多 117 像素）。
Task 8 就是在这儿发现 `DETAIL_MIN_WIDTH` 定小了：上一版 390 只够摆五个按钮，
含第六个要 441，而最窄窗口只给右栏 442——余 1 像素，换个字体就完了。

那几个勾（embed / auto_continue / auto_version_check）还是同一批：变量在
`__init__` 里就建好了，不用开设置窗就能读能写，所以第 2、3 节一个字没改。

第 4 节验的是"存了个比下限还小的窗口尺寸，开出来会不会被撑回下限"。

沙箱 USERPROFILE，必须的：它会往 launcher.json 里写东西（把三个勾都设成 True、
还故意写一个比下限还小的窗口尺寸）。0.4 的启动路径本身就会落一次盘（`_sync_selection`
把「当前工作区」记下来），所以不沙箱一定会碰用户的真配置。
"""
import json
import os
import shutil
import sys

PROFILE = "D:/Desktop/tmp/size_state"
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE
shutil.rmtree(PROFILE, ignore_errors=True)
os.makedirs(PROFILE, exist_ok=True)

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.config import CONFIG_FILE                     # noqa: E402
from claude_tool.handoff import HANDOFF_FILE                   # noqa: E402
from claude_tool.ui.detail import DETAIL_MIN_WIDTH             # noqa: E402
from claude_tool.ui.launcher import (                          # noqa: E402
    MIN_HEIGHT_FLOOR,
    MIN_MARGIN,
    PAGE_PAD,
    Launcher,
)
from claude_tool.ui.nav import NAV_WIDTH                       # noqa: E402

BAD = []


def check(label, got, want):
    ok = got == want
    if not ok:
        BAD.append(label)
    print("  {} {:<38} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))


print("== 第一次启动（全新配置）==")
app = Launcher()
app.update_idletasks()

# 先摆出**最宽状态**再量：右栏「管理」那排摆了「删交接文档」时才是最宽的
# （多 117 像素）。下限要守的就是这个状态——detail.py 里 DETAIL_MIN_WIDTH 的
# 注释写的就是"保证缩到最窄时那排六个按钮还在同一行上"。
# Task 8 查出来的：上一版按不含这颗按钮的量法写 390，最窄时右栏只分到 442，
# 而含它的那排要 441，余 1 像素。
ws = os.path.join(PROFILE, "带交接")
os.makedirs(ws, exist_ok=True)
with open(os.path.join(ws, HANDOFF_FILE), "w", encoding="utf-8") as f:
    f.write("# 交接\n")
app.config_data["workspaces"] = [{"name": "带交接", "path": ws,
                                  "permission": "acceptEdits",
                                  "handoff_ignore_git": False}]
app.refresh_workspaces()
app.update()

nav_need = app.side.winfo_reqwidth()
manage_row = app._detail_manage[0].master
manage_need = manage_row.winfo_reqwidth()
print("  窗口实际 :", app.winfo_width(), "x", app.winfo_height())
print("  minsize  :", app.minsize())
print("  左导航要 :", nav_need, "像素（定宽常数）")
print("  右栏自然宽 :", app.detail_area.winfo_reqwidth(),
      "像素（里面有个显示路径的 Label，会随路径长短飘，不做下限依据）")
print("  「管理」那排要 :", manage_need, "像素（含「删交接文档」，这才是依据）")
print("  右栏实际分到 :", app.detail_area.winfo_width(), "像素")
print("  embed 默认:", app.embed_var.get(), " auto_continue 默认:",
      app.auto_continue_var.get(), " auto_version_check 默认:",
      app.auto_version_var.get())

check("「删交接文档」摆出来了（那排的最宽状态）",
      app._handoff_btn.winfo_manager(), "pack")
# 左导航是定宽，量出来必须就是那个常数
check("左导航就是那个定宽数", nav_need, NAV_WIDTH)
# 给它定的下限得真够摆下那排按钮——不然缩到最窄「管理」那排就换行了
check("DETAIL_MIN_WIDTH 够摆下那排（含删交接文档）",
      manage_need <= DETAIL_MIN_WIDTH, True)
# 下限就是 _apply_min_size 那条式子，钉住它
check("下限宽度 = 两栏 + 各道缝",
      app.minsize()[0],
      NAV_WIDTH + PAGE_PAD + DETAIL_MIN_WIDTH + 2 * PAGE_PAD + MIN_MARGIN)
check("下限高度 = 可用底线", app.minsize()[1], MIN_HEIGHT_FLOOR)

# 真缩到下限看那排有没有换行——"常数够大"和"布局真没换行"是两件事
app.geometry("{}x{}".format(*app.minsize()))
app.update()
lines = {child.winfo_y() for child in manage_row.winfo_children()}
check("缩到最窄那排也没换行", len(lines), 1)
check("而且离换行还有余量（不是刚好卡住）",
      app.detail_area.winfo_width() - manage_need >= 20, True)
app.destroy()

print()
print("== 把勾都勾上，看落盘 ==")
app = Launcher()
app.embed_var.set(True)
app._on_embed_toggle()
app.auto_continue_var.set(True)
app._on_auto_continue_toggle()
app.auto_version_var.set(True)
app._on_version_check_toggle()
app.destroy()
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    saved = json.load(f)
print("  launcher.json 里 embed =", saved.get("embed"),
      " auto_continue =", saved.get("auto_continue"),
      " auto_version_check =", saved.get("auto_version_check"))
check("三个勾都落盘了",
      (saved.get("embed"), saved.get("auto_continue"),
       saved.get("auto_version_check")), (True, True, True))

print()
print("== 再开一次，看有没有记住 ==")
app = Launcher()
app.update_idletasks()
print("  embed 读回:", app.embed_var.get(),
      " auto_continue 读回:", app.auto_continue_var.get(),
      " auto_version_check 读回:", app.auto_version_var.get())
check("三个勾都读回来了",
      (app.embed_var.get(), app.auto_continue_var.get(),
       app.auto_version_var.get()), (True, True, True))
app.destroy()

print()
print("== 存档里的窗口尺寸比下限还小时，会不会被撑回去 ==")
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    saved = json.load(f)
saved["window"] = {"x": 100, "y": 100, "w": 560, "h": 520}
with open(CONFIG_FILE, "w", encoding="utf-8") as f:
    json.dump(saved, f, ensure_ascii=False, indent=2)

app = Launcher()
app.update_idletasks()
print("  存档写的 560x520，开出来:", app.winfo_width(), "x", app.winfo_height())
print("  下限 :", app.minsize())
check("宽度被撑回了下限", app.winfo_width() >= app.minsize()[0], True)
check("高度也被撑回了下限", app.winfo_height() >= app.minsize()[1], True)
app.destroy()

print()
print("结果:", "全过" if not BAD else "没过：" + str(BAD))
sys.exit(1 if BAD else 0)
