"""顶栏：本机 claude 是哪一版、网上出到哪一版，以及启动器自己有没有新版。

从 launcher.py 里按域切出来的一块（见 设计说明-0.4.md 第三节）。跟工作区、
模型那边分开：这里量的是"要不要更新"，落在界面上的只有顶栏那两颗胶囊，
跟中间两块列表没有共享控件。

以 mixin 的形式挂在 Launcher 上，所以方法里照旧自由用 self——主窗那些实例
状态（version_var、version_queue、self_queue、_self_pill…）都还在它的
__init__ 里，一个字没动。
"""
import queue
import subprocess
import threading
from tkinter import messagebox

from claude_tool import selfupdate
from claude_tool import versions
from claude_tool import __version__
from claude_tool.claude import CREATE_NO_WINDOW, find_claude
from claude_tool.config import save_config
from claude_tool.host import open_url
from claude_tool.theme import ACCENT, ACCENT_SOFT, HOVER_BG, MUTED, PANEL_BG
from claude_tool.widgets import PillButton, Tip


class UpdateMixin:

    def _probe_claude_version(self):
        """后台问一句 `claude --version`，结果通过队列丢回主线程。

        不能直接在主线程跑：claude 那个 shim 启动要一两秒，卡在启动路径上窗口
        就成了白板。也不能在工作线程里碰 tk——tkinter 不是线程安全的，那边的
        规矩是只有主线程能改控件。所以这里只往队列里塞字符串，
        _poll_running 每次醒过来顺手把它捞出来贴上。
        """
        exe = find_claude()
        if not exe:
            self.version_queue.put("没找到 claude")
            return

        def work():
            try:
                result = subprocess.run(
                    [exe, "--version"], capture_output=True,
                    creationflags=CREATE_NO_WINDOW, timeout=20)
                # 明确按 UTF-8 解：这台机器子进程默认走 cp936，版本串里万一有
                # 非 ASCII 会解出乱码甚至抛异常。
                text = result.stdout.decode("utf-8", "replace").strip()
                if not text:
                    text = result.stderr.decode("utf-8", "replace").strip()
                # 输出可能不止一行（新版偶尔会跟一句更新提示），只留第一行。
                if text:
                    self.version_queue.put("claude " + text.splitlines()[0])
                else:
                    self.version_queue.put("读不到版本号")
            except Exception:
                self.version_queue.put("读不到版本号")

        threading.Thread(target=work, daemon=True).start()

    def check_claude_update(self):
        """手动查一次（顶栏那个「查更新」）。不管底下那个勾开没开都查。

        只管 claude。启动器自己那件事不搭这颗按钮的车——一次点击出两条结果，反馈
        栏就一行，后到的把先到的顶掉，用户只看得到一半。自己那件事 0.4 起改成启动
        即查、没有开关，也不再需要"手动查一次"这个入口。
        """
        if not self._local_version:
            self.feedback_var.set("还没读到本机 claude 的版本号，过一两秒再点一次。")
            return
        self.feedback_var.set("正在问网上 claude 出到哪版了…")
        self._start_version_check(manual=True)

    def _start_version_check(self, manual=False):
        """后台跑一次"要不要更新"，结果走 version_check_queue 回主线程。

        联网得在后台：网络不通时单是超时就是好几秒，摆到主线程上界面会僵住。
        """
        local = self._local_version

        def work():
            try:
                result = versions.check(local)
            except Exception:
                result = None
            self.version_check_queue.put((manual, result))

        threading.Thread(target=work, daemon=True).start()

    def _poll_version_check(self):
        try:
            while True:
                manual, result = self.version_check_queue.get_nowait()
                self._apply_version_check(manual, result)
        except queue.Empty:
            pass

    def _apply_version_check(self, manual, result):
        if result is None:
            # 查不到就闭嘴——离线、被墙、npm 抽风都是常事，自动那次不该弹东西出来
            # 烦人；手动那次得说一声，不然用户点完等半天不知道发生了什么。
            if manual:
                self.feedback_var.set("没查到网上 claude 出到哪版了（可能联不上网），比不了。")
            return
        latest, source, stale = result
        local_parts = versions.parse(self._local_version)
        if local_parts is None:
            # 本机版本号压根没读出来（没装 claude，或者 --version 那下挂了），
            # 那就只剩下"网上是 X"这一条，比不出新旧。
            if manual:
                self.feedback_var.set(
                    "网上最新是 {}（{}），可本机版本没读出来，比不了。".format(
                        latest, source))
            return
        # 反馈栏就一行、长了会被窗口边裁掉，所以只报版本号本身，不把那串
        # "claude 2.1.150 (Claude Code)" 整个抄一遍。
        local = versions.number(local_parts)
        if not stale:
            self._hide_update()
            if manual:
                self.feedback_var.set("本机 claude {}，{} 上也是这一版，不用更新。".format(
                    local, source))
            return
        self._show_update(latest, source, local)

    def _show_update(self, latest, source, local):
        """本机落后了：在版本号旁边摆一个「有新版」。

        胶囊是查出来落后才建的（不预先建好再 pack_forget）：它的宽度是按文字量
        出来的，版本号得先知道才能建。

        点它是开安装面板的升级模式，不是开官网：面板那边会先认这份 claude 是
        哪条路装的，把对的那条升级命令替我们填好、预选上，用户点两下就升完了。
        """
        if self._update_pill is not None:
            self._update_pill.destroy()
        pill = PillButton(self.top_line, "有新版 " + latest,
                          lambda: self._open_install_dialog(updating=True),
                          primary=True, bg=PANEL_BG)
        Tip(pill, "本机这版 claude 旧了，点开就是把升级命令给你填好的面板")
        pill.pack(side="left", padx=(10, 0))
        self._update_pill = pill
        self.feedback_var.set(
            "本机 claude {}，{} 上已经是 {} 了。点「有新版」帮你升级。".format(
                local, source, latest))

    def _hide_update(self):
        if self._update_pill is None:
            return
        self._update_pill.destroy()
        self._update_pill = None

    def _on_version_check_toggle(self):
        self.config_data["auto_version_check"] = self.auto_version_var.get()
        save_config(self.config_data)
        if not self.auto_version_var.get():
            self.feedback_var.set(
                "关掉了：启动时不再联网查版本。顶栏「查更新」随时能手动查一次。")
            return
        self.feedback_var.set(
            "开着了：开启动器时问一次网上 claude 出到哪版了，本机旧了就在版本号"
            "旁边提一句。升级命令得你自己点「有新版」去跑。")
        # 当场就问一次，不用等下次启动：不然勾上去像是没反应。
        if self._local_version:
            self._start_version_check()

    # ── 启动器自己有没有新版 ────────────────────────────────────────────────
    # 跟上面那条 claude 的流水线是两套：查的是不同东西（一个 npm、一个我们自己的
    # 官网），能做的事也不一样——claude 那边只能指路，这边能把包装下来替掉自己。

    def _start_self_check(self, manual=False):
        """后台问一次官网。结果走 self_queue 回主线程。

        跟 claude 那条一样得在后台：网络不通时单是超时就好几秒。

        manual 就是"用户自己点的"：查不到、或者已经是最新，这两种"什么事都没
        发生"的结果只有手动那次需要说出来，自动那次闭嘴。0.4 起启动即查（底下
        那个勾去掉了），所以现在没有调用方传 True——参数留着是给设置窗「关于」
        页的手动查入口用的，别顺手删。
        """
        def work():
            try:
                result = versions.launcher(__version__)
            except Exception:
                result = None
            self.self_queue.put(("check", manual, result))

        threading.Thread(target=work, daemon=True).start()

    def _poll_self_update(self):
        """self_queue 里那三种消息各自理事。取和画都在主线程。"""
        try:
            while True:
                message = self.self_queue.get_nowait()
                if message[0] == "check":
                    self._apply_self_check(message[1], message[2])
                elif message[0] == "progress":
                    self._show_download_progress(*message[1:])
                elif message[0] == "done":
                    self._downloaded(message[1])
                else:
                    self._download_failed(message[1], message[2])
        except queue.Empty:
            pass

    def _apply_self_check(self, manual, result):
        if result is None:
            # 跟 claude 那边同一个道理：查不到就闭嘴，除非是用户自己点的。
            if manual:
                self.feedback_var.set(
                    "没查到官网上最新是几版（可能联不上网），比不了。")
            return
        if not result.stale:
            self._hide_self_update()
            if manual:
                self.feedback_var.set(
                    "启动器本机是 {}，网上也是这一版，不用更新。".format(
                        __version__))
            return
        self._show_self_update(result)

    def _show_self_update(self, release):
        """网上那份比本机新：在顶栏摆一颗「下载最新版 X」。

        跟 claude 那颗「有新版」一路货：都是查出来才建（宽度按文字量，得先知道版本
        号）。查到的记录连着那颗胶囊一起记下来，下载时直接用，不再问一遍官网。
        """
        self._self_release = release
        if self._self_pill is not None:
            self._self_pill.destroy()
        pill = PillButton(self.top_line, "下载最新版 " + release.latest,
                          self._download_update, primary=True, bg=PANEL_BG)
        Tip(pill, "点一下：把这一版的安装包下到 ~/.claude_tool/downloads，"
                  "下完问你要不要现在装")
        pill.pack(side="left", padx=(10, 0))
        self._self_pill = pill
        self.feedback_var.set(
            "启动器本机是 {}，官网上已经出到 {} 了。".format(
                __version__, release.latest))

    def _hide_self_update(self):
        if self._self_pill is None:
            return
        self._self_pill.destroy()
        self._self_pill = None

    def _download_update(self):
        """胶囊点下去：后台把包装下来。下载中不再理第二下。"""
        if self._downloading or self._self_release is None:
            return
        release = self._self_release
        self._downloading = True
        self.feedback_var.set("正在下载 {} …".format(release.latest))
        seen = [-1]        # 上一个报过的百分比，只在这三个数变了才往队列里塞

        def work():
            def progress(done, total):
                percent = int(done * 100 / total) if total else -1
                if percent != seen[0]:
                    seen[0] = percent
                    self.self_queue.put(("progress", done, total))
            try:
                path = selfupdate.download(release, progress)
            except LookupError as error:
                # 这一版压根没给我们这个系统打包，跟"网断了"是两回事
                self.self_queue.put(("failed", True, str(error)))
            except Exception as error:
                self.self_queue.put(("failed", False, str(error)))
            else:
                self.self_queue.put(("done", path))

        threading.Thread(target=work, daemon=True).start()

    def _show_download_progress(self, done, total):
        if total:
            self.feedback_var.set("正在下载启动器最新版… {}%（{:.1f} / {:.1f} MB）".format(
                int(done * 100 / total), done / 1048576.0, total / 1048576.0))
        else:
            self.feedback_var.set("正在下载启动器最新版… {:.1f} MB".format(
                done / 1048576.0))

    def _download_failed(self, missing, reason):
        self._downloading = False
        if missing:
            # 这一版还没给我们这个系统打包——不是故障，是我们自己还没打，别写成
            # "下载失败"吓人。官网上有下载页，问一句要不要开过去。
            if messagebox.askyesno("这版还没给你的系统打包",
                                   "{}\n\n要去官网的下载页看一眼吗？".format(reason)):
                try:
                    open_url(versions.SITE)
                except OSError:
                    pass
                self.feedback_var.set("官网下载页：{}".format(versions.SITE))
            else:
                self.feedback_var.set("{}。".format(reason))
            return
        messagebox.showerror("下载没成",
                             "启动器最新版的包没下下来：\n\n{}".format(reason))
        self.feedback_var.set("下载没成，看弹窗里那句。")

    def _downloaded(self, path):
        """包已经在盘上了。Windows 上是安装包——装它就等于替掉正在跑的这个程序，
        所以得先把启动器关掉；别处那个是 tar.gz，没有安装器，弹个文件夹拉倒。"""
        self._downloading = False
        if not selfupdate.INSTALLER:
            selfupdate.open_artifact(path)
            self.feedback_var.set(
                "下好了：{}\n已经弹出它所在的文件夹，解开就能用。".format(path))
            return
        if not messagebox.askyesno(
                "下好了，现在装吗",
                "新版安装包下好了：\n{}\n\n"
                "装它得先把启动器关掉——安装程序要覆写的就是正在跑的这些文件。\n"
                "（内嵌终端里那个 claude 会跟着一起关；开在独立窗口里的不受影响。）\n\n"
                "现在关掉启动器并运行安装包吗？选「否」的话文件留在那儿，"
                "你自己双击也行。".format(path)):
            self.feedback_var.set("安装包放在 {}，想装的时候双击它。".format(path))
            return
        try:
            selfupdate.open_artifact(path)
        except OSError as error:
            messagebox.showerror("拉不起来", "安装包没能跑起来：\n{}".format(error))
            return
        # 让安装程序先站稳再关自己：这边一 destroy，进程就没了。
        self.after(800, self._on_close)

    def _update_model_chip(self, text, known):
        """模型名胶囊。text 是要显示的字，known 是说这个名字是不是一个真预设。

        配色跟着 known 走：认得出来的预设才用强调色，其余一律中性灰——没有预设
        和"预设已经对不上现在这份 settings.json"都得是灰的。空状态长得像链接或者
        报错都是误导，用户会以为点它有用、或者以为哪里错了。
        """
        self.status_var.set(text)
        self.model_chip.configure(bg=ACCENT_SOFT if known else HOVER_BG,
                                  fg=ACCENT if known else MUTED)
