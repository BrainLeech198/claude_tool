"""把一本书画成右栏那块面板。**只画界面，不读文件**——数据由 `novel_book` 给。

## 外面套一层滚动区，是有意的

右栏（`detail_plugin_area`）是 `fill="x"` 一路往下摞的，没有自己的滚动条；窗口缩到
最小（660 高）时那块地方只剩四百来像素。面板要是直接铺开，多出来的部分会被**裁掉**
——而且裁掉的正好是最下面那排按钮。所以面板自己套一个 `ScrollArea` 并把高度封顶：
内容再多也只是面板内部滚动，不会顶出窗口、也不会把按钮挤没。

## 按钮画在面板里，不用 register_action

`register_action("workspace_detail", …)` 挂出来的按钮是**全局**的：不管当前选中的
是不是小说工程，它都排在右栏那一行。而这两个动作只对"一本书"成立，所以跟面板一起
画——选中别的工程时整块一起消失（空面板宿主会连标题一起收掉）。
"""
import tkinter as tk

from novel_book import plain

# 面板最多占多高。留够最小窗口（660）时的余地，见文件头那段。
MAX_PANEL_HEIGHT = 360

# 长列表的截断线。一本连载几百章时不能把面板画成几百行。
MAX_CHAPTERS = 60
MAX_PEOPLE = 30
MAX_WATCH = 8

# 「按规范审查」喂给会话的提示词。**只指菜单、不指死路径**——协议自己搬过家
# （`doc/11 …` → `doc/协作与流程/11 …`），指死了下次搬家就又对不上。
REVIEW_PROMPT = (
    "按本作品集的《小说审查协议》走【诊断档】，审一遍《{title}》。\n"
    "\n"
    "入口：先读根目录的 CLAUDE.md 和 doc/00 索引与用法.md，把当前口径对上；"
    "再看本书 doc/审查清单.md（本书专有的判据、已登记观察、机检基线）。"
    "本书的写法口径在 文笔要点.md。\n"
    "报告按协议规定的格式出，落在本书 doc/ 下，文件名带日期。\n"
    "**只审不改**：正文一个字都别动；查到要改的，出选择题让作者勾。\n"
)


# ── 版式小工具 ────────────────────────────────────────────────────────────

def _room(parent):
    """面板能用的文字宽度。

    父框刚建出来时 `winfo_width()` 还是 1（还没 map），所以回退问上一层——
    右栏那个容器早就有真宽度了。
    """
    for widget in (parent, getattr(parent, "master", None)):
        if widget is None:
            continue
        try:
            width = widget.winfo_width()
        except tk.TclError:
            continue
        if width > 1:
            return max(int(width) - 8, 220)
    return 420


def _caption(parent, text, theme, pady=(0, 4)):
    tk.Label(parent, text=text, bg=theme.PAGE_BG, fg=theme.MUTED,
             font=theme.font(9), anchor="w").pack(anchor="w", pady=pady)


def _divider(parent, theme):
    tk.Frame(parent, bg=theme.BORDER, height=1).pack(fill="x", pady=(10, 8))


def _split_row(parent, left, right, theme, right_color=None):
    """一行两截：左边名字、右边小字右对齐（章节行用它挂"已发布·冻结"）。"""
    line = tk.Frame(parent, bg=theme.PAGE_BG)
    line.pack(fill="x", pady=(0, 1))
    tk.Label(line, text=left, bg=theme.PAGE_BG, fg=theme.TEXT,
             font=theme.font(10), anchor="w").pack(side="left")
    if right:
        tk.Label(line, text=right, bg=theme.PAGE_BG,
                 fg=right_color or theme.MUTED, font=theme.font(9),
                 anchor="e").pack(side="right")
    return line


def _wrapped(parent, text, theme, room, color=None, size=9, pady=(0, 0)):
    tk.Label(parent, text=text, bg=theme.PAGE_BG, fg=color or theme.MUTED,
             font=theme.font(size), anchor="w", justify="left",
             wraplength=room).pack(anchor="w", pady=pady)


def _chapter_label(item):
    if item["num"] is None:
        return item["title"] or item["file"]
    return "{:03d}  {}".format(item["num"], item["title"] or item["file"])


# ── 一本书 ────────────────────────────────────────────────────────────────

def build_book_panel(parent, book, host):
    theme = host.theme
    widgets = host.widgets
    room = _room(parent)
    bg = theme.PAGE_BG
    path = book["path"]
    card = book["card"]
    title = card["title"] or book["name"]

    area = widgets.ScrollArea(parent, max_height=MAX_PANEL_HEIGHT, bg=bg)
    area.pack(fill="x")
    inner = area.inner

    _caption(inner, "小说工作台", theme)

    tk.Label(inner, text="《{}》".format(title), bg=bg, fg=theme.TEXT,
             font=theme.font(13, True), anchor="w").pack(anchor="w")

    published = book["published"]
    meta = "{} 章 · 已发布 {}".format(len(book["chapters"]), len(published))
    if card["category"]:
        meta = "{} · {}".format(card["category"], meta)
    _wrapped(inner, meta, theme, room, pady=(2, 0))

    if card["intro"]:
        _wrapped(inner, card["intro"], theme, room, pady=(6, 0))

    if book["has_style"]:
        _wrapped(inner, "写法口径：文笔要点.md（开会话时会带上）", theme, room,
                 pady=(6, 0))

    # 按钮紧挨着书卡，排在长列表**前面**：面板滚动时它们始终在最上头。
    actions = tk.Frame(inner, bg=bg)
    actions.pack(fill="x", pady=(10, 0))
    widgets.PillButton(actions, "按规范审查", lambda: _review(host, path, title),
                       primary=True, bg=bg, height=28).pack(side="left")
    widgets.PillButton(actions, "打开书目录",
                       lambda: _open(host, path), bg=bg, height=28).pack(
        side="left", padx=(6, 0))

    _divider(inner, theme)
    _caption(inner, "章节（story/·章号＝发布顺序）", theme)
    _chapters(inner, book["chapters"], published, theme, room)

    _divider(inner, theme)
    _caption(inner, "人物（人物设定.md）", theme)
    _people(inner, book, theme, room)

    _divider(inner, theme)
    _caption(inner, "本轮观察词（doc/审查清单.md §三）", theme)
    _watch(inner, book["watch"], theme, room)

    area.fit()


def _chapters(parent, chapters, published, theme, room):
    if not chapters:
        _wrapped(parent, "story/ 里还没有章节文件。", theme, room)
        return
    for item in chapters[:MAX_CHAPTERS]:
        number = item["num"]
        mark = "已发布·冻结" if number in published else ""
        _split_row(parent, _chapter_label(item), mark, theme,
                   right_color=theme.ACCENT if mark else theme.MUTED)
    rest = len(chapters) - MAX_CHAPTERS
    if rest > 0:
        _wrapped(parent, "还有 {} 章…".format(rest), theme, room, pady=(4, 0))


def _people(parent, book, theme, room):
    people = book["people"]
    if not people:
        hint = ("有 人物设定.md，但没找到「### 名字 · 身份」这种小标题。"
                if book["has_people"] else "没有 人物设定.md。")
        _wrapped(parent, hint, theme, room)
        return
    for person in people[:MAX_PEOPLE]:
        _split_row(parent, person["name"], person["role"], theme)
    rest = len(people) - MAX_PEOPLE
    if rest > 0:
        _wrapped(parent, "还有 {} 位…".format(rest), theme, room, pady=(4, 0))


def _watch(parent, words, theme, room):
    """本轮观察词。⭐ 的是作者标的本轮重点，先摆它们。

    整表（含"位置""处置"两列那些长说明）不往这儿搬：这是"一眼看见这轮盯什么"的
    地方，全文在 `doc/审查清单.md` §三。
    """
    if not words:
        _wrapped(parent, "没有登记观察（或缺 doc/审查清单.md）。", theme, room)
        return
    starred = [item for item in words if item["starred"]]
    shown = (starred or words)[:MAX_WATCH]
    for item in shown:
        word = item["word"]
        if item["extra"]:
            word = "{} 等 {} 个".format(word, item["extra"] + 1)
        _wrapped(parent, ("⭐ " if item["starred"] else "") + word, theme, room,
                 color=theme.TEXT, size=10, pady=(0, 1))
    rest = len(words) - len(shown)
    if rest > 0:
        _wrapped(parent, "另有 {} 条 · 全文见 doc/审查清单.md §三".format(rest),
                 theme, room, pady=(4, 0))


# ── 作品集根 ──────────────────────────────────────────────────────────────

def build_collection_panel(parent, books, root, host):
    """选中作品集根时：列一下有几本，每本多少章、发过几章。"""
    theme = host.theme
    widgets = host.widgets
    room = _room(parent)
    bg = theme.PAGE_BG

    area = widgets.ScrollArea(parent, max_height=MAX_PANEL_HEIGHT, bg=bg)
    area.pack(fill="x")
    inner = area.inner

    _caption(inner, "小说工作台", theme)
    tk.Label(inner, text="作品集 · {} 本".format(len(books)), bg=bg, fg=theme.TEXT,
             font=theme.font(13, True), anchor="w").pack(anchor="w")

    if not books:
        _wrapped(inner, "这个目录下有作品集规范，但没找到书"
                        "（书＝子目录里有 书籍信息.md 和 story/）。",
                 theme, room, pady=(6, 0))
    for book in books[:MAX_CHAPTERS]:
        from novel_book import read_chapters, read_published
        total = len(read_chapters(book["path"]))
        done = len(read_published(book["path"]))
        _split_row(inner, "《{}》".format(book["name"]),
                   "{} 章 · 已发布 {}".format(total, done), theme)
    _wrapped(inner, "在左栏选中某一本，这块就摊开它的书卡、章节、人物与观察词。",
             theme, room, pady=(8, 0))

    actions = tk.Frame(inner, bg=bg)
    actions.pack(fill="x", pady=(10, 0))
    widgets.PillButton(actions, "打开作品集目录", lambda: _open(host, root),
                       bg=bg, height=28).pack(side="left")

    area.fit()


# ── 两个动作 ──────────────────────────────────────────────────────────────

def _review(host, path, title):
    """开一个审查会话。**插件不跑脚本**——只把口径喂给 claude（对接函 N2）。"""
    host.log("开一个审查会话：《{}》".format(title))
    try:
        host.open_claude(path, prompt=REVIEW_PROMPT.format(title=title))
    except Exception as exc:                              # noqa: BLE001
        host.log("开不了会话：{}: {}".format(type(exc).__name__, exc))


def _open(host, path):
    try:
        host.open_dir(path)
    except Exception as exc:                              # noqa: BLE001
        host.log("打不开目录：{}: {}".format(type(exc).__name__, exc))
