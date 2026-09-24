"""工作区：列表怎么扫出来、怎么排序筛选、怎么搬迁改名移除，以及默认目录。

从 launcher.py 里按域切出来的一块（见 设计说明-0.4.md 第三节）。拉起会话那件事
不在这儿，在 ui/sessions.py —— 这里只管"有哪些书"。

以 mixin 的形式挂在 Launcher 上——主窗那些实例状态（ws_filter、ws_view、
_move_job、config_data…）都还在它的 __init__ 里，一个字没动。
"""
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from claude_tool import mover
from claude_tool.config import (
    humanize_ago,
    last_chat_time,
    merge_scanned,
    path_key,
    save_config,
)
from claude_tool.handoff import HANDOFF_FILE
from claude_tool.host import trash_path
from claude_tool.paths import TOOL_DIR
from claude_tool.theme import ACCENT, MUTED, PAGE_BG, TEXT, WARN, font
from claude_tool.widgets import Row


# 搬工作区时主线程去后台线程那儿收进度的节奏。比迁移流水线那个松一点：复制是 IO
# 活，一次复制几百个小文件也就几毫秒，追得太紧是白烧 CPU；而几百毫秒的延迟摆在
# "已经复制了 N 个"那行字上，人眼看不出来。
#
# 跟着搬迁那几条方法一起从 launcher.py 搬过来：整份代码里只有 _poll_move 用它。
MOVE_POLL_MS = 250


class WorkspacesMixin:

    # ── 有哪些书 ──
    def refresh_workspaces(self):
        self.ws_list.clear()
        inner = self.ws_list.inner
        workspaces = self.config_data["workspaces"]
        needle = self.ws_filter.get().strip().lower()

        # 筛选后编号会变，所以快捷键按的是"屏幕上第几个"，不是配置里的下标。
        view = [item for item in workspaces
                if needle in item["name"].lower() or needle in item["path"].lower()] \
            if needle else list(workspaces)
        self.ws_view = view

        if not workspaces:
            # 跟模型那边一个路数：先把"工作区"这个词解释掉，再说去哪儿加。原来只有
            # 一句"点右上角"，可"工作区"本身对没上手的人就不是个自明的词。
            tk.Label(inner, text="还没有工作区。", bg=PAGE_BG, fg=TEXT,
                     font=font(10, True)).pack(anchor="w", padx=6, pady=(12, 3))
            tk.Label(inner, text="工作区 = 一个项目文件夹，claude 就在那儿读写文件。\n"
                                 "右上角「＋ 添加工作区」既能新建一个文件夹，"
                                 "也能把已经有的目录加进来。",
                     bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                     ).pack(anchor="w", padx=6, pady=(0, 9))
            # 上面那个分支（筛不出来）有 fit()，这条原来漏了——空列表时滚动区的高度
            # 还停在上一次 fit 的值上，跟"没有工作区"该占的高度对不上。
            self.ws_list.fit()
            return
        if not view:
            tk.Label(inner, text="没有匹配「{}」的工作区。".format(self.ws_filter.get().strip()),
                     bg=PAGE_BG, fg=MUTED, font=font(10)).pack(anchor="w", pady=10, padx=6)
            self.ws_list.fit()
            return

        for position, item in enumerate(view):
            path = item["path"]
            exists = os.path.isdir(path)
            # 右侧那格放"这行现在什么状态"：有没有交接文档、上次聊是多久以前。
            # 不往副标题里塞是有原因的——副标题是路径，长了会被省略号吃掉尾巴，
            # 而"上次聊"正好就是被吃掉的那部分，等于白写。右侧这格是右对齐的，
            # 永远不会被截断。
            has_handoff = exists and os.path.isfile(os.path.join(path, HANDOFF_FILE))
            if not exists:
                note, note_color = "目录不存在", WARN
            else:
                stamp = last_chat_time(path)
                parts = []
                if has_handoff:
                    parts.append("有交接文档")
                if stamp is not None:
                    parts.append("上次聊 " + humanize_ago(stamp))
                # 有交接文档是"现在能接着干"的信号，用强调色；只是时间就是中性灰。
                note = " · ".join(parts)
                note_color = ACCENT if parts[:1] == ["有交接文档"] else MUTED
            # actions 是从右往左摆的（下标 0 在最右边），所以这里的顺序要倒着念：
            # 屏幕上从左到右是 搬迁 开目录 ↑ ↓ 改名 移除。上移/下移用箭头不用词，
            # 是因为一行里塞六个两字词会糊成一片，而箭头没有认不出来的风险。
            actions = [("移除", lambda it=item: self.remove_workspace(it)),
                       ("改名", lambda it=item: self.rename_workspace(it)),
                       ("↓", lambda it=item: self.move_workspace(it, 1)),
                       ("↑", lambda it=item: self.move_workspace(it, -1)),
                       ("开目录", lambda p=path: self.open_folder(p)),
                       ("搬迁", lambda it=item: self.open_move_dialog(it))]
            # 「删交接文档」挂在列表最末 = 屏幕最左边。动作是从右边开始排的，加在
            # 末尾意味着上面那六个格子一个都不挪窝——只有真有文档的行，最左边才多
            # 冒出来这一颗。没有文档的行不摆它，免得按下去只得到一句"没有"。
            if has_handoff:
                actions.append(("删交接文档",
                                lambda it=item: self.remove_handoff(it)))
            Row(inner, title=item["name"], subtitle=path, height=52,
                warn=note, warn_color=note_color,
                # 行号就是 Ctrl+行号。9 以后没有号（按不到），但位置留着，
                # 免得后几行的标题跟前面错开一格。
                badge=str(position + 1) if position < 9 else "",
                on_click=lambda it=item: self.open_workspace(it),
                actions=actions,
                ).pack(fill="x", pady=3)
        self.ws_list.fit()

    def move_workspace(self, item, step):
        """把工作区在列表里挪一格。step 是 -1 上移、+1 下移。

        挪的是配置里 workspaces 的顺序——Ctrl+1~9 选的就是屏幕上第几个，而屏幕
        顺序就是这个数组的顺序。所以挪完立刻落盘，下次打开还是这个次序。

        用 is 而不是 == 找位置：配置里出现两条内容完全一样的工作区时，== 会一直
        认成第一个，按下去就没反应了。
        """
        workspaces = self.config_data["workspaces"]
        index = next((i for i, w in enumerate(workspaces) if w is item), None)
        if index is None:
            return
        target = index + step
        if not 0 <= target < len(workspaces):
            self.feedback_var.set("「{}」已经在{}了。".format(
                item["name"], "最上面" if step < 0 else "最下面"))
            return
        workspaces[index], workspaces[target] = workspaces[target], workspaces[index]
        save_config(self.config_data)
        self.refresh_workspaces()

    # ── 搬工作区 ──

    def move_workspace_to(self, item, target):
        """把整个工作区目录搬到新位置，会话记录那份跟着搬。

        顺序是死的，mover 那边定下了：检查 → 复制 → 对账 → 改配置 → 丢回收站。
        这里只负责在开工前问清两件只有界面知道的事、摆进度、把结果说给用户听；
        中间一步都不自己动手，全都交给 mover，免得两头各有一套判断标准。

        先问的两件事比 mover 里任何一条都要紧：文件夹正被会话写的时候复制，复制
        出来的是半截的东西，对账也可能通不过——而那两个 mover 从文件系统上看不
        出来。
        """
        if self._busy():
            self.feedback_var.set("启动器手上有活，等它跑完再搬。")
            return
        if self._handoff_proc is not None and self._handoff_proc.poll() is None:
            # 手动整理交接文档不算 _busy（那条路不占 _task_running），但它也占着
            # 同一块进度区，写完还会把它收回去——这会儿插一个搬迁进去，进度区会
            # 被它收走。写的正好是正要搬的这个目录时更得等：文件夹正被写。
            self.feedback_var.set("正有一份交接文档在写，等它写完再搬。")
            return
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return
        key = path_key(path)
        if any(path_key(s["path"]) == key for s in self.running):
            messagebox.showerror(
                "那个会话还开着",
                "「{}」这个工作区的会话正开着。\n\n"
                "先把它关掉再搬——文件夹正被写的时候复制，复制出来的是半截的"
                "东西，对账也会对不上。".format(item["name"]))
            return

        project_from, project_to = mover.project_paths(path, target)
        try:
            target = mover.check(path, target, project_from, project_to)
        except mover.MoveError as e:
            messagebox.showerror("搬不了", str(e))
            return

        self._move_running = True
        self._move_job = {"item": item, "source": path, "target": target,
                          "project_from": project_from, "project_to": project_to}
        self.task_head_var.set("正在搬工作区")
        self.task_count_var.set("")
        self.task_name_var.set(item["name"])
        self._show_task_area()
        self._task_started = time.time()
        self._task_step("正在复制整个目录……")
        threading.Thread(target=self._move_worker, args=(self._move_job,),
                         daemon=True).start()
        self.after(100, self._poll_move)

    def _move_worker(self, job):
        """复制 + 对账跑在后台线程里，进度和结果都走队列回主线程。

        两棵树共用同一个回调，靠 phase 那个格子区分现在在复制哪一份——会话记录
        通常小得多，不分开说清楚的话，用户会以为进度条退回去了。
        """
        queue_ = self._move_queue
        phase = ["正在复制整个目录"]

        def note(count):
            queue_.put(("count", count, phase[0]))

        try:
            mover.copy_tree(job["source"], job["target"], progress=note)
            phase[0] = "正在复制会话记录"
            mover.copy_project(job["project_from"], job["project_to"], progress=note)
        except mover.MoveError as e:
            queue_.put(("fail", str(e)))
            return
        except Exception as e:                       # noqa: BLE001
            queue_.put(("fail", "复制的时候出了岔子：{}".format(e)))
            return
        queue_.put(("copied", None))

    def _poll_move(self):
        """主线程收后台线程的进度：Tk 控件只能主线程碰。"""
        event = None
        try:
            while True:
                event = self._move_queue.get_nowait()
        except queue.Empty:
            pass
        if event is None:
            self.after(MOVE_POLL_MS, self._poll_move)
            return
        if event[0] == "count":
            self._task_step("{}……已经复制了 {} 个文件".format(event[2], event[1]))
            self.after(MOVE_POLL_MS, self._poll_move)
            return
        if event[0] == "fail":
            # 复制这一层没成。旧的没动、新的可能留了半截在目标位置上——mover
            # 那头的话里已经把这两句说了，原样摆出来。
            self._finish_move(None, event[1])
            return
        self._commit_move(self._move_job)

    def _commit_move(self, job):
        """复制对过账了，改配置——这一步得在主线程做，界面手里那份就是配置。

        配置写不成就不丢旧的：新的那份留在那儿（大不了用户自己删），旧的原封不
        动、配置也还指着旧的，至少系统是自洽的。反过来先丢旧的再存配置，中间一
        失败就是两边都没了。
        """
        item = job["item"]
        old_name, old_path = item["name"], item["path"]
        new_path = job["target"]
        # 名字跟着搬：没被用户改过（正等于旧文件夹名）的就换成新文件夹名，这样
        # 顺手改名那一下才有用；用户自己起的名字是人给的名字，照留。
        if old_name == os.path.basename(old_path.rstrip("/\\")):
            item["name"] = os.path.basename(new_path.rstrip("/\\"))
        item["path"] = new_path
        try:
            save_config(self.config_data)
        except OSError as e:
            item["name"], item["path"] = old_name, old_path
            self._finish_move(None, "配置写不成（{}），旧的一个没动，"
                                    "新位置那份你自己删一下。".format(e))
            return
        self._task_step("配置改好了，正在把旧的那份丢进回收站。")
        self.after(MOVE_POLL_MS, lambda: self._discard_move(job))

    def _discard_move(self, job):
        """新的对过账、配置也指着新的了，这才轮到旧的。

        丢不成【不回滚】——这时候新的那份已经在用了，把旧的搬回来才是真乱。
        丢不成的如实报出来让用户自己处理。
        """
        stuck = mover.discard(job["source"], job["project_from"])
        self._finish_move(job, None, stuck)

    def _finish_move(self, job, error, stuck=()):
        self._move_running = False
        self._move_job = None
        self._hide_task_area()
        if error is not None:
            self.feedback_var.set("没搬成：{}".format(error))
            return
        name = job["item"]["name"]
        if stuck:
            self.feedback_var.set(
                "「{}」搬到 {} 了，但旧的那份没能丢进回收站（{}），得你自己删。"
                .format(name, job["target"], "、".join(stuck)))
        else:
            self.feedback_var.set("「{}」搬到 {} 了，旧的进了回收站。"
                                  .format(name, job["target"]))
        self.refresh_workspaces()

    # ── 改名、移除、默认目录 ──

    def rename_workspace(self, item):
        new_name = simpledialog.askstring("重命名工作区", "显示名称：",
                                          initialvalue=item["name"], parent=self)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showerror("名称为空", "显示名称不能为空。")
            return
        item["name"] = new_name
        save_config(self.config_data)
        self.refresh_workspaces()
        self.feedback_var.set("已重命名为 {}。".format(new_name))

    def remove_workspace(self, item):
        if not messagebox.askyesno(
                "移除工作区",
                "把「{}」从列表里拿掉？\n目录本身不会被删除。".format(item["name"])):
            return
        index = next((i for i, w in enumerate(self.config_data["workspaces"])
                      if w is item), -1)
        if index < 0:
            return
        del self.config_data["workspaces"][index]
        save_config(self.config_data)
        self.refresh_workspaces()
        self.feedback_var.set("已移除 {}。".format(item["name"]))

    def remove_handoff(self, item):
        """把工作区里那份 handoff.md 丢进回收站。

        交接文档是别人（claude）在用户目录里留下的一份文件，删它得让用户知道
        自己删的是什么、以及还能不能捞回来——所以先问一句，再走回收站而不是
        直接 unlink。删完刷新列表，"有交接文档"那行小字跟着消失。
        """
        target = os.path.join(item["path"], HANDOFF_FILE)
        if not os.path.isfile(target):
            self.feedback_var.set("「{}」里已经没有交接文档了。".format(item["name"]))
            self.refresh_workspaces()
            return
        if not messagebox.askyesno(
                "删除交接文档",
                "把「{}」里的 {} 丢进回收站？\n\n"
                "删掉之后，下次开这个工作区时「先读交接文档」那一步就没得读了。\n"
                "只是丢进回收站，还能找回来。".format(item["name"], HANDOFF_FILE)):
            return
        if trash_path(target):
            self.feedback_var.set(
                "「{}」的交接文档已丢进回收站。".format(item["name"]))
        else:
            messagebox.showerror(
                "删不掉",
                "没能把 {} 丢进回收站。\n可以手动去这个目录里删：\n{}".format(
                    HANDOFF_FILE, target))
        self.refresh_workspaces()

    def _set_workplace(self, base):
        """换默认工作区目录：老的那个从扫描列表里换掉，别的扫描目录留着。"""
        old = self.config_data.get("workplace")
        self.config_data["workplace"] = base
        roots = list(self.config_data["roots"])
        if old:
            roots = [r for r in roots if path_key(r) != path_key(old)]
        if all(path_key(r) != path_key(base) for r in roots):
            roots.insert(0, base)
        self.config_data["roots"] = roots

    def pick_workplace(self, parent):
        """弹目录选择框换默认工作区，选中就当场落盘。返回换没换。

        「换默认目录」本身不是创建动作，不该挂在哪次"建文件夹"上——早先只有
        「新建文件夹」对话框里能改，而且得点「创建」才生效，选完点「取消」就
        白选一场。所以提出来单独一件事，主界面那行和对话框那行都走这儿。

        parent 传对话框的话，选择框会挂在那个对话框上，不会被它的 grab 挡住。
        """
        chosen = filedialog.askdirectory(
            parent=parent, title="选择默认工作区目录",
            initialdir=self.config_data["workplace"] or TOOL_DIR)
        if not chosen:
            return False
        self._set_workplace(os.path.normpath(chosen))
        save_config(self.config_data)
        self.refresh_workplace_label()
        self.feedback_var.set(
            "默认工作区已改成 {}。这个目录下的子文件夹，点「重新扫描」能拉进列表。"
            .format(self.config_data["workplace"]))
        return True

    def refresh_workplace_label(self):
        self.workplace_var.set(self.config_data["workplace"])


    def rescan(self):
        added = merge_scanned(self.config_data)
        save_config(self.config_data)
        self.refresh_workspaces()
        if added:
            self.feedback_var.set("新发现 {} 个工作区：{}".format(len(added), "、".join(added)))
        else:
            self.feedback_var.set("扫描目录下没有新工作区。")
