"""探针：启动器自己"感知新版 → 下载最新版 → 打开"这条闭环。

三层，从便宜到贵：

  1. **解清单**（纯函数，不联网）：releases.js 那串文本怎么解、比大小怎么比、
     记录里本平台那一格怎么认、下载地址怎么拼。这一层全靠喂字符串，一个请求
     都不发。
  2. **下载**：起一个本机的 http.server 打假包下来，验字节数对不对得上、
     `.part` 有没有改名、下一次会不会白下第二遍、主源挂掉会不会退到镜像、
     下错长度会不会把半截文件留在盘上。
  3. **界面**（开真窗口）：顶栏那颗「下载最新版」查出来才摆、点下去真的落盘、
     落完真的去"打开"。**一个真的安装包都不跑**——open_artifact 在这一层是
     记账的假函数，真跑它就是在用户机器上装一遍启动器。

    python _probe_selfupdate.py
"""
import functools
import http.server
import os
import shutil
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 下载落在 ~/.claude_tool/downloads 下面，沙箱化一下别碰用户真实的那份（必须在
# import claude_tool.paths 之前）。
PROFILE = "D:/Desktop/tmp/selfup"
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from _probe_common import rebind                       # noqa: E402

from claude_tool import __version__                 # noqa: E402
from claude_tool import selfupdate as S             # noqa: E402
from claude_tool import versions                    # noqa: E402
from claude_tool.paths import DOWNLOAD_DIR          # noqa: E402

SANDBOX = "D:/Desktop/tmp/selfup/work"
SERVE = "D:/Desktop/tmp/selfup/serve"
OK = [True]


def check(label, passed, detail=""):
    OK[0] = OK[0] and passed
    print("{} {} {}".format("OK  " if passed else "FAIL", label, detail))


# ── 1. 解清单 ──────────────────────────────────────────────────────────────

# 照 docs/releases.js 的真形状捏一份（那几条注释和两个基地址都在，记录三条）。
SAMPLE = '''// 注释
window.DOWNLOAD_BASE = "https://gitee.example/dl";
window.MIRROR_BASE = "https://github.example/dl";
window.RELEASES = [
  {
    "version": "0.2.0",
    "date": "2026-09-20",
    "notes": "",
    "claude": "2.1.150",
    "windows": {"file": "ClaudeLauncher-0.2.0-Setup.exe", "size": 123},
    "linux": {"file": "ClaudeLauncher-0.2.0-linux-x86_64.tar.gz", "size": 456},
    "macos": null
  },
  {
    "version": "0.1.0",
    "date": "2026-09-19",
    "notes": "",
    "claude": "2.1.150",
    "windows": {"file": "ClaudeLauncher-0.1.0-Setup.exe", "size": 99},
    "linux": null,
    "macos": null
  }
];
// 尾巴
'''


def listing():
    print("--- 解 releases.js ---")
    entries = versions.releases(SAMPLE)
    check("解出两条，0 号是最新那版",
          [e["version"] for e in entries] == ["0.2.0", "0.1.0"],
          [e.get("version") for e in entries])
    check("newest_claude 还是取 0 号那格",
          versions.newest_claude(SAMPLE) == "2.1.150")
    check("两个基地址都读得到",
          versions._literal(SAMPLE, versions.DOWNLOAD_BASE_MARKER)
          == "https://gitee.example/dl"
          and versions._literal(SAMPLE, versions.MIRROR_BASE_MARKER)
          == "https://github.example/dl")
    check("没有那行赋值时给 None，不是炸",
          versions._literal("啥也没有", versions.DOWNLOAD_BASE_MARKER) is None)
    check("切不出数组就给空表",
          versions.releases("window.RELEASES = 这不是 json;") == [])
    check("空数组也是空表", versions.releases("window.RELEASES = [];") == [])

    print("--- 比大小 ---")
    check("0.2.0 比 0.1.9 新", versions.newer("0.2.0", "0.1.9"))
    check("0.2.0 比 0.1.10 新（按数字比，不按字串）",
          versions.newer("0.2.0", "0.1.10"))
    check("同一版不算新", not versions.newer("0.1.0", "0.1.0"))
    check("0.1 和 0.1.0 是一版（短的补零）",
          not versions.newer("0.1", "0.1.0") and not versions.newer("0.1.0", "0.1"))
    check("本机比网上新，不算落后", not versions.newer("0.1.0", "0.2.0"))
    check("解不出版本号的一律不算新",
          not versions.newer("还没出", "0.1.0") and not versions.newer("0.2.0", ""))

    print("--- 记录里本平台那一格 ---")
    entry = versions.releases(SAMPLE)[0]
    check("windows 那格认得出文件",
          S.asset(entry, "windows")["file"] == "ClaudeLauncher-0.2.0-Setup.exe")
    check("linux 那格也认得出", S.asset(entry, "linux")["size"] == 456)
    check("macos 那格是 null：这一版没给这个平台打包",
          S.asset(entry, "macos") is None)
    check("文件名为空的格子算没打",
          S.asset({"windows": {"file": "  ", "size": 1}}, "windows") is None)
    check("本机认出来的平台在三个格子之内",
          S.platform_key() in versions.PLATFORM_KEYS, S.platform_key())

    print("--- 下载地址怎么拼 ---")
    cell = dict(S.asset(entry, "windows"))
    release = versions.LauncherRelease("0.2.0", True, entry,
                                       "https://gitee.example/dl",
                                       "https://github.example/dl/")
    check("主源 + 版本号 + 文件名，尾巴上的斜杠不多不少",
          S.urls(release, cell) == [
              "https://gitee.example/dl/0.2.0/ClaudeLauncher-0.2.0-Setup.exe",
              "https://github.example/dl/0.2.0/ClaudeLauncher-0.2.0-Setup.exe"],
          S.urls(release, cell))
    pinned = dict(cell, url="https://mirror.example/x.exe")
    check("记录里手填的那个 url 排第一（原样用，不拼版本号和文件名）",
          S.urls(release, pinned) == [
              "https://mirror.example/x.exe",
              "https://gitee.example/dl/0.2.0/ClaudeLauncher-0.2.0-Setup.exe",
              "https://github.example/dl/0.2.0/ClaudeLauncher-0.2.0-Setup.exe"],
          S.urls(release, pinned))
    same = dict(cell, url="https://gitee.example/dl/0.2.0/"
                          "ClaudeLauncher-0.2.0-Setup.exe")
    check("填的正好是主源那条就去重，不会试两遍同一个地址",
          len(S.urls(release, same)) == 2, S.urls(release, same))
    check("镜像留空就只剩一条",
          S.urls(versions.LauncherRelease("0.2.0", True, entry,
                                          "https://gitee.example/dl", None),
                 cell) == ["https://gitee.example/dl/0.2.0/"
                           "ClaudeLauncher-0.2.0-Setup.exe"])

    print("--- launcher() 自己 ---")
    real_get = versions._get
    versions._get = lambda url: SAMPLE
    try:
        got = versions.launcher("0.1.0")
        check("本机 0.1.0 对着清单里 0.2.0：落后",
              got is not None and got.latest == "0.2.0" and got.stale is True,
              None if got is None else (got.latest, got.stale))
        check("两个基地址跟着一起带出来",
              got.base == "https://gitee.example/dl"
              and got.mirror == "https://github.example/dl")
        check("本机已经是最新那版：不落后",
              versions.launcher("0.2.0").stale is False)
        check("本机比清单里所有版本都新：也不落后",
              versions.launcher("9.9.9").stale is False)
        check("本机版本号解不出来就什么都不说",
              versions.launcher("") is None)
        versions._get = lambda url: "window.RELEASES = [];"
        check("清单是空的也不说话", versions.launcher("0.1.0") is None)
        versions._get = lambda url: (_ for _ in ()).throw(OSError("断了"))
        check("拉不到清单不抛异常，返回 None", versions.launcher("0.1.0") is None)
    finally:
        versions._get = real_get


# ── 2. 下载 ────────────────────────────────────────────────────────────────

class Counter(http.server.SimpleHTTPRequestHandler):
    hits = []

    def do_GET(self):
        Counter.hits.append(self.path)
        super().do_GET()

    def log_message(self, *args):
        pass


def serve():
    handler = functools.partial(Counter, directory=SERVE)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:{}".format(httpd.server_address[1])


def downloading():
    print("--- 起个本机的源，打一个假包下来 ---")
    payload = bytes(range(256)) * 800            # 200 KB，够走几轮 CHUNK
    # 摆在版本号那个子目录下：拼出来的地址就是 <基地址>/<版本号>/<文件名>，
    # 跟 Gitee 发行版附件的真实路径一个形状。文件名和那个 404 的都保持 ASCII
    # ——urllib 的请求行按 ASCII 编，中文路径连发都发不出去。
    served = os.path.join(SERVE, "9.9.9", "ClaudeLauncher-9.9.9-Setup.exe")
    os.makedirs(os.path.dirname(served), exist_ok=True)
    with open(served, "wb") as f:
        f.write(payload)
    size = len(payload)
    httpd, base = serve()
    url = base + "/9.9.9/ClaudeLauncher-9.9.9-Setup.exe"
    missing = base + "/9.9.9/nope.exe"
    target = os.path.join(DOWNLOAD_DIR, "ClaudeLauncher-9.9.9-Setup.exe")

    try:
        seen = []
        got = S.fetch([url], target, size, lambda done, total: seen.append((done, total)))
        check("下完返回的就是目标路径", got == target, got)
        check("文件在，大小对得上",
              os.path.exists(target) and os.path.getsize(target) == size)
        check("中间那个 .part 改名了，没留在盘上",
              not os.path.exists(target + ".part"))
        check("进度报到了 100%",
              seen and seen[-1][0] == size and seen[-1][1] == size,
              seen[-1] if seen else None)

        print("--- 第二遍不该白下 ---")
        Counter.hits = []
        S.fetch([url], target, size)
        check("文件已经在盘上、大小也对：一个请求都不发",
              Counter.hits == [], Counter.hits)

        print("--- 主源挂了退到镜像 ---")
        other = os.path.join(DOWNLOAD_DIR, "镜像版.exe")
        Counter.hits = []
        got = S.fetch([missing, url], other, size)
        check("第一个地址 404 之后接着试第二个",
              got == other and os.path.getsize(other) == size)
        check("两个地址都真的试过", len([h for h in Counter.hits if h]) == 2,
              Counter.hits)

        print("--- 长度对不上：清掉重来，不留半截 ---")
        half = os.path.join(DOWNLOAD_DIR, "半截.exe")
        try:
            S.fetch([url], half, size + 1)
            check("长度对不上得抛出来", False, "没抛")
        except IOError as error:
            check("长度对不上抛 IOError，话里带着两个数",
                  str(size) in str(error) and str(size + 1) in str(error),
                  str(error))
        check("半截文件没留在盘上",
              not os.path.exists(half) and not os.path.exists(half + ".part"))

        print("--- 一个源都不通 / 一个地址都没给 ---")
        try:
            S.fetch([missing], os.path.join(DOWNLOAD_DIR, "x.exe"), None)
            check("全挂得抛出最后一个异常", False, "没抛")
        except Exception as error:
            check("全挂抛出的是 HTTPError 那类（不是静默返回）",
                  hasattr(error, "code") and error.code == 404, repr(error))
        try:
            S.fetch([], os.path.join(DOWNLOAD_DIR, "x.exe"), None)
            check("没地址可试得当场说清楚", False, "没抛")
        except ValueError as error:
            check("没地址可试抛 ValueError，话说明白是基地址空的",
                  "基地址" in str(error), str(error))

        print("--- download() 按记录挑产物 ---")
        entry = {"version": "9.9.9", "claude": "",
                 S.platform_key(): {"file": "ClaudeLauncher-9.9.9-Setup.exe",
                                    "size": size},
                 "macos": None}
        release = versions.LauncherRelease("9.9.9", True, entry, base, None)
        os.remove(target)
        Counter.hits = []
        got = S.download(release, None)
        check("按本平台那格下到了同一个文件", got == target)
        check("地址是拿带出来的基地址拼的（没写死官网那个）",
              Counter.hits == ["/9.9.9/ClaudeLauncher-9.9.9-Setup.exe"],
              Counter.hits)
        empty = versions.LauncherRelease("9.9.9", True, {"version": "9.9.9"},
                                         base, None)
        try:
            S.download(empty, None)
            check("这一版没给本平台打包得抛 LookupError", False, "没抛")
        except LookupError as error:
            check("抛的是 LookupError，话里写着没这份包",
                  "这份包" in str(error), str(error))
    finally:
        httpd.shutdown()


# ── 3. 界面 ────────────────────────────────────────────────────────────────

def pump(app, seconds=4.0, until=None):
    """转一会儿事件循环，等 until() 成立。窗口没了就停。"""
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        try:
            app.update()
        except Exception:
            return bool(until and until())
        if until and until():
            return True
        time.sleep(0.02)
    return bool(until and until())


def interface():
    print("--- 界面：那颗「下载最新版」 ---")
    import claude_tool.ui.launcher as L
    from claude_tool.ui.launcher import Launcher

    payload = os.path.join(SERVE, "9.9.9", "ClaudeLauncher-9.9.9-Setup.exe")
    if not os.path.exists(payload):          # 单跑这一节也站得住
        os.makedirs(os.path.dirname(payload), exist_ok=True)
        with open(payload, "wb") as f:
            f.write(bytes(range(256)) * 800)
    size = os.path.getsize(payload)
    httpd, base = serve()
    target = os.path.join(DOWNLOAD_DIR, os.path.basename(payload))
    if os.path.exists(target):
        os.remove(target)

    key = S.platform_key()
    entry = {"version": "9.9.9", "claude": "", "macos": None,
             key: {"file": os.path.basename(payload), "size": size}}
    release = versions.LauncherRelease("9.9.9", True, entry, base, None)

    # 这台机器上有 claude（探针就跑在仓库里）。要验的不是那条路，别让它去起进程。
    real_find = L.find_claude
    real_launcher, real_download = versions.launcher, S.download
    real_open, real_installer = S.open_artifact, S.INSTALLER
    rebind("find_claude", lambda *a, **k: None)
    versions.launcher = lambda local: release

    opened, answered = [], []
    real_ask = L.messagebox.askyesno
    L.messagebox.askyesno = lambda *a, **k: (answered.append(a), True)[1]

    app = Launcher()
    app.update()
    errors = []
    app.report_callback_exception = lambda *a: errors.append(a)
    try:
        check("没查之前顶栏没有那颗胶囊", app._self_pill is None)

        app._start_self_check(manual=True)
        pump(app, until=lambda: app._self_pill is not None)
        pill = app._self_pill
        check("查出落后了：顶栏摆出「下载最新版 9.9.9」",
              pill is not None and pill._text == "下载最新版 9.9.9",
              None if pill is None else pill._text)
        check("反馈栏说了本机是几版、网上是几版",
              "9.9.9" in app.feedback_var.get()
              and __version__ in app.feedback_var.get(), app.feedback_var.get())
        check("这颗胶囊认的是官网那份记录（下载要用）",
              app._self_release is not None
              and app._self_release.latest == "9.9.9")

        print("--- 点下去：下载，然后打开（打开那步是假的）---")
        S.INSTALLER = False
        S.open_artifact = lambda path: (opened.append(path), path)[1]
        pill._command()
        check("点第一下就锁住了，不等第二轮", app._downloading is True)
        pump(app, until=lambda: app._downloading is False)
        check("包真的落在 ~/.claude_tool/downloads 里了",
              os.path.exists(target) and os.path.getsize(target) == size, target)
        check("下完真的去打开了那个文件", opened == [target], opened)
        check("别处（没有安装器）只说下好了、弹个文件夹",
              app.feedback_var.get().startswith("下好了"), app.feedback_var.get())

        print("--- Windows 那条：先问一句，再关自己跑安装包 ---")
        scheduled, quit_called = [], []
        real_after = app.after
        app.after = lambda ms, fn=None, *a: scheduled.append((ms, fn))
        app._on_close = lambda: quit_called.append(True)
        S.INSTALLER = True
        opened[:] = []
        answered[:] = []
        try:
            app._downloaded(target)
        finally:
            app.after = real_after
        check("问了一句「现在装吗」", len(answered) == 1, answered)
        check("答「是」之后才跑安装包", opened == [target], opened)
        check("跑完安装包才安排关自己（800ms 之后）",
              [ms for ms, _ in scheduled] == [800], scheduled)
        for _ms, fn in scheduled:
            if fn:
                fn()
        check("关自己走的是正经 _on_close（内嵌那个要道别再关）",
              quit_called == [True], quit_called)

        print("--- 答「否」：文件留着，不关自己 ---")
        L.messagebox.askyesno = lambda *a, **k: (answered.append(a), False)[1]
        opened[:] = []
        scheduled[:] = []
        app.after = lambda ms, fn=None, *a: scheduled.append((ms, fn))
        try:
            app._downloaded(target)
        finally:
            app.after = real_after
        check("不跑安装包", opened == [], opened)
        check("也不安排关自己", scheduled == [], scheduled)
        check("反馈栏告诉用户文件在哪",
              target in app.feedback_var.get(), app.feedback_var.get())

        print("--- 这一版没给本系统打包 ---")
        app._download_failed(True, "网上的 9.9.9 里没有 windows 这份包")
        check("不弹「下载没成」那种报错，只是告诉一声",
              "这份包" in app.feedback_var.get(), app.feedback_var.get())

        print("--- 后来查出来已经是最新：胶囊收回去 ---")
        versions.launcher = lambda local: versions.LauncherRelease(
            "9.9.9", False, entry, base, None)
        app._start_self_check(manual=True)
        pump(app, until=lambda: app._self_pill is None)
        check("不再落后就把那颗胶囊撤了", app._self_pill is None)
        check("手动查的那次会说一句「不用更新」",
              "不用更新" in app.feedback_var.get(), app.feedback_var.get())

        pump(app, seconds=0.4)
        check("这一轮里界面没抛过异常", not errors, errors)
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        rebind("find_claude", real_find)
        versions.launcher, S.download = real_launcher, real_download
        S.open_artifact, S.INSTALLER = real_open, real_installer
        L.messagebox.askyesno = real_ask
        httpd.shutdown()


def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    os.makedirs(SERVE, exist_ok=True)
    print("沙箱:", PROFILE)
    print()
    listing()
    print()
    downloading()
    print()
    interface()
    shutil.rmtree(PROFILE, ignore_errors=True)
    print()
    print("结果:", "全过" if OK[0] else "有失败")
    return 0 if OK[0] else 1


if __name__ == "__main__":
    sys.exit(main())
