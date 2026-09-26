"""插件包（zip）导入的纯逻辑自检。

**不建窗口、不起进程、不弹任何东西**——验的是 pack / manifest.parse 和 registry
的双目录发现，跟界面无关。用户明确要求过"别打扰我"，所以这条永远不进"要上屏"
那一档。

四块：

  find_root   在包里找插件根：根 / 一层包裹 / 没有 / 两个 / 嵌太深
  import_zip  正常装、换版本、包裹外的东西不装、失败不留残渣
  安全闸      zip slip / 绝对路径 / 符号链接 / 文件数 / 解压后体积 / 坏清单 / id 不合法
  接上宿主    装完能被发现（且仍是未启用）、能加载、双目录同名时用户那份优先

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool。

    python _probe_plugin_zip.py
"""
import json
import os
import shutil
import sys
import zipfile

from _probe_common import sandbox  # noqa: E402
ROOT = sandbox("pluginzip")

# 红线：必须在任何 `import claude_tool.*` 之前（见 _probe_plugins.py 里那段）。
os.environ["USERPROFILE"] = ROOT
os.environ["HOME"] = ROOT
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_tool.plugins import manifest as M   # noqa: E402
from claude_tool.plugins import pack as P       # noqa: E402
from claude_tool.plugins import registry as R   # noqa: E402
from claude_tool.plugins.host import Host       # noqa: E402

FAILED = []

MANIFEST = {"id": "demo", "name": "示例插件", "version": "0.1.0",
            "api_version": M.API_VERSION, "min_host": "0.4.0",
            "entry": "entry.py:register"}

ENTRY_SRC = ("def register(host):\n"
             "    host.register_action('toolbar', '嗨', lambda: None)\n")


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<44} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))


def section(title):
    print()
    print("── {} ──".format(title))


def manifest_text(**over):
    data = dict(MANIFEST)
    data.update(over)
    return json.dumps(data, ensure_ascii=False)


def build(name, members):
    """member 可以是 (名字, 内容) 或者光一个名字（空文件）。"""
    path = os.path.join(ROOT, name)
    with zipfile.ZipFile(path, "w") as zf:
        for item in members:
            if isinstance(item, tuple):
                member, content = item
            else:
                member, content = item, ""
            zf.writestr(member, content)
    return path


def make_zip(name, inner="", text=None, entry=True, extra=None):
    """造一个正常插件包。`inner` 是包裹目录前缀（`""` 或 `"demo/"`）。"""
    members = []
    if text is not None:
        members.append((inner + M.FILE_NAME, text))
    if entry:
        members.append((inner + "entry.py", ENTRY_SRC))
    for member, content in (extra or {}).items():
        members.append((member, content))
    return build(name, members)


def grabbed(fn, *args, **kw):
    """跑一下，返回 BadPackage 那句话；没抛或者抛了别的，都返回 None。"""
    try:
        fn(*args, **kw)
    except P.BadPackage as exc:
        return str(exc)
    except Exception:                             # noqa: BLE001
        return None
    return None


def test_find_root():
    section("find_root：在包里找插件根")

    check("根就是插件", P.find_root(["plugin.json", "entry.py"]), "")
    check("一层包裹", P.find_root(["demo/plugin.json", "demo/entry.py"]), "demo/")
    check("目录项混在里面也不干扰",
          P.find_root(["demo/", "demo/plugin.json"]), "demo/")
    check("同名成员出现两次不算两个",
          P.find_root(["plugin.json", "plugin.json"]), "")
    check("没有清单 -> 拒绝",
          isinstance(grabbed(P.find_root, ["a.txt"]), str), True)
    check("两个清单 -> 拒绝",
          isinstance(grabbed(P.find_root, ["a/plugin.json", "b/plugin.json"]), str),
          True)
    check("嵌太深不算数（只认一层）",
          isinstance(grabbed(P.find_root, ["a/b/plugin.json"]), str), True)


def test_import_normal():
    section("import_zip：正常装")

    dest = os.path.join(ROOT, "dest")
    pid, target = P.import_zip(make_zip("root.zip", text=manifest_text()), dest)
    check("返回 id", pid, "demo")
    check("落点是目的目录下的 id",
          os.path.normcase(target), os.path.normcase(os.path.join(dest, "demo")))
    check("清单落到了",
          os.path.isfile(os.path.join(dest, "demo", M.FILE_NAME)), True)
    check("入口落到了",
          os.path.isfile(os.path.join(dest, "demo", "entry.py")), True)

    # 带一层包裹的包，落点跟"根就是插件"的包**完全一样**
    dest2 = os.path.join(ROOT, "dest2")
    pid2, target2 = P.import_zip(
        make_zip("wrapped.zip", inner="demo/", text=manifest_text()), dest2)
    check("包裹包也落在 dest2/demo",
          (pid2, os.path.normcase(target2)),
          ("demo", os.path.normcase(os.path.join(dest2, "demo"))))

    # 包裹目录之外的东西不装
    dest3 = os.path.join(ROOT, "dest3")
    P.import_zip(make_zip("extra.zip", inner="demo/", text=manifest_text(),
                          extra={"README.md": "外头的说明"}), dest3)
    check("包裹外的 README 没进来",
          os.path.exists(os.path.join(dest3, "demo", "README.md")), False)
    check("也没落在上一层的插件目录里",
          os.path.exists(os.path.join(dest3, "README.md")), False)

    # 打 zip 的机器留下的垃圾不装
    dest4 = os.path.join(ROOT, "dest4")
    P.import_zip(make_zip("junk.zip", text=manifest_text(),
                          extra={"__MACOSX/._plugin.json": "x",
                                 ".DS_Store": "x"}), dest4)
    check("__MACOSX 没进来",
          os.path.exists(os.path.join(dest4, "demo", "__MACOSX")), False)
    check(".DS_Store 没进来",
          os.path.exists(os.path.join(dest4, "demo", ".DS_Store")), False)

    # 子目录里的文件要按原结构解出来
    dest5 = os.path.join(ROOT, "dest5")
    P.import_zip(make_zip("sub.zip", text=manifest_text(),
                          extra={"assets/icon.txt": "x"}), dest5)
    check("子目录文件按原结构落",
          os.path.isfile(os.path.join(dest5, "demo", "assets", "icon.txt")), True)


def test_swap():
    section("import_zip：换版本、失败不留残渣")

    dest = os.path.join(ROOT, "dest_swap")
    P.import_zip(make_zip("v1.zip", text=manifest_text()), dest)
    check("先装上的版本",
          json.load(open(os.path.join(dest, "demo", M.FILE_NAME),
                         encoding="utf-8"))["version"], "0.1.0")

    P.import_zip(make_zip("v2.zip", text=manifest_text(version="0.2.0")), dest)
    check("再装就换成新的",
          json.load(open(os.path.join(dest, "demo", M.FILE_NAME),
                         encoding="utf-8"))["version"], "0.2.0")
    check("旧的那份没留下", sorted(os.listdir(dest)), ["demo"])

    # 失败之后不留半截：装个坏包，dest 里那份还得是好的
    bad = build("slip_swap.zip", [("../跑外面.txt", "x"),
                                  (M.FILE_NAME, manifest_text())])
    check("坏包被拦住", isinstance(grabbed(P.import_zip, bad, dest), str), True)
    check("坏包之后旧插件还在、没残渣", sorted(os.listdir(dest)), ["demo"])
    check("坏包没有写到 dest 外面",
          os.path.exists(os.path.join(ROOT, "跑外面.txt")), False)


def test_guards():
    section("安全闸")

    dest = os.path.join(ROOT, "dest_guard")
    man = manifest_text()

    def reject(label, members, **kw):
        path = build("bad{}.zip".format(abs(hash(label)) % 10000), members)
        said = grabbed(P.import_zip, path, dest, **kw)
        check("拦住：" + label, isinstance(said, str) and bool(said), True)

    reject("用 .. 往回走", [("../evil.txt", "x"), (M.FILE_NAME, man)])
    reject("反斜杠写的 ..", [("..\\evil.txt", "x"), (M.FILE_NAME, man)])
    reject("绝对路径", [("/etc/passwd", "x"), (M.FILE_NAME, man)])
    reject("带盘符的绝对路径", [("C:/Windows/x.txt", "x"), (M.FILE_NAME, man)])
    reject("完全是另一个盘", [("D:\\别的\\x.txt", "x"), (M.FILE_NAME, man)])

    # 符号链接：外面看着是个普通文件，解到 POSIX 上是指向别处的链接
    link = zipfile.ZipInfo("link")
    link.external_attr = 0o120777 << 16          # 类型位 = 符号链接
    path = os.path.join(ROOT, "symlink.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(M.FILE_NAME, man)
        zf.writestr(link, "/etc/passwd")
    said = grabbed(P.import_zip, path, dest)
    check("拦住：符号链接", isinstance(said, str) and "符号链接" in said, True)

    # 两道闸（用注入的小上限跑，不然得造两千个文件）
    many = build("many.zip", [(M.FILE_NAME, man)] +
                 [("f{}.txt".format(i), "x") for i in range(10)])
    check("拦住：文件数超限",
          isinstance(grabbed(P.import_zip, many, dest, max_files=5), str), True)
    check("没超就放行",
          isinstance(P.import_zip(many, dest, max_files=50), tuple), True)

    big = build("big.zip", [(M.FILE_NAME, man), ("blob.bin", "0" * 4096)])
    check("拦住：解压后体积超限",
          isinstance(grabbed(P.import_zip, big, dest, max_bytes=1024), str), True)
    check("没超就放行",
          isinstance(P.import_zip(big, dest, max_bytes=1024 * 1024), tuple), True)

    # 清单这一层的坏法
    for i, (label, text) in enumerate([
            ("清单不是 JSON", "{不是 json"),
            ("清单顶层不是对象", "[1, 2, 3]"),
            ("少了 entry", json.dumps({k: v for k, v in MANIFEST.items()
                                      if k != "entry"}, ensure_ascii=False)),
            ("版本号看不懂", manifest_text(version="一版")),
            ("接口版本是文字", manifest_text(api_version="1")),
            ("entry 没有冒号", manifest_text(entry="entry.py")),
            ("id 里有斜杠", manifest_text(id="a/b")),
            ("id 里有空格", manifest_text(id="a b")),
            ("id 里有中文", manifest_text(id="示例")),
    ]):
        path = make_zip("badman{}.zip".format(i), text=text)
        check("拦住：" + label,
              isinstance(grabbed(P.import_zip, path, dest), str), True)

    # 包本身的问题
    path = os.path.join(ROOT, "notazip.zip")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("我不是 zip")
    check("拦住：不是 zip",
          isinstance(grabbed(P.import_zip, path, dest), str), True)
    check("拦住：文件不存在",
          isinstance(grabbed(P.import_zip, os.path.join(ROOT, "没有这个.zip"),
                             dest), str), True)
    check("拦住：包里没清单",
          isinstance(grabbed(P.import_zip,
                             build("noman.zip", [("a.txt", "x")]), dest), str), True)


class FakeVar:
    def set(self, value):
        self._v = value

    def get(self):
        return getattr(self, "_v", None)


class FakeApp:
    """Host 需要的那几样，不建真窗口。"""

    def __init__(self):
        self.config_data = {"workspaces": []}
        self.feedback_var = FakeVar()

    def selected_entry(self):
        return None


def test_registry():
    section("接上宿主：装完能被发现、仍是未启用")

    builtin_dir = os.path.join(ROOT, "builtin_plugins")
    user_dir = os.path.join(ROOT, "user_plugins")
    state = os.path.join(ROOT, "state.json")

    # 内置那份
    P.import_zip(make_zip("builtin.zip",
                          text=manifest_text(name="内置的示例")), builtin_dir)
    # 用户那份：同一个 id，另一个版本
    P.import_zip(make_zip("user.zip", text=manifest_text(
        name="用户改过的", version="0.9.9")), user_dir)

    reg = R.Registry(plugin_dirs=[builtin_dir, user_dir], state_file=state)
    found = reg.discover("0.4.0")
    check("同名只出一个", [p.id for p in found], ["demo"])
    check("用户那份优先（名字）", found[0].name, "用户改过的")
    check("用户那份优先（版本）", found[0].version, "0.9.9")
    check("标成不是内置", found[0].builtin, False)
    check("落点指向用户目录",
          os.path.normcase(os.path.dirname(found[0].path)),
          os.path.normcase(user_dir))
    check("装完是未启用（导入 != 信任）", reg.is_enabled(found[0]), False)

    # 只有内置那一份时，标记反过来
    reg2 = R.Registry(plugin_dirs=[builtin_dir], state_file=state)
    only = reg2.discover("0.4.0")
    check("只有内置时标成内置", only[0].builtin, True)
    check("内置那份的名字", only[0].name, "内置的示例")
    check("它也没被启用", reg2.is_enabled(only[0]), False)

    # 装进来的插件跟"手放的目录"是同一种东西：能加载、能注册
    reg.set_enabled(found[0], True)
    host = Host(FakeApp())
    ok, failed = reg.load_enabled(host)
    check("导入的包能加载", (len(ok), len(failed)), (1, 0))
    check("插件注册的动作挂上了",
          [t for t, _ in host.actions("toolbar")], ["嗨"])

    # 换版本之后要重新确认（替换装上去的那条路）
    P.import_zip(make_zip("user2.zip", text=manifest_text(
        name="用户改过的", version="1.0.0")), user_dir)
    reg.discover("0.4.0")
    bumped = reg.get("demo")
    check("换版本后不算启用", reg.is_enabled(bumped), False)
    check("换版本后标成要重新确认", bumped.stale, True)


def main():
    test_find_root()
    test_import_normal()
    test_swap()
    test_guards()
    test_registry()

    print()
    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
