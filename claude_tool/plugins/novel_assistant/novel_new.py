"""新建小说工程：一个全局入口，外加"把当前空文件夹变成小说工程"。

## 只铺最低契约

生成的东西只有两样：

    书籍信息.md     书名 / 类别 / 内容简介 三个字段（值留空，等作者填）
    story/          空章节目录

这就是插件认出一本书的全部条件（见 `novel_book.is_book`）。人物设定、文笔要点、
审查清单这些不在这里硬塞模板——空模板只会变成又一份没人看的占位文件；需要时
让会话照着作品集规范补。

## 目录和配置交给宿主

插件只决定"建在哪儿、叫什么"；真正 `makedirs` 和写 launcher.json 的是
`host.create_workspace`（见 plugins/host.py）。这样插件的记录跟界面上那两个
「添加工作区」入口天然同形，不会拼出一条字段不全的工作区。
"""
import os
import tkinter as tk

from novel_book import CARD_FILE, STORY_DIR, is_empty_dir

# 书卡模板。**值一律留空**：`**类别**：` 后面直接换行，读者打开就能填；
# 塞一句"在这里写类别"反而会被当成真值显示在面板上。
CARD_TEMPLATE = """# 书籍信息

**书名**：《{title}》
**类别**：
**内容简介**：
"""

# 文件夹名里不能出现的字符。Windows 上这些是硬限制，其他平台也大同小异；
# 顺手把空白也挡掉——带空格的目录名在命令行里天天要加引号。
_BAD_NAME = set('\\/:*?"<>|')


# ── 骨架 ──────────────────────────────────────────────────────────────────

def _clean_title(text):
    return (text or "").strip().strip("《》").strip()


def _clean_folder(text):
    return (text or "").strip().strip(".")


def _folder_problem(name):
    """文件夹名哪里不行；能用就返回空串。"""
    if not name:
        return "文件夹名不能为空"
    if name in (".", ".."):
        return "换一个文件夹名"
    if any(ch in _BAD_NAME for ch in name):
        return '文件夹名不能包含 \\ / : * ? " < > |'
    if name != name.strip() or name.endswith("."):
        return "文件夹名首尾不要留空格或句点"
    return ""


def scaffold(path, title):
    """在 `path` 里铺一本书的最小骨架，**已存在的文件一个都不覆盖**。

    返回 `(ok, 新建了什么, 用的标题或错误说明)`。失败时第三项是给人看的错误。
    """
    title = _clean_title(title) or os.path.basename(os.path.normpath(path or ""))
    if not path:
        return False, [], "没有指定目录"
    made = []
    try:
        os.makedirs(path, exist_ok=True)
        story = os.path.join(path, STORY_DIR)
        if not os.path.isdir(story):
            os.makedirs(story, exist_ok=True)
            made.append(STORY_DIR + "/")
        card = os.path.join(path, CARD_FILE)
        if not os.path.isfile(card):
            with open(card, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(CARD_TEMPLATE.format(title=title))
            made.append(CARD_FILE)
    except OSError as exc:
        return False, made, "{}: {}".format(type(exc).__name__, exc)
    return True, made, title


# ── 两个入口 ──────────────────────────────────────────────────────────────

def new_book(host):
    """全局入口：选父目录 + 文件夹名 + 书名，建好并加进左栏、当场选中。"""
    home = _default_place(host) or os.path.expanduser("~")
    dialog = _dialog("新建小说工程")
    widgets = host.widgets
    theme = host.theme
    body = widgets.make_form(dialog, "新建小说工程")

    parent_var = tk.StringVar(value=home)
    folder_var = tk.StringVar()
    title_var = tk.StringVar()

    _label(body, "建在哪儿", theme)
    where = tk.Frame(body, bg=theme.PAGE_BG)
    where.pack(fill="x", pady=(0, 10))
    widgets.make_entry(where, parent_var, width=34).pack(
        side="left", fill="x", expand=True, ipady=4)
    widgets.PillButton(where, "浏览…", lambda: _pick_parent(parent_var),
                       bg=theme.PAGE_BG, height=28).pack(side="left", padx=(8, 0))

    _label(body, "文件夹名", theme)
    folder_entry = widgets.make_entry(body, folder_var, width=40)
    folder_entry.pack(fill="x", ipady=4, pady=(0, 10))

    _label(body, "书名（留空就用文件夹名）", theme)
    widgets.make_entry(body, title_var, width=40).pack(fill="x", ipady=4)

    error_var = tk.StringVar()
    tk.Label(body, textvariable=error_var, bg=theme.PAGE_BG, fg=theme.WARN,
             font=theme.font(9), anchor="w", justify="left",
             wraplength=400).pack(fill="x", pady=(8, 0))

    def submit(_event=None):
        parent = os.path.normpath(parent_var.get().strip() or ".")
        name = _clean_folder(folder_var.get())
        problem = _folder_problem(name)
        if problem:
            error_var.set(problem)
            return "break"
        target = os.path.join(parent, name)
        if not os.path.isdir(parent):
            error_var.set("父目录不存在：{}".format(parent))
            return "break"
        if os.path.exists(target) and not os.path.isdir(target):
            error_var.set("同名文件已经存在：{}".format(target))
            return "break"
        if os.path.isdir(target) and not is_empty_dir(target):
            error_var.set("这个文件夹已经存在而且不是空的，换个名字吧。")
            return "break"
        title = _clean_title(title_var.get()) or name
        ok, made, detail = scaffold(target, title)
        if not ok:
            error_var.set("建不出来：{}".format(detail))
            return "break"
        entry = _add_workspace(host, target, name=title)
        host.log("建好《{}》{}（{}）".format(
            title, " · " + ", ".join(made) if made else "", target))
        if entry is None:
            host.log("《{}》的文件建好了，但没能加进左栏——请手动添加 {}".format(
                title, target))
        _close(dialog)

    folder_entry.bind("<Return>", submit)
    dialog.bind("<Return>", submit)
    _finish(dialog, widgets, [("取消", lambda: _close(dialog), False),
                              ("创建", submit, True)])
    folder_entry.focus_set()
    _wait(dialog, widgets)


def init_current(host, path):
    """把当前选中的空文件夹变成小说工程。书名默认用文件夹名。"""
    dialog = _dialog("初始化小说工程")
    widgets = host.widgets
    theme = host.theme
    body = widgets.make_form(dialog, "初始化小说工程")

    _label(body, "文件夹：{}".format(path), theme)
    _label(body, "书名", theme)
    title_var = tk.StringVar(
        value=_clean_title(os.path.basename(os.path.normpath(path))))
    entry = widgets.make_entry(body, title_var, width=40)
    entry.pack(fill="x", ipady=4)
    _label(body, "会生成 书籍信息.md 和 story/，已有的文件不会被覆盖。", theme)

    error_var = tk.StringVar()
    tk.Label(body, textvariable=error_var, bg=theme.PAGE_BG, fg=theme.WARN,
             font=theme.font(9), anchor="w", justify="left",
             wraplength=400).pack(fill="x", pady=(8, 0))

    def submit(_event=None):
        ok, made, title = scaffold(path, title_var.get())
        if not ok:
            error_var.set("初始化失败：{}".format(title))
            return "break"
        _add_workspace(host, path)
        host.log("《{}》初始化好了{}".format(
            title, " · " + ", ".join(made) if made else "（文件已经都在）"))
        _close(dialog)

    entry.bind("<Return>", submit)
    dialog.bind("<Return>", submit)
    _finish(dialog, widgets, [("取消", lambda: _close(dialog), False),
                              ("初始化", submit, True)])
    entry.focus_set()
    _wait(dialog, widgets)


# ── 对话框小工具 ──────────────────────────────────────────────────────────

def _default_place(host):
    """宿主设置里的默认工作区目录；老宿主没有这个接口就返回空串。"""
    picker = getattr(host, "default_workplace", None)
    try:
        return picker() if callable(picker) else ""
    except Exception:                                     # noqa: BLE001
        return ""


def _add_workspace(host, path, name=None):
    """加进左栏并选中；老宿主没有这个接口就只留一句提示，不假装成功。"""
    maker = getattr(host, "create_workspace", None)
    if not callable(maker):
        host.log("这个启动器版本还不能从插件添加工作区，请手动添加：" + path)
        return None
    try:
        return maker(path, name=name, select=True)
    except Exception as exc:                              # noqa: BLE001
        host.log("加进工作区失败：{}: {}".format(type(exc).__name__, exc))
        return None


def _dialog(title):
    """一个还没摆控件的空对话框；主题和尺寸由 `make_form` 收尾。"""
    dialog = tk.Toplevel()
    dialog.withdraw()
    dialog.title(title)
    return dialog


def _label(parent, text, theme, pady=(0, 3)):
    tk.Label(parent, text=text, bg=theme.PAGE_BG, fg=theme.MUTED,
             font=theme.font(9), anchor="w", justify="left",
             wraplength=400).pack(fill="x", pady=pady)


def _finish(dialog, widgets, buttons):
    widgets.finish_form(dialog, buttons)
    dialog.bind("<Escape>", lambda _e: _close(dialog))


def _wait(dialog, widgets):
    """摆完控件再露脸、居中、抢焦点——先显示会看到一次跳位置。

    委派给宿主的 `widgets.fit_window`：小屏 / 缩放 / 任务栏都按同一套工作区
    收一遍，不再自己拿整屏尺寸减一减。
    """
    dialog.update_idletasks()
    dialog.deiconify()
    width = max(dialog.winfo_reqwidth(), 440)
    height = dialog.winfo_reqheight()
    widgets.fit_window(dialog, width, height, min_w=440, margin=16)
    dialog.grab_set()
    dialog.wait_window(dialog)


def _close(dialog):
    try:
        dialog.grab_release()
        dialog.destroy()
    except tk.TclError:
        pass


def _pick_parent(var):
    from tkinter import filedialog
    folder = filedialog.askdirectory(initialdir=var.get() or None,
                                     title="新小说建在哪个目录下")
    if folder:
        var.set(os.path.normpath(folder))
