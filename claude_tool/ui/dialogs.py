"""那几个对话框：加模型、加工作区、点工作区那次询问。

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

from claude_tool.paths import ILLEGAL_CHARS, PRESET_DIR, TOOL_DIR, WORKPLACE_DIR
from claude_tool.theme import (
    MUTED,
    PAGE_BG,
    PANEL_BG,
    TEXT,
    font,
)
from claude_tool.permissions import (
    PERMISSION_VALUES,
    permission_hint,
    permission_option,
    workspace_permission,
)
from claude_tool.presets import PROVIDERS, discover_presets, host_of, preset_path, read_env
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

        fields = [("预设名称", "name", False), ("Base URL", "base_url", False),
                  ("API Token", "token", True), ("模型名", "model", False)]
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

        for label, key, secret in fields:
            tk.Label(body, text=label, bg=PAGE_BG, fg=MUTED, font=font(10),
                     anchor="w").grid(row=row, column=0, sticky="w", pady=4)
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

        note = "改名会一并重命名 claude_settings\\<名称>.json；" if is_edit else \
               "设置会存成 .claude_tool\\claude_settings\\<名称>.json；"
        tk.Label(body, text=note + "env 之外的公共设置沿用原文件。",
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


    def _open_workplace_dialog(self):
        """改默认工作区目录。选完就落盘，以后「＋ 新建文件夹」都建在这儿。"""
        current = self.config_data["workplace"]
        chosen = filedialog.askdirectory(
            parent=self, title="选择默认工作区目录",
            initialdir=current if os.path.isdir(current) else TOOL_DIR)
        if not chosen:
            return
        base = os.path.normpath(chosen)
        if path_key(base) == path_key(current):
            return
        self._set_workplace(base)
        save_config(self.config_data)
        self.feedback_var.set(
            "默认工作区目录改成 {}，以后「＋ 新建文件夹」就建在这儿。".format(base))


    def _open_quick_workspace_dialog(self):
        """「＋ 新建文件夹」：在默认工作区目录下面建一个新文件夹，当工作区用。"""
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
        """「＋ 选已有目录」：挑一个已经存在的目录，加进列表里当工作区。"""
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
