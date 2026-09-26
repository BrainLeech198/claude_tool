"""小说工作台的数据层：书卡 / 章节 / 发布状态 / 人物 / 观察词，按契约读对。

## 盯的是什么

`claude_tool/plugins/novel_assistant/novel_book.py` 只读、只解析，是插件里唯一
"读错了会**静默**给出错答案"的一层——界面上看不出来。所以造一份假作品集喂它。

造的是**两份真书里最脏的那几处**，不是干净的理想格式：

1. 书卡的简介是引用块（`> ` 开头）——规范 §3.1 说引用块和普通段落等价、都要吃
   （《雾中点名》是引用块《1975》是普通段落），两条路都造。
2. 简介后面紧跟 `#### 简介状态：…` 标题——**简介得在这儿停住**，别把下面那些
   讲"旧稿作废"的说明也一起收进去。
3. 书卡以外的段落里还有 `**作者…**：` 这种加粗行、甚至再抄一遍书名。
   **后出现的同名字段不能顶掉书卡里那个。**
4. `发布记录.md` 的 §五「解冻与回改」里全是 `| 4 | 004 L95 … |` 这种表格行——
   规范 §七 明说只读 §一，这些**不能**混进发布状态。
5. `审查清单.md` 的 §一/§四 也有表格——观察词只认 §三，其余一行都不许漏进来。
6. 章号排序按**数字**：`003` 要排在 `010` 前面（按字符串排会把 `010` 排前头）。

## 为什么不沙箱 USERPROFILE

这份探针**不起启动器、不 import claude_tool**——它只 load 插件那个纯数据模块，
碰的全是自己在 `temp/probes/sandboxes/` 里造的假书。没有"把 ~ 冻成常量"的风险。

## 它自己也验过"验得出错"

`temp/novel_book_negative.py` 把 `_section()`（只读某一节）换成"整份文件都收"，
再跑同一批断言——发布状态与观察词会红。跑绿不算数，能红才算数。

    python _probe_run.py _probe_novel_book.py
"""
import importlib.util
import os
import shutil
import sys

from _probe_common import sandbox  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(ROOT, "claude_tool", "plugins", "novel_assistant")

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<50} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))
    return ok


def load_book_module(name="novel_book_probe"):
    """把 `novel_book.py` 单独 load 进来——不起启动器、不碰 tk。

    数据层没有插件内的相对 import，所以能这样直着 load；探针于是跟界面完全解耦。
    """
    path = os.path.join(PLUGIN, "novel_book.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 造一份假作品集 ────────────────────────────────────────────────────────

CARD_QUOTE = """# 书籍信息（《甲书》）

> 作者口述重建。旧版同名文件作废。

## 一、书卡

**书名**：《甲书》
**类别**：悬疑脑洞（子类·规则怪谈）
**内容简介**：

> 第一句。
> 第二句。
> 第三句。

#### 简介状态：**已定稿·可再改**

**作者 2026-01-01 定**：旧稿元素已作废，本版重新定稿。

## 四、书名与卷次

**书名**：《乙书》（这不是书卡，不该顶掉上面那个）
第一卷＝第 001 章起。
"""

CARD_PLAIN = """# 乙书

## 一、书卡

**书名**：乙书
**类别**：玄幻奇幻
**内容简介**：
加班猝死，社畜一朝转世。投胎至隐世半仙家族。
"""

PEOPLE = """# 人物设定（《甲书》）

> 说明。

## 一、主角团（四人）

### 许默 · 主角

**外形**　瘦。

### 夏栀 · 女主

**外形**　匀称。
"""

PUBLISH = """# 发布记录（《甲书》）

> 说明。

## 一、已发布

**作者告：前两章已发布。**

| 章 | 文件 | 平台 | 发布 | 非空白字 |
|---|---|---|---|---|
| 001 盘山道 | `story/001 盘山道.txt` | 番茄 | 2026-01-01 | 3118 |
| 002 三个弯 | `story/002 三个弯.txt` | 番茄 | 2026-01-01 | 6082 |

## 二、"冻结"是什么意思

1. 正文不再改。

## 五、解冻与回改

### 001「盘山道」

| # | 改动 | 处数 |
|---|---|---|
| 4 | 004 L95 那处改了 | 1 |
| 10 | 010 L231 那处改了 | 1 |
"""

REVIEW = """# 《甲书》审查清单

> 通用流程见根 doc/。

## 一、六条标准在本书的具体判据

| # | 标准 | 本书怎么算"有" |
|---|---|---|
| ① | 灵异恐怖感 | 靠"缺" |

## 三、登记观察（本次不动，往后写作避开）

> 词表会被机检脚本自动读取。

| 词／项 | 位置 | 处置 |
|---|---|---|
| ⭐ `过了一会儿／过了会儿` | **×8** | 004 起每章 ≤1 次 |
| `我没敢` | ×5 | 换写法 |
| ⭐ `她说`／`他说`／`我说` | 002 单章 19 次 | 单章 ≤10 次 |

## 四、机检基线

| `非空白字` | 001 |
|---|---|
| `对话占比` | 41% |
"""


def _write(path, text):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build_fixture():
    """造一个假的"作品集根"。同名目录每跑一次重建，不留上次的渣。"""
    root = sandbox("novel_book")
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)

    _write(os.path.join(root, "作品集结构规范.md"), "# 作品集结构规范\n\n（占位）\n")

    book = os.path.join(root, "甲书")
    _write(os.path.join(book, "书籍信息.md"), CARD_QUOTE)
    _write(os.path.join(book, "人物设定.md"), PEOPLE)
    _write(os.path.join(book, "文笔要点.md"), "# 文笔要点\n")
    _write(os.path.join(book, "doc", "发布记录.md"), PUBLISH)
    _write(os.path.join(book, "doc", "审查清单.md"), REVIEW)
    # 下面这几份是**契约说不读的**：摆在这儿就是要确认它们一个字都没漏进来
    _write(os.path.join(book, "doc", "第一卷空间布局.md"), "# 布局\n")
    _write(os.path.join(book, "plan", "选择题-001-20260101.md"), "# 选择题\n")
    _write(os.path.join(book, "temp", "12 出来拿.txt"), "废稿\n")
    for name in ("001 盘山道.txt", "002 三个弯.txt", "003 错车.txt",
                 "010 十.txt", "序言.txt"):
        _write(os.path.join(book, "story", name), "正文\n")
    _write(os.path.join(book, "story", "readme.md"), "不是章节\n")

    plain_book = os.path.join(root, "乙书")
    _write(os.path.join(plain_book, "书籍信息.md"), CARD_PLAIN)
    _write(os.path.join(plain_book, "story", "001 序言.txt"), "正文\n")

    # 有书卡、没有 story/：**不算一本书**（规范 §3.1 两条必填缺一）
    _write(os.path.join(root, "半本", "书籍信息.md"), CARD_QUOTE)

    # 跟书没关系的工作区、以及该被跳过的隐藏/下划线目录
    _write(os.path.join(root, "随手建的", "readme.md"), "x\n")
    _write(os.path.join(root, ".hidden", "书籍信息.md"), "x\n")
    _write(os.path.join(root, "_临时", "书籍信息.md"), "x\n")
    return root


# ── 断言 ──────────────────────────────────────────────────────────────────

def run(module, root):
    """跑全部断言，返回挂了的那几条。负面验证会拿**同一批**断言再跑一遍。"""
    global FAILED
    FAILED = []
    book = os.path.join(root, "甲书")
    plain_book = os.path.join(root, "乙书")

    # ── 书卡 ──
    card = module.read_card(book)
    check("书卡 · 书名剥掉书名号", card["title"], "甲书")
    check("书卡 · 类别", card["category"], "悬疑脑洞（子类·规则怪谈）")
    check("书卡 · 引用块简介：剥 `> `、三句都在",
          card["intro"], "第一句。\n第二句。\n第三句。")
    check("书卡 · 简介在 `#### 简介状态` 那儿停住、没把说明收进去",
          "旧稿" in card["intro"], False)
    check("书卡 · 后面那段 `**书名**` 不顶掉书卡里那个",
          card["title"], "甲书")

    plain_card = module.read_card(plain_book)
    check("书卡 · 普通段落式简介（《1975》那种）也吃",
          plain_card["intro"], "加班猝死，社畜一朝转世。投胎至隐世半仙家族。")
    check("书卡 · 书名没写书名号也认", plain_card["title"], "乙书")

    # ── 章节 ──
    chapters = module.read_chapters(book)
    check("章节 · 按数字排（003 在 010 前）、没章号的排最后",
          [(c["num"], c["title"]) for c in chapters],
          [(1, "盘山道"), (2, "三个弯"), (3, "错车"), (10, "十"), (None, "序言")])
    check("章节 · 非 .txt（readme.md）不进来",
          [c["file"] for c in chapters if c["file"].endswith(".md")], [])

    # ── 发布状态 ──
    published = module.read_published(book)
    check("发布状态 · 只认 §一 那两行", sorted(published), [1, 2])
    check("发布状态 · §五「解冻与回改」的 `004` 不能漏进来", 4 in published, False)
    check("发布状态 · 同处那个 `010` 也不能", 10 in published, False)
    check("发布状态 · 顺带带上平台", published[1]["platform"], "番茄")
    check("发布状态 · 缺 发布记录.md 时是空表（缺文件不是错）",
          module.read_published(plain_book), {})

    # ── 观察词 ──
    watch = module.read_watch_words(book)
    check("观察词 · 只认 §三 那三行", len(watch), 3)
    check("观察词 · 取到第一列反引号里的词", watch[0]["word"], "过了一会儿／过了会儿")
    check("观察词 · ⭐ 标出来", [w["starred"] for w in watch], [True, False, True])
    check("观察词 · 一格塞了三个词的标出「等几个」", watch[2]["extra"], 2)
    check("观察词 · §四 的表格（`非空白字` 那种）不能漏进来",
          [w["word"] for w in watch if w["word"] in ("非空白字", "对话占比")], [])

    # ── 人物 ──
    check("人物 · 三级标题拆成 名字/身份",
          [(p["name"], p["role"]) for p in module.read_people(book)],
          [("许默", "主角"), ("夏栀", "女主")])
    check("人物 · 缺 人物设定.md 时是空表",
          module.read_people(plain_book), [])

    # ── 判定它是"什么" ──
    check("判定 · 有书卡有 story/ ＝ 一本书", module.is_book(book), True)
    check("判定 · 只有书卡没 story/ 不算书",
          module.is_book(os.path.join(root, "半本")), False)
    check("判定 · 有规范 ＝ 作品集根", module.is_collection(root), True)
    check("判定 · 一本书不是作品集根", module.is_collection(book), False)

    kind, data = module.detect(book)
    check("detect · 一本书 → book", (kind, data["card"]["title"]), ("book", "甲书"))
    kind, books = module.detect(root)
    check("detect · 作品集根 → 列出两本书（隐藏/下划线目录跳过）",
          (kind, [b["name"] for b in books]), ("collection", ["乙书", "甲书"]))
    check("detect · 既不是书也不是根 → 什么都不出现",
          module.detect(os.path.join(root, "随手建的")), (None, None))
    check("detect · 路径不存在也不炸",
          module.detect(os.path.join(root, "没有这个")), (None, None))

    # ── 汇总 ──
    data = module.load_book(book)
    check("汇总 · has_style 认得 文笔要点.md", data["has_style"], True)
    check("汇总 · has_people", data["has_people"], True)
    check("汇总 · 名字退回目录名", data["name"], "甲书")

    check("plain · 去掉粗体和反引号", module.plain("**头号**`透明句`"), "头号透明句")

    return list(FAILED)


def main():
    module = load_book_module()
    root = build_fixture()
    print("假作品集：{}\n".format(root))
    failed = run(module, root)

    print()
    if failed:
        print("挂了 {} 条：{}".format(len(failed), "、".join(failed)))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    sys.exit(code)
