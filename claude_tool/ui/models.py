"""模型区：预设列表、测试、切换，以及换模型时把开着的会话接过去的那条流水线。

从 launcher.py 里按域切出来的一块（见 设计说明-0.4.md 第三节）。最后那条流水线
跟搬工作区共用同一块进度区，所以那个互斥判断（_busy）留在主窗上，两边都调它。

以 mixin 的形式挂在 Launcher 上——主窗那些实例状态（model_list、model_rows、
_task_queue、_results…）都还在它的 __init__ 里，一个字没动。
"""
import os
import queue
import shutil
import threading
import tkinter as tk
from tkinter import messagebox

from claude_tool.handoff import HANDOFF_FILE, READ_HANDOFF_PROMPT
from claude_tool.host import close_window, window_alive
from claude_tool.paths import SETTINGS_FILE
from claude_tool.presets import (
    active_preset,
    describe_preset,
    discover_presets,
    read_model,
    test_preset,
)
from claude_tool.theme import MUTED, OK, PAGE_BG, TEXT, WARN, font
from claude_tool.widgets import PillButton, Row


# 迁移流水线里"等旧会话退干净""等新窗口认出来"的轮询节奏和上限。早先这几处是拍
# 一个固定秒数硬等（关完旧窗口等 1.5 秒、重开完等 2.5 秒）：快机器上白等，慢机器
# 上又未必够。改成看条件——一成立立刻走，到上限就不再等，记一句照常往下走。
#
# 跟着这段流水线一起从 launcher.py 搬过来：整份代码里只有 _wait_until 用它俩。
MIGRATE_POLL_MS = 200
MIGRATE_WAIT_TRIES = 75          # 200 毫秒 × 75 ≈ 15 秒


class ModelsMixin:
    def refresh_models(self):
        # 0.4 起「模型」这一区搬进了设置窗（见 ui/settings.py），窗口没开过时
        # 这张列表还不存在。这儿**只是提前返回，不是整个跳过**：顶栏那颗模型
        # 胶囊在主窗上一直摆着，它读的是 active_name，得照样更新——不然新装一份
        # 预设、设置窗还没开，胶囊会一直写着上次那个名字。
        if self.model_list is None:
            presets = discover_presets()
            self.active_name = active_preset(presets)
            self._update_model_chip(
                self.active_name or ("未设置" if not presets else "自定义 / 未知"),
                bool(self.active_name))
            return
        self.model_list.clear()
        self.model_rows = {}
        presets = discover_presets()
        self.active_name = active_preset(presets)
        inner = self.model_list.inner

        if not presets:
            # 空状态是新手第一眼撞上的东西，别只丢一句"点右上角"。先把"模型"这
            # 个词解释了（备注留着，术语本身不动），再把两条路直接摆到眼前。
            tk.Label(inner, text="还没有模型。", bg=PAGE_BG, fg=TEXT,
                     font=font(10, True)).pack(anchor="w", padx=6, pady=(12, 3))
            tk.Label(inner, text="模型 = 你打算用哪家的 AI（DeepSeek、Kimi 这种）。\n"
                                 "已经在别的窗口里用着 claude 了，就直接导进来；"
                                 "没配过就手动填一个。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                     ).pack(anchor="w", padx=6, pady=(0, 9))
            buttons = tk.Frame(inner, bg=PAGE_BG)
            buttons.pack(anchor="w", padx=6)
            PillButton(buttons, "导入当前在用的", self._import_current, primary=True,
                       bg=PAGE_BG).pack(side="left")
            PillButton(buttons, "手动填", self._open_model_dialog, bg=PAGE_BG,
                       ).pack(side="left", padx=(8, 0))
            self._update_model_chip("未设置", False)
            self.model_list.fit()
            return

        for name in sorted(presets):
            path = presets[name]
            row = Row(inner, title=name, subtitle=describe_preset(path), marker=True,
                      active=(name == self.active_name), height=50,
                      on_click=lambda n=name, p=path: self.switch_model(n, p),
                      actions=[("移除", lambda n=name, p=path: self.remove_model(n, p)),
                               ("编辑", lambda n=name, p=path: self._open_model_dialog((n, p)))])
            row.pack(fill="x", pady=3)
            self.model_rows[name] = row
        self._update_model_chip(self.active_name or "自定义 / 未知",
                                bool(self.active_name))
        self.model_list.fit()

    def test_all_models(self):
        """并发打一遍所有预设，结果落在各自的卡片右侧。"""
        if self._testing:
            self.feedback_var.set("上一轮还在测，等等。")
            return
        presets = discover_presets()
        jobs = [(name, presets[name]) for name in self.model_rows if name in presets]
        if not jobs:
            self.feedback_var.set("没有可测的模型预设。")
            return

        self._testing = True
        self._results = queue.Queue()
        for row in self.model_rows.values():
            row.set_warn("测试中…", MUTED)
        self.feedback_var.set("正在测 {} 个模型…".format(len(jobs)))

        def worker(name, path):
            self._results.put((name, test_preset(path)))

        for name, path in jobs:
            threading.Thread(target=worker, args=(name, path), daemon=True).start()
        self.after(100, lambda: self._drain_results(len(jobs)))

    def _drain_results(self, remaining):
        """在主线程里收后台线程塞进队列的结果——Tk 控件只能主线程碰。"""
        try:
            while True:
                name, (ok, note) = self._results.get_nowait()
                remaining -= 1
                row = self.model_rows.get(name)
                if row is not None:
                    try:
                        row.set_warn(note, OK if ok else WARN)
                    except tk.TclError:
                        pass
        except queue.Empty:
            pass
        if remaining > 0:
            self.after(100, lambda: self._drain_results(remaining))
            return
        self._testing = False
        self.feedback_var.set("测完了：绿色=打得通，红色=打不通。")

    def switch_model(self, name, path):
        try:
            shutil.copyfile(path, SETTINGS_FILE)
        except Exception as e:
            messagebox.showerror("切换失败", "写入 settings.json 失败：\n{}".format(e))
            return
        self.feedback_var.set(
            "已切换到 {}（{}）。重启 Claude Code 后生效。".format(name, read_model(path)))
        self.refresh_models()
        self._offer_migration()

    # ── 换模型时把开着的会话接过去 ──

    def _offer_migration(self):
        """正在跑的会话还挂在旧模型上，逐个问要不要接着聊下去。

        claude 是启动时读一次 settings.json，已经跑起来的那些不会因为换了预设
        就跟着变，得重开。重开走 `--continue`，claude 自己把上次那段上下文整个
        接过去——所以**这条路不写交接文档**（早先那版每换一次模型都先烧一轮
        token 写一份，等它写完才重开，慢且没多大用：同一个会话自己的上下文比
        一份总结更全）。想让新会话读一份提炼过的东西，就自己先点「整理交接文档」
        写一份——那个目录里有 handoff.md 时，重开的会话会带着"先读它"起来。

        问的是"要不要续"这件事本身，所以措辞里得把后果说全：旧窗口会被关掉。
        不然用户以为旧窗口会留着，结果被关掉了。
        """
        if self._busy():
            self.feedback_var.set("上一轮活还没干完，等它跑完。")
            return
        sessions = list(self.running)
        if not sessions:
            return
        embedded_proc = self.embedded.process if self.embedded is not None else None

        picked = []
        for item in sessions:
            if messagebox.askyesno(
                    "要接着聊吗",
                    "「{}」这个会话还挂在旧模型上。\n\n"
                    "要关掉它，用新模型接着上次的上下文重新开吗？\n\n"
                    "接着聊靠 claude 自己的 --continue，不写交接文档。"
                    .format(item["name"])):
                picked.append({"item": item,
                               "was_embedded": item["proc"] is embedded_proc})
        if not picked:
            self.feedback_var.set("换好了。正在跑的会话保持原样，没动它们。")
            return

        self._task_queue = picked
        self._task_total = len(picked)
        self._task_running = True
        self.task_head_var.set("正在换模型")
        self.feedback_var.set("要接着聊的有 {} 个，一个一个来。".format(self._task_total))
        # 这一步别漏：流水线早先靠"先写交接文档"那一步顺手把进度区摆出来，那条路
        # 砍掉之后没人摆了——不摆的话进度还在走、步骤行还在变，但用户眼前什么都
        # 没有，看着就像点完没反应。
        self._show_task_area()
        self._run_next_task()

    def _run_next_task(self):
        """流水线跑下一个会话：关旧的 → 用新模型接着重开。"""
        if self._pending:
            # 上一个内嵌会话还在等控制台窗口冒出来。这会儿再开一个，
            # embed_workspace 会直接打回去、那个工作区就白排队了。
            self.after(500, self._run_next_task)
            return
        if not self._task_queue:
            self._finish_tasks()
            return

        job = self._task_queue.pop(0)
        item = job["item"]
        self.task_count_var.set("第 {}/{} 个".format(
            self._task_total - len(self._task_queue), self._task_total))
        self.task_name_var.set(item["name"])
        self._task_step("先把「{}」旧的窗口关掉。".format(item["name"]))
        self.after(500, lambda: self._close_old_session(job))

    def _wait_until(self, pred, then, note, tries=0):
        """等条件成立再往下走，最多 MIGRATE_WAIT_TRIES 轮（每轮 MIGRATE_POLL_MS）。

        到上限就不再等，把 note 摆到进度条上照常往下走——各步自己都有兜底，卡死
        在这儿比早走一步更糟。
        """
        done = pred()
        if done or tries >= MIGRATE_WAIT_TRIES:
            if not done:
                self._task_step(note)
            then()
            return
        self.after(MIGRATE_POLL_MS,
                   lambda: self._wait_until(pred, then, note, tries + 1))

    def _close_old_session(self, job):
        item = job["item"]
        if job["was_embedded"] and self.embedded is not None:
            # 句柄得在 close_terminal 把 self.embedded 清掉之前先拿住。它只给个
            # WM_CLOSE，三秒还没退就硬掐（见 close_terminal），所以这一等有底。
            process = getattr(self.embedded, "process", None)
            self.close_terminal()
            if process is None:
                self._launch_migrated(job)
                return
            self._task_step("等旧的那个会话退出去，退了就用新模型重开。")
            self._wait_until(lambda: process.poll() is not None,
                             lambda: self._launch_migrated(job),
                             "旧会话没退利索，先往下走。")
            return
        hwnd = item.get("hwnd")
        item["hwnd"] = None
        if hwnd and window_alive(hwnd):
            close_window(hwnd)
            # 窗口是异步退的。等它真没了再开新的，免得两扇撞在同一块地方。
            self._task_step("等旧窗口关掉，关了就用新模型重开。")
            self._wait_until(lambda: not window_alive(hwnd),
                             lambda: self._launch_migrated(job),
                             "旧窗口没关利索，先往下走。")
            return
        self._task_step("旧的关掉了，准备用新模型重开。")
        self._launch_migrated(job)

    def _launch_migrated(self, job):
        item = job["item"]
        self._task_step("用新模型重开「{}」。".format(item["name"]))
        # 目录里真有那份文档才让新会话去读它——硬塞的话，没写过交接的目录里就是
        # 让 claude 去读一个不存在的东西。跟点工作区那个弹框里「先读交接文档」
        # 那一勾是同一套判断（那边也是看见了文件才摆出来）。
        prompt = None
        if os.path.isfile(os.path.join(item["path"], HANDOFF_FILE)):
            prompt = READ_HANDOFF_PROMPT
        entry = self.launch_workspace(item, cont=True, prompt=prompt)
        # 下一轮开新窗口前要先拍一份窗口名单、再比出哪扇是新开的。刚开出去那扇要是
        # 还没认出来，两扇新窗口会挤进同一次比对里，句柄认串。等它认出来再往下走。
        # 内嵌那条路不用等：那扇是本窗口里的一栏、不参与比对，而且 _run_next_task
        # 自己会躲着 _pending。
        if entry is None or entry.get("watched"):
            self._run_next_task()
            return
        self._wait_until(
            lambda: entry.get("watched") or entry["proc"].poll() is not None,
            self._run_next_task,
            "新窗口还没认出来，先接着往下走。")

    def _finish_tasks(self):
        self._task_running = False
        self._task_queue = []
        self._hide_task_area()
        self.feedback_var.set(
            "换模型这件活干完了：{} 个会话都用新模型接着上次的上下文重开了。".format(
                self._task_total))

    def remove_model(self, name, path):
        message = "删除模型预设「{}」？\n\n{}".format(name, path)
        if name == self.active_name:
            message += "\n\n它正是当前生效的模型，删掉后 settings.json 保持原样。"
        if not messagebox.askyesno("删除模型", message):
            return
        try:
            os.remove(path)
        except Exception as e:
            messagebox.showerror("删除失败", "删不掉 {}：\n{}".format(path, e))
            return
        self.feedback_var.set("已删除模型预设 {}。".format(name))
        self.refresh_models()
