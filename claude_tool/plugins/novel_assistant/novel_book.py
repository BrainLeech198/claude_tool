"""小说工作台的数据层：**只读、只解析，一行界面都不碰**。

契约写死在作品集的 `作品集结构规范.md` §七——只认那几个死名字，不做模糊匹配。
把"读一份文件、解析成结构"独立出来，是因为**这一层能拿真书直接验**（见
`_probe_novel_book.py`）：界面上那层反而不好测。

三条口径跟着规范走，别自作主张：

1. **缺文件是正常状态**（契约第 2 条）——返回空值，不抛错、不去别处找。
2. **`doc/` 只读点名的两份**：`发布记录.md` 的 §一、`审查清单.md` 的 §三。
   剩下的一律不看——尤其别把 §五"解冻与回改"里那些 `001／002` 当成发布状态。
3. **`temp/` 与 `plan/` 一律不读**。
"""
import os
import re

CARD_FILE = "书籍信息.md"
SPEC_FILE = "作品集结构规范.md"
STORY_DIR = "story"
PEOPLE_FILE = "人物设定.md"
STYLE_FILE = "文笔要点.md"
PUBLISH_FILE = os.path.join("doc", "发布记录.md")
REVIEW_FILE = os.path.join("doc", "审查清单.md")

# 章文件名 `NNN 标题.txt`（规范 §4.2）。**章号就是发布顺序**，所以排序认数字不认
# 字符串——否则 `100` 会排到 `09` 前头。顺带容忍早期那种 `1.序言` 的写法。
_CHAPTER_RE = re.compile(r"^(\d+)\s*[.、]?\s*(.*)$")
# 书卡字段行：`**字段**：值`（规范 §3.1，中文冒号；半角也认）。
_FIELD_RE = re.compile(r"^\*\*([^*]+)\*\*\s*[:：]\s*(.*)$")
# 标题行，用来给"内容简介到哪儿为止"划线（规范 §3.1）。
_HEADING_RE = re.compile(r"^#{1,6}\s")
# 表格分隔行 `|---|---|`。
_TABLE_SEP_RE = re.compile(r"^[\s|:\-]+$")
_NUMBER_RE = re.compile(r"^(\d+)")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_MARK_RE = re.compile(r"[*`]+")


# ── 小工具 ────────────────────────────────────────────────────────────────

def _read(path):
    """读一份文本。读不了就回空串——缺文件是正常状态（契约第 2 条）。

    编码按 `utf-8-sig`：规范定的"无 BOM"是给写入方立的规矩，读取方多认一种写法
    是白赚的——真碰到带 BOM 的文件，别让那三个字节跑到书名里去。
    """
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _strip_marks(text):
    """把书名号剥掉：`《雾中点名》` → `雾中点名`。规范 §3.1 说可写可不写。"""
    return (text or "").strip().strip("《》").strip()


def _leading_number(text):
    match = _NUMBER_RE.match((text or "").strip())
    return int(match.group(1)) if match else None


def _split_row(line):
    """拆一行 markdown 表格 → 各格文本。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _section(lines, wanted):
    """取某个 `## ` 小节的行，到下一个 `## ` 为止。

    `wanted` 是**小节序号**（"一" / "三"）而不是整句标题——标题里的字会改，
    序号不会。底下那些 `###` 子标题不算分节（§五 里全是 `### 001…`）。
    """
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().startswith(wanted)
            continue
        if inside:
            out.append(line)
    return out


def plain(text):
    """去掉 markdown 的粗体/反引号——面板上要显示的是给人看的字，不是源码。"""
    return _MARK_RE.sub("", text or "").strip()


# ── 是什么 ────────────────────────────────────────────────────────────────

def is_book(path):
    """这个目录是不是"一本书"：有书卡、且有 `story/`（规范 §3.1 的两条必填）。"""
    return bool(path) and (
        os.path.isfile(os.path.join(path, CARD_FILE))
        and os.path.isdir(os.path.join(path, STORY_DIR)))


def is_collection(path):
    """这个目录是不是"作品集根"：有那份规范。"""
    return bool(path) and os.path.isfile(os.path.join(path, SPEC_FILE))


# ── 读一本书 ──────────────────────────────────────────────────────────────

def read_card(path):
    """书卡：书名 / 类别 / 内容简介。缺的字段是空串。

    内容简介是唯一允许跨行的字段：从字段行下一行起算，碰到下一个 `**字段**：`
    行或 `#` 标题为止；行首的引用块记号 `> ` 剥掉（规范 §3.1——引用块和普通段落
    等价，两本书各用一种，都得吃）。
    """
    lines = _read(os.path.join(path, CARD_FILE)).splitlines()
    card = {"title": "", "category": "", "intro": ""}
    index = 0
    while index < len(lines):
        match = _FIELD_RE.match(lines[index])
        if not match:
            index += 1
            continue
        name, value = match.group(1).strip(), match.group(2).strip()
        # **先出现的那个算数**：书卡以外的段落里也有 `**作者 2026-01-01 定**：` 这种
        # 加粗行，正文里还可能再抄一遍书名（讲卷次时会提到）。后出现的同名字段不认。
        if name == "书名" and not card["title"]:
            card["title"] = _strip_marks(value)
        elif name == "类别" and not card["category"]:
            card["category"] = value
        elif name == "内容简介" and not card["intro"]:
            collected = [value] if value else []
            index += 1
            while index < len(lines):
                nxt = lines[index]
                if _FIELD_RE.match(nxt) or _HEADING_RE.match(nxt):
                    break
                text = nxt.strip().lstrip(">").strip()
                if text:
                    collected.append(text)
                index += 1
            card["intro"] = "\n".join(collected)
            continue
        index += 1
    return card


def read_chapters(path):
    """章节列表，按**解析出的章号**排序（＝发布顺序，规范 §4.1）。

    文件名不是 `NNN 标题` 那种的排在后头，照原样列出来——不假装它有个章号。
    """
    try:
        names = os.listdir(os.path.join(path, STORY_DIR))
    except OSError:
        return []
    items = []
    for name in names:
        if not name.lower().endswith(".txt"):
            continue
        stem = os.path.splitext(name)[0]
        match = _CHAPTER_RE.match(stem)
        if match:
            items.append((0, int(match.group(1)), match.group(2).strip(), name))
        else:
            items.append((1, 0, stem.strip(), name))
    items.sort(key=lambda item: (item[0], item[1], item[3]))
    return [{"num": None if kind else num, "title": title, "file": name}
            for kind, num, title, name in items]


def read_published(path):
    """已发布章：`doc/发布记录.md` §一 的表 → `{章号: {平台, 日期, 字数}}`。

    规范 §七 明说**只读 §一**。§五 那份"解冻与回改"里也满是 `001／002` 之类的
    字样，混进来就会把"改过"当成"发布过"。
    """
    lines = _read(os.path.join(path, PUBLISH_FILE)).splitlines()
    out = {}
    for line in _section(lines, "一"):
        if not line.lstrip().startswith("|") or _TABLE_SEP_RE.match(line):
            continue
        cells = _split_row(line)
        number = _leading_number(cells[0]) if cells else None
        if number is None:
            continue                      # 表头那种"章"没有数字，自然跳过
        out[number] = {
            "platform": cells[2] if len(cells) > 2 else "",
            "date": cells[3] if len(cells) > 3 else "",
            "words": cells[4] if len(cells) > 4 else "",
        }
    return out


def read_watch_words(path):
    """本轮观察词：`doc/审查清单.md` §三 的表（规范 §七 只读这张表）。

    这张表是**机检脚本的输入**，所以第一列里反引号包着的词才是要紧的东西——那是
    下一轮审查会自动盯上的字眼。第一列没有反引号的行（说明、小标题）跳过。
    """
    lines = _read(os.path.join(path, REVIEW_FILE)).splitlines()
    out = []
    for line in _section(lines, "三"):
        if not line.lstrip().startswith("|") or _TABLE_SEP_RE.match(line):
            continue
        cells = _split_row(line)
        if not cells:
            continue
        words = _BACKTICK_RE.findall(cells[0])
        if not words:
            continue
        out.append({
            "word": words[0],
            "extra": len(words) - 1,      # 一格塞了好几个词的，面板上标"等 N 个"
            "starred": "⭐" in cells[0],  # ⭐ ＝ 作者标的本轮重点
            "where": cells[1] if len(cells) > 1 else "",
            "how": cells[2] if len(cells) > 2 else "",
        })
    return out


def read_people(path):
    """人物：`人物设定.md` 里的三级标题（`### 许默 · 主角`）。

    只取标题、不读正文——面板上要的是"有谁"，设定在文件里，点开看全的。没有三级
    标题就退一级；都没有就返回空表（人物页按契约是可缺的）。
    """
    text = _read(os.path.join(path, PEOPLE_FILE))
    if not text:
        return []
    for level in ("### ", "## "):
        found = []
        for line in text.splitlines():
            if line.startswith(level) and not line.startswith(level + "#"):
                title = line[len(level):].strip()
                if title:
                    found.append(title)
        if found:
            return [_split_person(title) for title in found]
    return []


def _split_person(title):
    """`许默 · 主角` → `{"name": "许默", "role": "主角"}`。"""
    if "·" in title:
        name, role = title.split("·", 1)
        return {"name": name.strip(), "role": role.strip()}
    return {"name": title.strip(), "role": ""}


def load_book(path):
    """一本书能读到的**全部**东西，一次读完。缺的项是空值，别当错。"""
    return {
        "path": path,
        "name": os.path.basename(os.path.normpath(path)),
        "card": read_card(path),
        "chapters": read_chapters(path),
        "published": read_published(path),
        "people": read_people(path),
        "watch": read_watch_words(path),
        "has_people": os.path.isfile(os.path.join(path, PEOPLE_FILE)),
        "has_style": os.path.isfile(os.path.join(path, STYLE_FILE)),
    }


def find_books(root):
    """作品集根下的书：直接子目录里有 `书籍信息.md` 的那些（平铺，不递归）。"""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    books = []
    for name in names:
        if name.startswith((".", "_")):
            continue
        full = os.path.join(root, name)
        if os.path.isdir(full) and is_book(full):
            books.append({"name": name, "path": full})
    return books


def detect(path):
    """当前选中的这个工作区是什么。返回 `(种类, 数据)`：

    - `("book", 一本书的数据)`   —— 它就是一本小说工程
    - `("collection", 书单)`     —— 它是作品集根
    - `(None, None)`             —— 都不是；插件那一块整个不出现

    第三种是**谈定的规则**：不能见个目录就往右栏挂一块东西，"是不是作品集"得有个
    明确判据，不是猜。
    """
    if not path or not os.path.isdir(path):
        return None, None
    if is_book(path):
        return "book", load_book(path)
    if is_collection(path):
        return "collection", find_books(path)
    return None, None
