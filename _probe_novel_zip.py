"""小说工作台能打成 zip、装进用户插件目录、再被宿主扫出来。

## 为什么单拎一条

"插件是 `.zip`，导入按钮一装就能用"是这轮定下的分发方式（用户原话：像 MC 的模组
那样）。`_probe_plugin_zip.py` 验的是**装包器**本身，这条验的是**这个插件**的形状
过不过得去——它不是一个文件，是 `novel_assistant/` 一棵小树（`plugin.json` ＋
三个 `.py`），包里那层目录名还得跟清单里的 `id` 对得上（`manifest.read` 那条）。

还顺带验一条容易忽略的：同名时**用户导入的那份盖掉内置的**（`Registry.discover`
后扫的盖先扫的）——内置那份随启动器升级会被覆盖，用户想自己改就得靠这条。

    python _probe_run.py _probe_novel_zip.py

沙箱 USERPROFILE，绝不碰用户真实的 ~/.claude_tool。
"""
import os
import shutil
import sys
import zipfile

from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("novelzip")
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool.plugins import pack, registry                  # noqa: E402
from claude_tool.plugins.host import Host                       # noqa: E402

SRC = os.path.join(ROOT, "claude_tool", "plugins", "novel_assistant")
ZIP = os.path.join(ROOT, "temp", "novel_assistant-0.1.0.zip")
DEST = os.path.join(PROFILE, ".claude_tool", "plugins")

FAILED = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILED.append(label)
    print("{} {:<50} got={!r} want={!r}".format(
        "ok  " if ok else "FAIL", label, got, want))
    return ok


def make_zip():
    """按"发给别人的样子"打包：顶层就是 `novel_assistant/` 那层目录。"""
    files = sorted(name for name in os.listdir(SRC)
                   if os.path.isfile(os.path.join(SRC, name)))
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files:
            zf.write(os.path.join(SRC, name), "novel_assistant/" + name)
    return files


def main():
    if os.path.isdir(PROFILE):
        shutil.rmtree(PROFILE)
    if os.path.isfile(ZIP):
        os.remove(ZIP)

    files = make_zip()
    check("插件目录里有清单 + 三个 .py", files,
          ["__init__.py", "novel_book.py", "novel_panel.py", "plugin.json"])

    pid, place = pack.import_zip(ZIP, DEST)
    check("装包认得这个包、id 跟目录名一致", pid, "novel_assistant")
    check("四个文件都落到用户插件目录里",
          sorted(os.listdir(place)), files)

    # 扫一遍：内置那份在、用户导入的那份也在 -> 同名，用户那份该赢
    reg = registry.Registry()
    reg.discover()
    hit = reg.get(pid)
    check("扫出来了", hit is not None, True)
    check("赢的是用户导入那份（不是内置的）",
          bool(hit and hit.builtin), False)
    check("装进来仍是未启用（来源是 zip 也不放松信任）",
          bool(hit and hit.enabled), False)

    # 启用 -> 真加载 -> 面板注册上了
    reg.set_enabled(hit, True)
    reg.discover()
    host = Host(None)
    ok, failed = reg.load_enabled(host)
    check("加载成功、没报错", [p.id for p in failed], [])
    check("加载的就是这个插件", [p.id for p in ok], [pid])
    check("面板注册上了", len(host.views("workspace_detail")), 1)
    check("用的是当前接口版本", host.api_version, 1)

    print()
    if FAILED:
        print("FAILED {} 项: {}".format(len(FAILED), FAILED))
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    sys.exit(code)
