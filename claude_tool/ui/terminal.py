"""内嵌终端：把 conhost 贴在右栏、跟着窗口挪、断开关联。

从 launcher.py 里按域切出来的一块（见 设计说明-0.4.md 第三节）。跟会话是一对
——launch_workspace 决定"这次塞进窗口还是另开一扇"，塞的话交给这儿的
embed_workspace。

只在 Windows 上有（host.EMBED_SUPPORTED）。别处这些方法根本不会被调到——勾都
不摆，但代码留着，行为跟拆分前一致。

以 mixin 的形式挂在 Launcher 上——主窗那些实例状态（embedded、term_head、
term_holder、panel、_width_before_embed…）都还在它的 __init__ / _build_ui 里。
"""
from tkinter import messagebox

from claude_tool.config import save_config
from claude_tool.handoff import AUTO_CONTINUE_MAX, AUTO_CONTINUE_RESET
from claude_tool.host import (
    EmbeddedConsole,
    fresh_console,
    place_window,
    screen_bounds,
    spawn_console,
    window_position,
)
from claude_tool.theme import SIDE_GAP


class TerminalMixin:

    # ── 内嵌终端 ──

    def _on_embed_toggle(self):
        """这个勾是"新开的会话塞不塞进本窗口"的偏好，跟自动刷文档那个一样记下来。

        取消勾选只是把当前嵌着的那个放回独立窗口，不打断它——所以这不是"关掉
        终端"，下一次启动照样按这个勾决定。
        """
        self.config_data["embed"] = self.embed_var.get()
        save_config(self.config_data)
        if not self.embed_var.get():
            self.detach_terminal()

    def _on_auto_continue_toggle(self):
        self.config_data["auto_continue"] = self.auto_continue_var.get()
        save_config(self.config_data)
        if self.auto_continue_var.get():
            self.feedback_var.set(
                "已挂上 Stop hook：它停下问话时替它接一句「接着干，自己定」，连着推 "
                "{} 轮就放行；隔 {} 分钟重新数，它自己说「已完成」也会停。".format(
                    AUTO_CONTINUE_MAX, AUTO_CONTINUE_RESET // 60))
        else:
            self.feedback_var.set("新开的会话不再自动继续。")

    def embed_workspace(self, item, cont=False, prompt=None, settings=None,
                        permission=None):
        """把 claude 塞进本窗口。已经有一个的话先把它放出去，不打断它。"""
        if self._pending:
            self.feedback_var.set("上一个还在启动，稍等一下。")
            return
        self.detach_terminal()
        try:
            process, known = spawn_console(item["path"], cont, prompt, settings,
                                           permission)
        except Exception as e:
            messagebox.showerror("启动失败", "启动 claude 失败：\n{}".format(e))
            return
        self._pending = (process, known, item, settings, 0)
        self.feedback_var.set("正在把 claude 装进窗口…")
        self.after(80, self._poll_console)

    def _poll_console(self):
        """控制台窗口是异步建的，拿到之前一直轮询，别把界面卡住。"""
        process, known, item, settings, tries = self._pending
        hwnd = fresh_console(known)
        if hwnd is None:
            if tries >= 75:
                self._pending = None
                self.feedback_var.set("等不到控制台窗口，claude 可能已经退出了。")
                return
            self._pending = (process, known, item, settings, tries + 1)
            self.after(80, self._poll_console)
            return

        self._pending = None
        self.term_name_var.set(item["path"])
        self.term_head.pack(fill="x", pady=(0, 6))
        self.term_holder.pack(fill="both", expand=True)
        self._room_for_terminal(True)
        self.update_idletasks()
        self.embedded = EmbeddedConsole(process, hwnd, self.term_holder)
        self.track_running(item["name"], item["path"], process)
        self.feedback_var.set(
            "claude 已内嵌在 {}。conhost 没有字体回退，个别符号会是方框。"
            .format(item["path"]))

    def _room_for_terminal(self, want):
        """内嵌时右边多长出一栏，左边那列一个像素都不动；退出去还原。

        早先的写法是把左列收窄到 SIDE_WIDTH 再让终端占剩下的，结果一内嵌，
        主界面就被重新排了一遍版：工作区那几行本来挤着路径和四个按钮，一窄
        全被截成「De..上次聊…」。终端要地方就从右边往外长、窗口跟着变宽，
        前头那半张脸原样留着——这才是用户要的「不影响本来的布局」。

        宽度这块只有一个出处：先摆好栏，再让 _apply_min_size 把它算进下限，窗口
        就照那个下限变宽/还原。这样手动缩窗口也缩不过去，左列始终是原来那么宽。
        """
        if want:
            self._width_before_embed = self.winfo_width()
            self.panel.pack(side="left", fill="both", expand=False,
                            padx=(SIDE_GAP, 0))
            self._apply_min_size()
            self._widen(self.minsize()[0])
        else:
            was = self._width_before_embed
            self.panel.pack_forget()
            self._width_before_embed = None
            self._apply_min_size()
            if was:
                self._widen(was)
        self.ws_list.fit()

    def _widen(self, width):
        """改宽度，并保证整扇窗口还留在虚拟桌面内——贴到右边缘就往左挪。"""
        if not width:
            return
        left, _top, right, _bottom = screen_bounds(self)
        width = min(width, right - left)
        x, y = window_position(self)
        if x + width > right:
            x = max(left, right - width)
        self.geometry("{}x{}".format(width, self.winfo_height()))
        self.update_idletasks()
        place_window(self, x, y)

    def detach_terminal(self):
        """把内嵌的控制台放回独立窗口，进程不动。"""
        if not self.embedded:
            return
        try:
            self.embedded.detach()
        except Exception:
            pass
        self.embedded = None
        self._hide_terminal()

    def close_terminal(self):
        """关掉内嵌的这个会话——等同点它标题栏的 X。

        先好好说：给控制台发 WM_CLOSE，claude 有工夫把手头的活收尾。三秒还没
        退就掐掉 conhost，那里面的 cmd 和 claude 会跟着一起走；不然按钮按下去
        可能半天没动静，看着像坏的。
        """
        embedded, self.embedded = self.embedded, None
        if embedded is None:
            return
        try:
            embedded.close()
        except Exception:
            pass
        process = getattr(embedded, "process", None)
        self._hide_terminal()
        self.feedback_var.set("正在关掉这个会话…")
        if process is not None:
            self.after(3000, lambda: process.poll() is None and process.terminate())

    def _hide_terminal(self):
        """把右边那栏收回去，宽度还原。进程的事调用方自己管。"""
        self.term_head.pack_forget()
        self.term_holder.pack_forget()
        self.term_name_var.set("")
        self._room_for_terminal(False)

    def _on_window_move(self, event):
        """本窗口自己挪了：把内嵌的那个重新贴到终端栏上。

        子控件的事件也会流到这儿（顶层窗口在 bindtags 里占一环），所以拿
        event.widget 挡一下——只有它就是本窗口时才是真挪了窗口，不然列表里
        随便哪个控件动一下都要白贴一遍。
        """
        if event.widget is not self or self.embedded is None or self._placing:
            return
        self._placing = True
        try:
            self.embedded.place()
        finally:
            self._placing = False

    def _on_term_resize(self, _event):
        """终端栏自己变了尺寸（缩放窗口、或者右边这栏刚长出来）。"""
        if self.embedded and not self._placing:
            self._placing = True
            try:
                self.embedded.place()
            finally:
                self._placing = False
