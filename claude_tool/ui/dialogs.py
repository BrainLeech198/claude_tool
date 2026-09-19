"""那几个对话框：加模型、加工作区、点工作区那次询问、布置托管任务。

以 mixin 的形式挂在 Launcher 上。它们跟主窗口共享一大堆状态（config_data、
feedback_var、refresh_*），所以没有把状态收进单独的对象——那要挨个改方法体，
跟"只是把文件切开"是两码事。真要做，应该连同 ui/launcher.py 一起，按面板
（模型面板 / 工作区面板 / 会话面板）重新划分职责，而不是先拆对话框。
"""
import json
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from claude_tool.paths import (
    ILLEGAL_CHARS,
    PRESET_DIR,
    SETTINGS_FILE,
    TOOL_DIR,
    WORKPLACE_DIR,
)
from claude_tool.theme import (
    ACCENT,
    BORDER,
    MUTED,
    PAGE_BG,
    PANEL_BG,
    TEXT,
    WARN,
    font,
)
from claude_tool.permissions import (
    PERMISSION_VALUES,
    permission_hint,
    permission_option,
    workspace_permission,
)
from claude_tool.presets import (
    IMPORT_ALREADY,
    IMPORT_NO_ENV,
    IMPORT_NO_FILE,
    PROVIDERS,
    discover_presets,
    host_of,
    preset_path,
    probe_current_settings,
    read_env,
    suggest_preset_name,
)
from claude_tool.config import path_key, save_config
from claude_tool.handoff import HANDOFF_FILE, READ_HANDOFF_PROMPT
from claude_tool.widgets import PillButton, finish_form, make_entry, make_form


class LauncherDialogs:

    def _open_model_dialog(self, edit=None):
        """edit 传 (名称, 路径) 是编辑，传 None 是新增，两者共用一套表单。"""
        old_name, old_path = edit or (None, None)
        is_edit = old_path is not None
        env = read_env(old_path) if is_edit else {}

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "编辑模型" if is_edit else "添加模型")

        # 每个字段除了术语本身，再带一句人话备注：懂的人看标签，不懂的看备注，
        # 谁也不必将就。
        fields = [
            ("预设名称", "列表里显示的名字，随便起", "name", False),
            ("Base URL", "服务商的接口地址，上面挑一家会自动填", "base_url", False),
            ("API Token", "你在这家后台拿到的密钥", "token", True),
            ("模型名", "具体用哪款，挑完供应商会自动填", "model", False),
        ]
        values = {"name": old_name or "", "base_url": env.get("ANTHROPIC_BASE_URL", ""),
                  "token": env.get("ANTHROPIC_AUTH_TOKEN", ""),
                  "model": env.get("ANTHROPIC_MODEL", "")}

        row = 0
        hint_var = tk.StringVar(
            value="从下拉里挑一个供应商，地址和模型名会自动填好，你只要贴自己的 key。")

        # 常用供应商：选一个就把 Base URL 和模型名填好，用户只剩贴 key 这件事。
        picked = tk.Frame(body, bg=PAGE_BG)
        picked.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(picked, text="常用供应商", bg=PAGE_BG, fg=MUTED, font=font(9),
                 ).pack(side="left", padx=(0, 6))
        entries = {}
        provider_var = tk.StringVar()
        combo = ttk.Combobox(picked, textvariable=provider_var, state="readonly",
                             width=30, font=font(10),
                             values=["（自己填）"] + [p[0] for p in PROVIDERS])
        combo.current(0)
        combo.pack(side="left")

        # 记住名字框里那个名字是不是下拉给填的：是的话换一家就跟着换，
        # 用户自己敲过的就不动。免得挑完 DeepSeek 改挑 OpenRouter，名字还留着 DeepSeek。
        auto_name = [""]

        def pick(_event=None):
            """选中哪个就把那家的地址/模型名灌进对应的框。"""
            for short, label, base, model in PROVIDERS:
                if short != provider_var.get():
                    continue
                entries["base_url"][0].set(base)
                entries["model"][0].set(model)
                current = entries["name"][0].get().strip()
                if not current or current == auto_name[0]:
                    entries["name"][0].set(label)
                    auto_name[0] = label
                entries["token"][1].focus_set()
                hint_var.set(
                    "已填好 {} 的地址（{}）和模型名 {}。贴上你的 key；"
                    "模型名各家会变，不对就改一下再点「测试」验。"
                    .format(short, host_of(base), model))
                return

        combo.bind("<<ComboboxSelected>>", pick)
        if is_edit:
            # 编辑已有预设时，地址对得上哪家就把下拉停在哪家
            for index, provider in enumerate(PROVIDERS, start=1):
                if values["base_url"].rstrip("/") == provider[2]:
                    combo.current(index)
                    break
        row += 1

        for label, hint, key, secret in fields:
            cell = tk.Frame(body, bg=PAGE_BG)
            cell.grid(row=row, column=0, sticky="w", pady=4)
            tk.Label(cell, text=label, bg=PAGE_BG, fg=TEXT, font=font(10),
                     anchor="w").pack(anchor="w")
            tk.Label(cell, text=hint, bg=PAGE_BG, fg=MUTED, font=font(9),
                     anchor="w").pack(anchor="w")
            var = tk.StringVar(value=values[key])
            entry = make_entry(body, var, width=40, secret=secret)
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)
            entries[key] = (var, entry)
            if key == "token":
                shown = tk.BooleanVar(value=False)

                def toggle(v=shown, e=entry):
                    e.configure(show="" if v.get() else "*")

                tk.Checkbutton(body, text="显示", variable=shown, command=toggle,
                               bg=PAGE_BG, fg=MUTED, font=font(9),
                               activebackground=PAGE_BG, selectcolor=PANEL_BG,
                               highlightthickness=0, bd=0,
                               ).grid(row=row, column=2, sticky="w", padx=(8, 0))
            row += 1
        body.columnconfigure(1, weight=1)

        tk.Label(body, textvariable=hint_var, bg=PAGE_BG, fg=MUTED, font=font(9),
                 justify="left", anchor="w", wraplength=470,
                 ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        row += 1

        # 改一个当前没在用的预设时，默认不去动正在生效的 settings.json
        apply_now = tk.BooleanVar(value=(not is_edit) or old_name == self.active_name)
        tk.Checkbutton(body, text="保存后立即启用" if is_edit else "添加后立即启用",
                       variable=apply_now, bg=PAGE_BG, fg=TEXT, font=font(10),
                       activebackground=PAGE_BG, selectcolor=PANEL_BG,
                       highlightthickness=0, bd=0,
                       ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row += 1

        note = "改名会一并重命名那份配置文件；" if is_edit else "会存成一份配置文件；"
        tk.Label(body, text=note + "就在 ~/.claude_tool/claude_settings/ 里，"
                                  "以后想手改也行。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                 ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))

        save = lambda: self._save_model(dialog, entries, apply_now, edit)
        finish_form(dialog, [("保存", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        entries["name"][1].focus_set()
        self._center(dialog)


    def _save_model(self, dialog, entries, apply_now, edit):
        _old_name, old_path = edit or (None, None)
        name = entries["name"][0].get().strip()
        base_url = entries["base_url"][0].get().strip()
        token = entries["token"][0].get().strip()
        model = entries["model"][0].get().strip()

        if not all([name, base_url, token, model]):
            messagebox.showerror("缺少信息", "四个字段都要填。", parent=dialog)
            return
        if re.search(ILLEGAL_CHARS, name):
            messagebox.showerror("名称非法",
                                 "名称不能包含 < > : \" / \\ | ? * 和空格。", parent=dialog)
            return

        target = preset_path(name)
        renamed = old_path is not None and os.path.normcase(target) != os.path.normcase(old_path)
        if (old_path is None or renamed) and os.path.exists(target):
            messagebox.showerror("名称冲突", "预设「{}」已存在。".format(name), parent=dialog)
            return

        preset = self._preset_template()
        if old_path is not None:
            try:
                with open(old_path, "r", encoding="utf-8") as f:
                    preset = json.load(f)
            except Exception:
                pass
        preset["env"] = {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_MODEL": model,
        }

        try:
            os.makedirs(PRESET_DIR, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(preset, f, ensure_ascii=False, indent=2)
            if renamed:
                os.remove(old_path)
        except Exception as e:
            messagebox.showerror("保存失败", "写入 {} 失败：\n{}".format(target, e), parent=dialog)
            return

        dialog.destroy()
        if apply_now.get():
            self.switch_model(name, target)
        else:
            self.feedback_var.set(
                "{}模型：{}".format("已更新" if old_path else "已新增", name))
            self.refresh_models()


    def _preset_template(self):
        """从现有预设继承 env 之外的公共设置，作为新预设的模板。"""
        presets = discover_presets()
        candidates = []
        if self.active_name and self.active_name in presets:
            candidates.append(presets[self.active_name])
        candidates.extend(presets.values())
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {k: v for k, v in data.items() if k != "env"}
            except Exception:
                continue
        return {
            "enabledPlugins": {},
            "language": "简体中文",
            "skipDangerousModePermissionPrompt": True,
        }


    # ── 导入当前配置 ──

    def _import_current(self):
        """把 Claude Code 正在用的那份配置一键存成预设。

        新手最常见的开局是"我已经在用 claude 了"，可让他到别处把 Base URL 和
        key 抄进表单纯属折腾——那两样本来就躺在 settings.json 里。全程只读那份
        文件；会写它的地方全项目只有 switch_model() 一处，而且是有意的覆盖。
        """
        status, payload = probe_current_settings()
        if status == IMPORT_NO_FILE:
            messagebox.showinfo(
                "还没用过 Claude Code",
                "这台机器上还没有 Claude Code 的配置：\n{}\n\n"
                "先在别的地方用一次 claude（或者点顶上的「一键安装」），"
                "再回来点这里。".format(SETTINGS_FILE), parent=self)
            return
        if status == IMPORT_NO_ENV:
            messagebox.showinfo(
                "不用导入",
                "这份配置里没有第三方服务商的地址和密钥，说明你用的是"
                "官方账号登录。\n\n这种情况不用建模型，直接点右边的工作区就能开跑。",
                parent=self)
            return
        if status == IMPORT_ALREADY:
            messagebox.showinfo(
                "已经在用了",
                "当前生效的就是预设「{}」，不用再导一份。".format(payload), parent=self)
            return
        self._ask_import_name(payload)

    def _ask_import_name(self, payload):
        """导入前确认名字——列表里那张卡片就靠它认人。"""
        env = payload.get("env") or {}
        base = str(env.get("ANTHROPIC_BASE_URL") or "")
        model = str(env.get("ANTHROPIC_MODEL") or "")

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "导入当前配置")

        tk.Label(body, text="从 Claude Code 正在用的配置里读到的：", bg=PAGE_BG,
                 fg=MUTED, font=font(9), anchor="w",
                 ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(body, text="{}  ·  {}".format(host_of(base), model), bg=PAGE_BG,
                 fg=TEXT, font=font(10), anchor="w", justify="left",
                 wraplength=430).grid(row=1, column=0, columnspan=2, sticky="w",
                                      pady=(2, 14))

        tk.Label(body, text="预设名称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        name_var = tk.StringVar(value=suggest_preset_name(env) or "")
        entry = make_entry(body, name_var, width=34)
        entry.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=4)
        body.columnconfigure(1, weight=1)

        hint = tk.StringVar(value="以后在列表里就认这个名字。")
        tk.Label(body, textvariable=hint, bg=PAGE_BG, fg=MUTED, font=font(9),
                 justify="left", anchor="w", wraplength=440,
                 ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def save():
            name = name_var.get().strip()
            if not name:
                hint.set("给它起个名字。")
                return
            if re.search(ILLEGAL_CHARS, name):
                hint.set("名字里不能有 < > : \" / \\ | ? * 和空格。")
                return
            target = preset_path(name)
            if os.path.exists(target):
                hint.set("已经有一个叫「{}」的预设了，换个名字。".format(name))
                return
            try:
                os.makedirs(PRESET_DIR, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("保存失败",
                                     "写入 {} 失败：\n{}".format(target, e),
                                     parent=dialog)
                return
            dialog.destroy()
            # 不调 switch_model：存下的内容跟 settings.json 逐字一样，本来就是当前
            # 生效的那份。刷新一下列表，它自己会亮成"当前"。
            self.feedback_var.set("已把当前在用的配置存成预设「{}」。".format(name))
            self.refresh_models()

        finish_form(dialog, [("保存", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        entry.focus_set()
        self._center(dialog)


    def open_workspace(self, item):
        """点工作区先问一句：新开一个还是接着上次聊，要不要先读交接文档。"""
        path = item["path"]
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path))
            return

        handoff = os.path.join(path, HANDOFF_FILE)
        has_handoff = os.path.isfile(handoff)

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, item["name"])

        tk.Label(body, text=path, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                 justify="left", wraplength=430).grid(row=0, column=0, columnspan=2,
                                                      sticky="w")

        read_var = tk.BooleanVar(value=has_handoff)
        if has_handoff:
            tk.Checkbutton(body, text="先读交接文档 " + HANDOFF_FILE + "，接着上次的进度干",
                           variable=read_var, bg=PAGE_BG, fg=TEXT, font=font(10),
                           activebackground=PAGE_BG, selectcolor=PANEL_BG,
                           highlightthickness=0, bd=0, anchor="w",
                           ).grid(row=1, column=0, columnspan=2, sticky="w",
                                  pady=(12, 0))
        row_after = 2 if has_handoff else 1

        # 权限等级：预选这个工作区上次用的那档，选了就记回工作区条目里。
        # 按下的永远是下拉里那一行的下标，真正的值（claude 要的那串英文）从
        # PERMISSION_VALUES 现取——界面上摆的字和传给 claude 的字是两回事。
        #
        # 选中项只认 combobox 自己的 current()，不给它挂 StringVar。从前挂过，
        # 那变量是这函数里的局部变量，函数一返回就被 GC 掉，而 tkinter 的
        # Variable 销毁时会顺手 unset 掉底层那个 Tcl 变量：下拉于是变空白、
        # current() 变 -1，每次都得自己重选。控件自己的状态没这个寿命问题。
        perm_row = tk.Frame(body, bg=PAGE_BG)
        perm_row.grid(row=row_after, column=0, columnspan=2, sticky="ew",
                      pady=(12, 0))
        tk.Label(perm_row, text="权限", bg=PAGE_BG, fg=MUTED,
                 font=font(10)).pack(side="left")
        perm_combo = ttk.Combobox(
            perm_row, state="readonly", width=30, font=font(10),
            values=[permission_option(mode) for mode in PERMISSION_VALUES])
        perm_combo.current(PERMISSION_VALUES.index(workspace_permission(item)))
        perm_combo.pack(side="left", padx=(10, 0))
        # 命令行参数和解释分两行：熟练用户认的是 --permission-mode 后面那串英文，
        # 中文那句他只当注释看，两行各给各的。合在一行会超宽折行，换档时对话框
        # 还会跟着跳高度。
        perm_flag = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w")
        perm_flag.grid(row=row_after + 1, column=0, columnspan=2, sticky="w",
                       pady=(4, 0))
        # wraplength 给到 470：最长那句（auto）量出来 435px，卡在 430 上就会把
        # 句末那个句号甩到第二行去。
        perm_hint = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9),
                             justify="left", anchor="w", wraplength=470)
        perm_hint.grid(row=row_after + 2, column=0, columnspan=2, sticky="w",
                       pady=(2, 0))

        def picked_permission():
            """下拉这一格现在选的是哪个值。

            current() 认不出来就是 -1，而 PERMISSION_VALUES[-1] 取的是数组尾巴
            ——正好是"什么都不问"。宁可退回最保守那档，也不能一失手把权限全开。
            """
            return PERMISSION_VALUES[max(perm_combo.current(), 0)]

        def show_permission(value):
            perm_flag.configure(text="命令行参数 --permission-mode " + value)
            perm_hint.configure(text=permission_hint(value))

        def pick_permission(_event=None):
            show_permission(picked_permission())

        show_permission(workspace_permission(item))
        perm_combo.bind("<<ComboboxSelected>>", pick_permission)
        row_after += 3

        tk.Label(body, text="没读过的目录就选「开新会话」。想回到上次那段对话就选"
                            "「接着上次聊」——它接的是这个话题里最近的一次会话。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), justify="left", anchor="w",
                 wraplength=430,
                 ).grid(row=row_after, column=0, columnspan=2, sticky="w",
                        pady=(12, 0))

        def go(cont):
            prompt = READ_HANDOFF_PROMPT if read_var.get() else None
            # 记下这次挑的权限等级，下次点这个工作区预选它。变了才落盘。
            chosen = picked_permission()
            if chosen != item.get("permission"):
                item["permission"] = chosen
                if item in self.config_data["workspaces"]:
                    save_config(self.config_data)
            dialog.destroy()
            self.launch_workspace(item, cont=cont, prompt=prompt)

        finish_form(dialog, [("接着上次聊", lambda: go(True), True),
                             ("开新会话", lambda: go(False), False)])
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dialog.bind("<Return>", lambda e: go(True))
        self._center(dialog)


    def _open_add_workspace_picker(self):
        """「＋ 添加工作区」：先问一句是新建一个文件夹，还是把已有的目录加进来。

        从前这是并排的两个按钮（「＋ 新建文件夹」「＋ 选已有目录」）。两个词都得
        读完才分得清差别，而且「新建文件夹」那个说法听着像文件管理，不像"弄一个
        能干活的工作区"。拆成一步问，每个选项自己带一句解释。
        """
        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "添加工作区")

        def pick(opener):
            dialog.destroy()
            opener()

        choices = [
            ("新建一个文件夹",
             "在默认工作区目录下面建一个新的，建好就能用。",
             self._open_quick_workspace_dialog),
            ("选已有目录",
             "把机器上已经有的一个目录加进列表。",
             self._open_add_workspace_dialog),
        ]
        for index, (caption, hint, opener) in enumerate(choices):
            line = tk.Frame(body, bg=PAGE_BG)
            line.grid(row=index, column=0, sticky="ew", pady=(0, 10))
            PillButton(line, caption, lambda o=opener: pick(o),
                       primary=index == 0, bg=PAGE_BG).pack(side="left")
            tk.Label(line, text=hint, bg=PAGE_BG, fg=MUTED, font=font(9),
                     anchor="w").pack(side="left", padx=(10, 0))

        finish_form(dialog, [("取消", dialog.destroy, False)])
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        self._center(dialog)


    def _open_autonomy_dialog(self):
        """「AI 托管」：挑工作区、写清要它干什么、定托管到哪一档。

        三档里只有第 1 档做出来了，另外两档灰着摆在那儿——这条路往上还有什么，
        用户得看得见，不能点。灰着比藏起来好：藏起来他会以为这工具就这么点本事。
        """
        workspaces = self.config_data["workspaces"]
        if not workspaces:
            messagebox.showinfo("还没有工作区", "先加一个工作区，托管得有个目录在里头干活。")
            return

        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "AI 托管")

        # 上次托管在哪个工作区就停在哪一格，不用每次重挑；那个工作区被移掉了
        # 就退回第一个。
        remembered = self.config_data.get("autonomy_workspace") or ""
        start = next((i for i, w in enumerate(workspaces)
                      if path_key(w["path"]) == path_key(remembered)), 0)

        tk.Label(body, text="工作区", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        # 不给它挂 StringVar，理由跟 open_workspace 那个下拉一样：局部变量一被
        # GC，tkinter 顺手就把底下的 Tcl 变量 unset 掉，下拉会自己变空白。
        combo = ttk.Combobox(body, state="readonly", width=38, font=font(10),
                             values=[item["name"] for item in workspaces])
        combo.current(start)
        combo.grid(row=0, column=1, sticky="w", padx=(12, 0), pady=4)

        # 名字可能重（用户自己起的），底下这行把路径摆出来，选的是哪个一目了然。
        # 换个工作区就跟着换，所以要绑在下拉上重算。
        where = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                         justify="left", wraplength=460)
        where.grid(row=1, column=0, columnspan=2, sticky="w")

        # 权限那句是必须写的：托管是要人走开的，而"它问你话"和"它等你点允许"
        # 是两种停顿——Stop hook 只接得住前一种。等级不够高的话，用户走开一趟
        # 回来会发现它卡在权限确认上，那不是这功能失灵，是权限的事，得说在前面。
        perm_note = tk.Label(body, bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                             justify="left", wraplength=460)
        perm_note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        def show_where(_event=None):
            item = workspaces[max(combo.current(), 0)]
            exists = os.path.isdir(item["path"])
            where.configure(
                text=item["path"] + ("" if exists else "（这个目录不在了）"),
                fg=MUTED if exists else WARN)
            perm_note.configure(
                text="权限：{}。它等你点「允许」时 hook 接不了，想让它一路跑到底，"
                     "先把这个工作区的等级调高。".format(
                         permission_option(workspace_permission(item))))

        combo.bind("<<ComboboxSelected>>", show_where)
        show_where()

        tk.Label(body, text="任务目标", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=3, column=0, sticky="nw", pady=(12, 4))
        goal = tk.Text(body, height=5, width=38, wrap="word", font=font(10),
                       bg=PANEL_BG, fg=TEXT, relief="flat", insertbackground=TEXT,
                       highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=ACCENT)
        goal.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=(12, 4))
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="它拿到的就是这段话，写得像个交接：要做什么、"
                            "做完算什么样。",
                 bg=PAGE_BG, fg=MUTED, font=font(9), anchor="w",
                 ).grid(row=4, column=0, columnspan=2, sticky="w")

        tk.Label(body, text="托管程度", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=5, column=0, columnspan=2, sticky="w",
                                  pady=(12, 4))
        tier_var = tk.IntVar(value=self.config_data.get("autonomy", 1))
        # 一档两行：单选按钮那行只说这档叫什么，底下缩进去一行说它到底干什么。
        # 全塞进按钮文字里的话，长句子会绕在单选圈旁边折成好几行，读起来是一团。
        tiers = [
            (1, False, "第 1 档 · 让它自己定",
             "它停下来问你的时候，替它回一句「接着干，自己定」，不回来烦你。"
             "会多用 token。（它摆选项框问你的那种还接不了。）"),
            (2, True, "第 2 档 · 接指挥模型（还没做）",
             "让另一个模型读一遍上下文，替你回答它问的那个问题。"),
            (3, True, "第 3 档 · 半指挥（还没做）",
             "平时让它自己跑，碰上改 git 历史、大范围重做这种，停下来等你。"),
        ]
        for index, (number, not_yet, caption, hint) in enumerate(tiers):
            base = 6 + index * 2
            tk.Radiobutton(body, text=caption, variable=tier_var, value=number,
                           state="disabled" if not_yet else "normal",
                           bg=PAGE_BG, fg=TEXT, activebackground=PAGE_BG,
                           selectcolor=PANEL_BG, font=font(10), anchor="w",
                           highlightthickness=0, bd=0, disabledforeground=MUTED,
                           ).grid(row=base, column=0, columnspan=2, sticky="w",
                                  pady=(0 if index == 0 else 8, 0))
            tk.Label(body, text=hint, bg=PAGE_BG, fg=MUTED, font=font(9),
                     anchor="w", justify="left", wraplength=420,
                     ).grid(row=base + 1, column=0, columnspan=2, sticky="w",
                            padx=(22, 0))

        def start():
            item = workspaces[max(combo.current(), 0)]
            task = goal.get("1.0", "end").strip()
            if not task:
                messagebox.showerror("还没写任务",
                                     "说一下要它干什么，空着开出去它也不知道该干嘛。",
                                     parent=dialog)
                return
            if not os.path.isdir(item["path"]):
                messagebox.showerror("目录不存在", "找不到目录：\n{}".format(item["path"]),
                                     parent=dialog)
                return
            dialog.destroy()
            self.start_autonomy(item, task, tier_var.get())

        finish_form(dialog, [("开始托管", start, True), ("取消", dialog.destroy, False)])
        # 不绑 <Return>：任务目标是个多行框，回车该在那儿换行。
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        goal.focus_set()
        self._center(dialog)


    def _open_quick_workspace_dialog(self):
        """「新建一个文件夹」：在默认工作区目录下面建一个新文件夹，当工作区用。

        底下那行「建在 <路径> [更改目录]」是改默认目录的地方——以前主窗口上还
        并排摆着一行一样的，那行撤了，改到这儿改。
        """
        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "新建工作区文件夹")

        tk.Label(body, text="文件夹名称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        folder_var = tk.StringVar()
        folder_entry = make_entry(body, folder_var, width=32)
        folder_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        tk.Label(body, text="简称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        name_var = tk.StringVar()
        make_entry(body, name_var, width=32).grid(row=1, column=1, sticky="ew",
                                                  padx=(12, 0), pady=4)
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="简称留空就用文件夹名。", bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").grid(row=2, column=0, columnspan=2,
                                                sticky="w", pady=(2, 0))

        base_var = tk.StringVar(value=self.config_data["workplace"])
        line = tk.Frame(body, bg=PAGE_BG)
        line.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        tk.Label(line, text="建在", bg=PAGE_BG, fg=MUTED, font=font(9),
                 ).pack(side="left")
        tk.Label(line, textvariable=base_var, bg=PAGE_BG, fg=TEXT, font=font(9),
                 anchor="w").pack(side="left", padx=(6, 8), fill="x", expand=True)

        def choose_base():
            chosen = filedialog.askdirectory(parent=dialog, title="选择默认工作区目录",
                                             initialdir=base_var.get() or TOOL_DIR)
            if chosen:
                base_var.set(os.path.normpath(chosen))

        PillButton(line, "更改目录", choose_base, bg=PAGE_BG).pack(side="right")

        save = lambda: self._save_quick_workspace(dialog, folder_var, name_var, base_var)
        finish_form(dialog, [("创建", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        folder_entry.focus_set()
        self._center(dialog)


    def _save_quick_workspace(self, dialog, folder_var, name_var, base_var):
        folder = folder_var.get().strip()
        if not folder:
            messagebox.showerror("缺少名称", "请填写文件夹名称。", parent=dialog)
            return
        if re.search(ILLEGAL_CHARS, folder):
            messagebox.showerror(
                "名称非法",
                "文件夹名称不能包含 < > : \" / \\ | ? * 和空格。", parent=dialog)
            return

        base = os.path.normpath(base_var.get().strip() or WORKPLACE_DIR)
        target = os.path.join(base, folder)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            messagebox.showerror("创建失败", "建不了目录：\n{}".format(e), parent=dialog)
            return
        if not os.path.isdir(target):
            messagebox.showerror("创建失败", "目录没建起来：\n{}".format(target),
                                 parent=dialog)
            return

        name = name_var.get().strip() or folder
        known = {path_key(w["path"]) for w in self.config_data["workspaces"]}
        if path_key(target) not in known:
            self.config_data["workspaces"].append({"name": name, "path": target})

        self._set_workplace(base)
        save_config(self.config_data)
        dialog.destroy()
        self.refresh_workspaces()
        self.feedback_var.set("已建好工作区 {}：{}".format(name, target))


    def _open_add_workspace_dialog(self):
        """「选已有目录」：挑一个已经存在的目录，加进列表里当工作区。"""
        dialog = tk.Toplevel(self)
        dialog.grab_set()
        body = make_form(dialog, "选已有目录当工作区")

        tk.Label(body, text="显示名称", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        name_var = tk.StringVar()
        name_entry = make_entry(body, name_var, width=38)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        tk.Label(body, text="目录路径", bg=PAGE_BG, fg=MUTED, font=font(10),
                 anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        path_var = tk.StringVar()
        make_entry(body, path_var, width=38).grid(row=1, column=1, sticky="ew",
                                                  padx=(12, 0), pady=4)

        def browse():
            chosen = filedialog.askdirectory(parent=dialog, title="选择工作区目录")
            if chosen:
                chosen = os.path.normpath(chosen)
                path_var.set(chosen)
                if not name_var.get().strip():
                    name_var.set(os.path.basename(chosen))

        PillButton(body, "浏览…", browse, bg=PAGE_BG).grid(row=1, column=2, padx=(8, 0))
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="显示名称留空就用文件夹名。", bg=PAGE_BG, fg=MUTED,
                 font=font(9), anchor="w").grid(row=3, column=0, columnspan=3,
                                                sticky="w", pady=(10, 0))

        save = lambda: self._save_new_workspace(dialog, name_var, path_var)
        finish_form(dialog, [("保存", save, True), ("取消", dialog.destroy, False)])
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        name_entry.focus_set()
        self._center(dialog)


    def _save_new_workspace(self, dialog, name_var, path_var):
        path = os.path.normpath(path_var.get().strip())
        if not path or path == ".":
            messagebox.showerror("缺少路径", "请填写目录路径。", parent=dialog)
            return
        if not os.path.isdir(path):
            messagebox.showerror("目录不存在", "找不到目录：\n{}".format(path), parent=dialog)
            return

        name = name_var.get().strip() or os.path.basename(path)
        known = {os.path.normcase(os.path.normpath(w["path"]))
                 for w in self.config_data["workspaces"]}
        if os.path.normcase(path) in known:
            messagebox.showerror("重复添加", "这个目录已经在列表里了。", parent=dialog)
            return

        self.config_data["workspaces"].append({"name": name, "path": path})
        save_config(self.config_data)
        dialog.destroy()
        self.refresh_workspaces()
        self.feedback_var.set("已添加工作区 {}。".format(name))


    def _center(self, dialog):
        """把对话框摆到主窗口中间。"""
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry("+{}+{}".format(x, y))
