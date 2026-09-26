"""插件宿主三件套的纯逻辑自检。

**不建窗口、不起进程、不弹任何东西**——验的是 manifest / registry / host 的逻辑，
跟界面无关。用户明确要求过"别打扰我"，所以这条探针永远不进"要上屏"那一档。

覆盖三块：

  manifest  清单读得对不对：缺文件 / 坏 JSON / 缺字段 / id 对不上目录 / 版本号写法 /
            门禁（api_version、min_host）
  registry  发现与信任：坏插件不炸、门禁不过灰着、**discover 不执行插件代码**、
            点启用才 import、版本变了要重新确认
  host      插件那一侧的接口：挂载点注册/撤销、订阅工作区、状态行、坏回调不连累别人

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool 和 launcher.json。
`Registry` 的目录和状态文件都是注入的，一个字节都不写用户那边。

    python _probe_plugins.py
"""
import json
import os
import shutil
import sys

from _probe_common import sandbox  # noqa: E402
ROOT = sandbox("plugins")

# 红线：必须在任何 `import claude_tool.*` 之前。paths.py 在自己 import 那一刻就把
# `~` 冻成模块级常量了（见 _probe_run.py 文件头那段）。
os.environ["USERPROFILE"] = ROOT
os.environ["HOME"] = ROOT
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_tool.plugins import manifest as M   # noqa: E402
from claude_tool.plugins import registry as R   # noqa: E402
from claude_tool.plugins.host import Host       # noqa: E402

PLUGINS = os.path.join(ROOT, "plugins")
STATE = os.path.join(ROOT, "plugins.json")

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<44} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))


def write_plugin(folder, data, entry_src=None):
    """造一个插件目录。data 里没有的必填字段由调用方自己保证。"""
    path = os.path.join(PLUGINS, folder)
    os.makedirs(path, exist_ok=True)
    if data is not None:
        with open(os.path.join(path, M.FILE_NAME), "w", encoding="utf-8") as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f, ensure_ascii=False)
    if entry_src is not None:
        with open(os.path.join(path, "entry.py"), "w", encoding="utf-8") as f:
            f.write(entry_src)
    return path


def good_manifest(**over):
    data = {"id": "good", "name": "好插件", "version": "0.1.0",
            "api_version": M.API_VERSION, "min_host": "0.4.0",
            "entry": "entry.py:register"}
    data.update(over)
    return data


# 入口带一个"被 import 就留痕"的副作用——用来证明 discover 没有执行插件代码。
ENTRY_SRC = '''import os

with open(os.path.join(os.path.dirname(__file__), "touched.txt"), "w") as f:
    f.write("ran")


def register(host):
    host.register_action("toolbar", "打个招呼", lambda: None)
'''

TOUCHED = os.path.join(PLUGINS, "good", "touched.txt")


def section(title):
    print()
    print("── {} ──".format(title))


def test_manifest():
    section("manifest：清单校验")

    path = write_plugin("good", good_manifest())
    man = M.read(path)
    check("读得出 id/name/version",
          (man.id, man.name, man.version), ("good", "好插件", "0.1.0"))
    check("entry 拆成 (文件, 函数)",
          (man.entry_file, man.entry_func), ("entry.py", "register"))
    check("可选字段缺了填空串",
          (man.author, man.description, man.homepage), ("", "", ""))

    # 坏插件：每一种都要抛 BadManifest，且是能读懂的一句话
    for label, folder, data in [
        ("没有 plugin.json", "nofile", None),
        ("不是合法 JSON", "badjson", "{不是 json"),
        ("顶层不是对象", "notobj", "[1, 2, 3]"),
        ("少了必填字段", "missing", {"id": "missing", "name": "x"}),
        ("id 跟目录名对不上", "mismatch", good_manifest(id="别的名字")),
        ("版本号写法看不懂", "badver", good_manifest(version="一版")),
        ("entry 没有冒号", "badentry", good_manifest(entry="entry.py")),
    ]:
        p = write_plugin(folder, data)
        try:
            M.read(p)
            raised = None
        except M.BadManifest as exc:
            raised = str(exc)
        check("坏插件抛错：" + label, isinstance(raised, str) and bool(raised), True)

    check("版本号能吃预发布后缀",
          M.version_tuple("1.2.3-beta.1", "v"), (1, 2, 3))
    check("版本号能吃缺位", M.version_tuple("1", "v"), (1,))
    check("短的一边补 0 再比", M.compare((1, 2), (1, 2, 0)), 0)
    check("低版本比高版本小", M.compare((0, 3, 1), (0, 4)), -1)

    # 门禁两档：接口对不上 / 启动器该升级了
    man = M.read(path)
    check("门禁：接口版本对不上就拦",
          isinstance(M.gate(man, "0.4.0", api_version=999), str), True)
    newer = M.read(write_plugin("needsnew", good_manifest(
        id="needsnew", min_host="9.9.9")))
    check("门禁：min_host 比宿主新就拦",
          isinstance(M.gate(newer, "0.4.0"), str), True)
    check("门禁：都合适就放行", M.gate(man, "0.4.0"), None)
    check("门禁：宿主版本更高也放行", M.gate(man, "0.5.0"), None)


def test_registry():
    section("registry：发现与信任")

    reg = R.Registry(plugin_dir=os.path.join(ROOT, "空目录"), state_file=STATE)
    check("目录不存在时发现结果为空", reg.discover("0.4.0"), [])

    # 重新铺一个干净的插件目录
    shutil.rmtree(PLUGINS, ignore_errors=True)
    write_plugin("good", good_manifest(), ENTRY_SRC)
    write_plugin("broken", "{坏 json")
    write_plugin("rooted", good_manifest(id="rooted", min_host="9.9.9"))
    write_plugin("_skipped", good_manifest(id="_skipped"))
    if os.path.exists(STATE):
        os.remove(STATE)

    reg = R.Registry(plugin_dir=PLUGINS, state_file=STATE)
    found = reg.discover("0.4.0")
    ids = sorted(p.id for p in found)
    check("发现三个（下划线目录不算）", ids, ["broken", "good", "rooted"])
    check("好插件能用", reg.get("good").usable, True)
    check("坏插件记了原因、没炸", bool(reg.get("broken").error), True)
    check("坏插件不算能用", reg.get("broken").usable, False)
    check("门禁不过的灰着", bool(reg.get("rooted").blocked), True)
    check("门禁不过也不算能用", reg.get("rooted").usable, False)

    # 要害：discover 只读 json，不执行插件代码
    check("discover 没有执行插件代码", os.path.exists(TOUCHED), False)

    good = reg.get("good")
    check("新插件默认没启用", reg.is_enabled(good), False)
    check("没信过就是 None", reg.trusted_version("good"), None)

    # 点「启用」——这一步才写状态、才 import
    reg.set_enabled(good, True)
    check("启用之后记下了", reg.is_enabled(good), True)
    check("状态文件记的是当前版本",
          json.load(open(STATE, encoding="utf-8")), {"enabled": {"good": "0.1.0"}})

    fake = FakeApp()
    host = Host(app=fake)
    ok, failed = reg.load_enabled(host)
    check("加载成功一个", len(ok), 1)
    check("失败列表空着", failed, [])
    check("加载这一步执行了插件代码", os.path.exists(TOUCHED), True)
    check("插件注册的动作挂上去了",
          [t for t, _ in host.actions("toolbar")], ["打个招呼"])

    # 版本变了要重新问：不写状态、只靠版本号对不上这一个比较
    write_plugin("good", good_manifest(version="0.2.0"), ENTRY_SRC)
    reg.discover("0.4.0")
    bumped = reg.get("good")
    check("版本对不上 -> 不算启用", reg.is_enabled(bumped), False)
    check("版本对不上 -> 标成要重新确认", bumped.stale, True)

    reg.set_enabled(bumped, True)
    check("重新确认后又是启用的", reg.is_enabled(bumped), True)

    reg.set_enabled(bumped, False)
    check("取消信任之后清干净", reg.trusted_version("good"), None)
    check("取消之后状态文件是空的", json.load(open(STATE, encoding="utf-8")),
          {"enabled": {}})

    # 一个坏插件不该拖垮好的
    reg.set_enabled(reg.get("good"), True)
    ok, failed = reg.load_enabled(Host(FakeApp()))
    check("坏插件被跳过、好的照跑", (len(ok), len(failed)), (1, 0))


def test_load_errors():
    section("registry：加载失败要照实记")

    shutil.rmtree(PLUGINS, ignore_errors=True)
    # 入口文件根本不存在
    write_plugin("gone", good_manifest(id="gone"))
    # 入口函数没写
    write_plugin("nofunc", good_manifest(id="nofunc", entry="entry.py:register"),
                 "def 别的名字(host):\n    pass\n")
    # 插件自己在 import 时就炸
    write_plugin("boom", good_manifest(id="boom"),
                 "raise RuntimeError('我自己炸的')\n")

    reg = R.Registry(plugin_dir=PLUGINS, state_file=os.path.join(ROOT, "s2.json"))
    if os.path.exists(reg.state_file):
        os.remove(reg.state_file)
    reg.discover("0.4.0")
    for pid in ("gone", "nofunc", "boom"):
        try:
            reg.load(reg.get(pid), Host(None))
            err = None
        except Exception as exc:            # noqa: BLE001
            err = type(exc).__name__
        check("加载 {} 抛错".format(pid), err, "RuntimeError")

    # 三个都启用，然后批量加载：一个都起不来，但宿主不能崩、失败要逐个记下
    for pid in ("gone", "nofunc", "boom"):
        reg.set_enabled(reg.get(pid), True)
    ok, failed = reg.load_enabled(Host(None))
    check("load_enabled 收齐三个失败", (len(ok), len(failed)), (0, 3))
    check("失败原因记在插件自己身上",
          all(p.load_error for p in failed), True)
    check("load_error 里带了异常名",
          all("RuntimeError" in p.load_error for p in failed), True)


def test_host():
    section("host：插件那一侧看到的接口")

    fake = FakeApp()
    host = Host(app=fake)
    check("身份：版本 / 接口版本",
          (host.version, host.api_version), ("0.4.0", M.API_VERSION))

    undo = host.register_action("toolbar", "甲", lambda: None)
    host.register_action("workspace_detail", "乙", lambda: None)
    check("toolbar 收到一个", [t for t, _ in host.actions("toolbar")], ["甲"])
    check("别的挂载点不受影响",
          [t for t, _ in host.actions("workspace_detail")], ["乙"])
    undo()
    check("撤销之后没了", host.actions("toolbar"), [])
    check("其余还在", [t for t, _ in host.actions("workspace_detail")], ["乙"])

    try:
        host.register_action("不存在的地方", "丙", lambda: None)
        bad = None
    except ValueError as exc:
        bad = str(exc)
    check("乱挂载点要报错", isinstance(bad, str), True)

    # 挂载点变了要叫一声宿主
    bums = []
    host.set_mount_hook(lambda: bums.append(1))
    host.register_action("toolbar", "丁", lambda: None)
    check("注册动作触发了重建", len(bums), 1)
    host.register_settings_page("插件页", lambda parent: None)
    check("加设置页也触发重建", len(bums), 2)

    # 画面板：注册 / 撤销 / 挂到只能放菜单的地方要报错
    undo_view = host.register_view("workspace_detail",
                                   lambda parent, entry: None)
    check("画面板也触发重建", len(bums), 3)
    check("view 挂上去了", len(host.views("workspace_detail")), 1)
    check("view 跟动作分开放",
          [t for t, _ in host.actions("workspace_detail")], ["乙"])
    undo_view()
    check("撤销之后 view 没了", host.views("workspace_detail"), [])
    try:
        host.register_view("workspace_row", lambda parent, entry: None)
        badview = None
    except ValueError as exc:
        badview = str(exc)
    check("菜单这种地方不能画面板", isinstance(badview, str), True)

    # 订阅工作区变化
    seen = []
    unsub = host.on_workspace_change(lambda entry: seen.append(entry))
    check("订阅那一刻不回调", seen, [])
    fake.selected = {"name": "甲本", "path": "D:/x"}
    host.notify_workspace_change(fake.selected)
    check("通知送到了订阅者", seen, [{"name": "甲本", "path": "D:/x"}])
    unsub()
    host.notify_workspace_change(None)
    check("撤销之后不再收", len(seen), 1)

    # 一个订阅者炸了不能连累别的、更不能把宿主带崩
    log = []
    host.set_log_sink(log.append)
    order = []
    host.on_workspace_change(lambda e: order.append("先") or 1 / 0)
    host.on_workspace_change(lambda e: order.append("后"))
    host.notify_workspace_change("x")
    check("坏订阅者没拦住后面的", order, ["先", "后"])
    check("坏订阅者被记了一笔", len(log), 1)
    check("状态行走的是接进来的 sink", log and "回调出错" in log[0], True)

    # 数据接口
    check("工作区列表来自宿主内存",
          [w["name"] for w in host.workspaces()], ["甲"])
    check("当前选中那条", host.current_workspace(), fake.selected)
    check("没接界面时 current 是 None", Host(None).current_workspace(), None)

    # log 的兜底：没接 sink 就写主窗状态行
    host2 = Host(FakeApp())
    host2.log("喂")
    check("没接 sink 就写 feedback_var", host2._app.feedback_var.get(), "喂")


class FakeVar:
    def __init__(self):
        self._v = None

    def set(self, v):
        self._v = v

    def get(self):
        return self._v


class FakeApp:
    """把 Host 需要的那几样凑齐，不建真窗口。"""

    def __init__(self):
        self.config_data = {"workspaces": [{"name": "甲", "path": "D:/x"}]}
        self.feedback_var = FakeVar()
        self.selected = None

    def selected_entry(self):
        return self.selected


def main():
    test_manifest()
    test_registry()
    test_load_errors()
    test_host()

    print()
    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
