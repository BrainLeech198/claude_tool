"""探针：没装 claude 时那个"帮你装"的面板。

三层，从便宜到贵：

  1. **逻辑**（纯函数，不装任何东西、不联网）：三种平台各能走哪几条路、前提不满
     足的那条会不会乖乖灰掉、命令文本是不是官方那几条、Node 版本够不够。这一层
     靠的是 routes() / checkup() / find_claude() 都收假的 platform、which 进来，
     所以不用去改这台机器的 PATH，也不用真装一次 claude。
  2. **流式输出**：拿一条无害命令跑一遍，看那个读数循环能不能把中文原样吐出来、
     退出码对不对、拉不起来的程序会不会变成 None 而不是炸掉。
  3. **面板**（开真窗口）：横幅上那颗「帮我装 claude」开出来的东西长什么样、几
     条路是不是照 ready 灰的、装之前问不问、装的时候锁不锁、装完输出进不进得来
     那一块。**run_stream 在这一层是假的**——真跑就是在这台机器上装一次
     claude，那是用户点的事，不是探针的事。

    python _probe_install.py
"""
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 原生脚本那半要往 ~/.local/bin 里找一个不存在的文件，沙箱化一下别碰用户真实的
# 那份（跟 _probe_autonomy.py 一个路数，必须在 import claude_tool.paths 之前）。
from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("install")
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from _probe_common import rebind  # noqa: E402

from claude_tool import claude as C                # noqa: E402
from claude_tool import install as I               # noqa: E402
from claude_tool.paths import LOCAL_BIN            # noqa: E402

SANDBOX = os.path.join(sandbox("install"), "work")
OK = [True]


def check(label, passed, detail=""):
    OK[0] = OK[0] and passed
    print("{} {} {}".format("OK  " if passed else "FAIL", label, detail))


def which_maker(*present):
    """一个假的 which：只认传进来的这几个名字。"""
    return lambda name: ("/fake/bin/" + name) if name in present else None


def flat(text):
    """把详情压成一行：护栏那两条是多行脚本，原样打出来会把探针自己的输出拆散。"""
    return " ".join(str(text).split())


def by_name(routes):
    return {route["name"]: route for route in routes}


def names(routes):
    return [route["name"] for route in routes]


def commands():
    print("--- 三种平台各摆哪几条 ---")
    win = I.routes("win32", which_maker("winget", "npm"), node=(20, 11, 0))
    mac = I.routes("darwin", which_maker("brew", "npm"), node=(20, 11, 0))
    linux = I.routes("linux", which_maker("curl", "npm"), node=(20, 11, 0))

    # 顺序就是面板上从上到下的顺序，最后一条永远是便携版 Node——它是垫底的：
    # 前面几条都走不通、机器上又没有 Node 的时候才轮到它。
    check("Windows：原生脚本 / winget / npm / 便携版 Node",
          names(win) == ["官方原生脚本", "winget", "npm", "便携版 Node + npm"],
          names(win))
    check("macOS：原生脚本 / Homebrew / npm / 便携版 Node",
          names(mac) == ["官方原生脚本", "Homebrew", "npm", "便携版 Node + npm"],
          names(mac))
    check("Linux：原生脚本 / npm / 便携版 Node（Linux 上没有别的包管理器这一层）",
          names(linux) == ["官方原生脚本", "npm", "便携版 Node + npm"],
          names(linux))
    check("便携版 Node 排在最后（前几条都不通时才轮到它）",
          all(names(r)[-1] == "便携版 Node + npm"
              for r in (win, mac, linux)))
    check("它不需要任何前提（前几条都不通的那台机器上它也得能点）",
          by_name(win)["便携版 Node + npm"]["ready"] is True
          and by_name(linux)["便携版 Node + npm"]["ready"] is True)
    check("第一条永远是原生脚本（官方推荐那条）",
          all(r[0]["name"] == "官方原生脚本" for r in (win, mac, linux)))

    print("--- 命令文本照官方核对过的来 ---")
    check("Windows 原生脚本用 irm | iex",
          by_name(win)["官方原生脚本"]["show"] == "irm https://claude.ai/install.ps1 | iex",
          by_name(win)["官方原生脚本"]["show"])
    check("别处用 curl | bash",
          by_name(linux)["官方原生脚本"]["show"]
          == "curl -fsSL https://claude.ai/install.sh | bash",
          by_name(linux)["官方原生脚本"]["show"])
    check("winget 带上 --id 和那两个 accept",
          by_name(win)["winget"]["argv"][:3] == ["winget", "install", "--id"]
          and "Anthropic.ClaudeCode" in by_name(win)["winget"]["argv"]
          and "--accept-package-agreements" in by_name(win)["winget"]["argv"],
          by_name(win)["winget"]["argv"])
    check("brew 那条是 --cask claude-code",
          by_name(mac)["Homebrew"]["argv"] == ["brew", "install", "--cask", "claude-code"],
          by_name(mac)["Homebrew"]["argv"])
    check("npm 那条是装官方那个包",
          by_name(linux)["npm"]["argv"] == ["npm", "install", "-g", "@anthropic-ai/claude-code"],
          by_name(linux)["npm"]["argv"])
    # PowerShell 那条得真的跑起来：不带 -NoProfile 会去读用户的 profile，
    # 装一半卡在别人写的脚本里就说不清了。
    check("Windows 原生脚本走 powershell -NoProfile",
          by_name(win)["官方原生脚本"]["argv"][:2] == ["powershell", "-NoProfile"],
          by_name(win)["官方原生脚本"]["argv"])

    print("--- 原生脚本那条：摆出来的和真跑的不是一回事（故意的）---")
    # show 得短到塞得进面板那格单行 Entry、还得是用户能拿去别处照敲的那句；
    # argv 得先验一眼取回来的是不是脚本——不然一张网页就那么进了 iex，0.2.0 上
    # 报回来的就是那个（claude.ai/install.ps1 从国内直连是 302 到一张地区限制
    # 页，HTTP 状态还是 200）。两条护栏各自的细节在 _probe_instguard.py 里撞。
    win_native = by_name(win)["官方原生脚本"]
    nix_native = by_name(linux)["官方原生脚本"]
    check("Windows 那条真跑的不是 `irm | iex` 那种裸管道",
          "| iex" not in " ".join(win_native["argv"]),
          flat(win_native["argv"][-1])[:70])
    check("Linux 那条真跑的不是 `curl | bash` 那种裸管道",
          "| bash" not in " ".join(nix_native["argv"]),
          flat(nix_native["argv"][-1])[:70])
    check("Windows 那条走的是 install.ps_guard",
          win_native["argv"][:3] == ["powershell", "-NoProfile", "-Command"]
          and win_native["argv"][3] == I.ps_guard(), win_native["argv"][:3])
    check("Linux 那条走的是 install.sh_guard",
          nix_native["argv"] == ["bash", "-c", I.sh_guard()], nix_native["argv"][:2])
    check("摆给用户看的那行还是官方原样（他要拿去别处敲的）",
          win_native["show"] == "irm {} | iex".format(I.NATIVE_PS1)
          and nix_native["show"] == "curl -fsSL {} | bash".format(I.NATIVE_SH),
          (win_native["show"], nix_native["show"]))
    check("说明里先讲明白会验一眼（别让它看着像偷偷多做了什么）",
          "验一眼" in win_native["why"] and "验一眼" in nix_native["why"],
          win_native["why"])
    check("多这一层没把前提连累掉（Windows 仍不需要前提、Linux 仍只要 curl）",
          win_native["ready"] is True and win_native["needs"] == "不需要装别的东西")

    print("--- 前提不满足的那条得灰掉，理由摆在明处 ---")
    bare_win = by_name(I.routes("win32", which_maker(), node=None))
    check("没 winget 的那台，winget 这条走不了",
          bare_win["winget"]["ready"] is False, bare_win["winget"])
    check("理由是人话，不是空白",
          "没有 winget" in bare_win["winget"]["needs"], bare_win["winget"]["needs"])
    check("原生脚本在 Windows 上不受影响（irm 是自带的）",
          bare_win["官方原生脚本"]["ready"] is True)
    check("没 Node 的那台，npm 这条走不了",
          bare_win["npm"]["ready"] is False and "Node" in bare_win["npm"]["needs"],
          bare_win["npm"]["needs"])

    bare_nix = by_name(I.routes("linux", which_maker("npm"), node=(20, 11, 0)))
    check("没 curl 的那台，Linux 原生脚本这条走不了",
          bare_nix["官方原生脚本"]["ready"] is False
          and "curl" in bare_nix["官方原生脚本"]["needs"],
          bare_nix["官方原生脚本"]["needs"])
    check("Windows 上不拿 curl 卡原生脚本",
          by_name(I.routes("win32", which_maker(), node=None))["官方原生脚本"]["ready"] is True)

    print("--- Node 版本卡在 18 ---")
    old = by_name(I.routes("linux", which_maker("npm"), node=(16, 20, 0)))
    new = by_name(I.routes("linux", which_maker("npm"), node=(18, 0, 0)))
    check("16 太旧，走不了", old["npm"]["ready"] is False, old["npm"]["needs"])
    check("理由里带上现在这版是多少", "v16.20.0" in old["npm"]["needs"],
          old["npm"]["needs"])
    check("正好 18 就够用", new["npm"]["ready"] is True, new["npm"]["needs"])
    # npm 在、node 问不出话：nvm 那种 shim 有时会这样。这种不该被当成"太旧"灰掉，
    # 不然唯一一条现成的路就白堵了。
    shim = by_name(I.routes("linux", which_maker("npm"), node=None))
    check("有 npm 但读不出 node 版本 -> 当作能用", shim["npm"]["ready"] is True,
          shim["npm"]["needs"])


def guards():
    print("--- 两条护栏本身：地址拼进去了没、拒绝时说不说人话 ---")
    ps = I.ps_guard()
    sh = I.sh_guard()
    check("PowerShell 那条带着官方那个地址", I.NATIVE_PS1 in ps, flat(ps)[:70])
    check("bash 那条带着官方那个地址", I.NATIVE_SH in sh, flat(sh)[:70])
    check("两条都没漏出占位符 __URL__", "__URL__" not in ps and "__URL__" not in sh)
    check("Windows 那条会把网页挡下来",
          "<!DOCTYPE" in ps and "不是安装脚本" in ps and "没有执行它" in ps)
    check("Linux 那条会把网页挡下来（认 shebang）",
          "'#!'" in sh and "不是安装脚本" in sh and "没有执行它" in sh)
    # 光拒掉还不够：用户看到的得是一句能照做的下一步，而不是又一屏看不懂的东西。
    check("两条都告诉用户去挂代理 / 换别的路",
          "代理" in ps and "代理" in sh and "npm" in ps and "npm" in sh)
    check("地址能换（探针就是靠这个把假 URL 喂进去的）",
          "http://x/y" in I.ps_guard("http://x/y")
          and "http://x/y" in I.sh_guard("http://x/y"))


def versions():
    print("--- 版本串认得出来 ---")
    check("v20.11.0 -> (20, 11, 0)", I.parse_version("v20.11.0") == (20, 11, 0))
    check("裸的 20.11.0 也认", I.parse_version("20.11.0") == (20, 11, 0))
    check("前面带别的字也认", I.parse_version("node v20.11.0\n") == (20, 11, 0))
    check("字节串也认（subprocess 回来的就是字节）",
          I.parse_version(b"v18.19.1\r\n") == (18, 19, 1))
    check("一个数字都没有返回 None", I.parse_version("没装") is None)
    check("空串返回 None", I.parse_version("") is None)
    check("写给人看的那一份带 v", I.version_text((20, 11, 0)) == "v20.11.0")
    check("平台名是人话", I.platform_name("win32") == "Windows"
          and I.platform_name("darwin") == "macOS"
          and I.platform_name("linux") == "Linux")


def finder():
    print("--- 装完当场认不认得出来 ---")
    check("PATH 里有就直接返回它",
          C.find_claude(which_maker("claude")) == "/fake/bin/claude")

    # 真建一个文件到沙箱的 ~/.local/bin 底下，走 find_claude 那条路径。
    bin_dir = os.path.join(PROFILE, ".local", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    real_local_bin = LOCAL_BIN
    # LOCAL_BIN 是 import 时算出来的，这时候已经把 USERPROFILE 指到沙箱了，
    # 所以这两个路径必须是一回事——不是的话说明沙箱没设上，下面那些断言都不算数。
    check("沙箱设上了（LOCAL_BIN 落在沙箱里）",
          os.path.normcase(real_local_bin) == os.path.normcase(bin_dir),
          "{} vs {}".format(real_local_bin, bin_dir))

    check("PATH 里没有、~/.local/bin 里也没有 -> None",
          C.find_claude(which_maker()) is None)
    target = os.path.join(bin_dir, "claude.exe")
    with open(target, "w", encoding="utf-8") as f:
        f.write("假的")
    check("PATH 里没有、~/.local/bin 里躺着 -> 认它",
          C.find_claude(which_maker()) == target, C.find_claude(which_maker()))
    check("清掉之后又回到 None",
          (os.remove(target) or True) and C.find_claude(which_maker()) is None)
    check("claude_exe 也跟着走同一条路",
          C.claude_exe(which_maker("claude")) == "/fake/bin/claude")

    # 便携版 Node 那条路：npm 的 shim 落在前缀根上（Windows 就是 NODE_DIR/claude.cmd，
    # 别的平台在它底下的 bin/）。装完那一刻 PATH 照样是旧的，认不出这个 shim 的话
    # 用户会觉得"提示装成功了、可它还说没装"——0.3.0 上就是这么报回来的。
    node_dir = os.path.join(PROFILE, ".claude_tool", "node")
    check("NODE_DIR 落在沙箱里", os.path.normcase(C.NODE_DIR)
          == os.path.normcase(node_dir), C.NODE_DIR)
    for sub, name in (("", "claude.cmd"), ("bin", "claude")):
        d = os.path.join(node_dir, sub) if sub else node_dir
        os.makedirs(d, exist_ok=True)
        shim = os.path.join(d, name)
        with open(shim, "w", encoding="utf-8") as f:
            f.write("假的 shim")
        check("PATH 里没有、便携 Node 目录里躺着 {} -> 认它".format(
            os.path.join(sub, name) if sub else name),
            C.find_claude(which_maker()) == shim, C.find_claude(which_maker()))
        os.remove(shim)


def upgrade():
    """升级那条线：认得出这份 claude 是怎么装的，认出来才给对应的升级命令。

    这一段全是纯数据——不碰界面、不跑任何命令。面板那半截（措辞、预选、跑完
    要不要再查一次版本）在 panel() 里。
    """
    print("--- 认这份 claude 是哪条路装的 ---")
    node_dir = os.path.join(PROFILE, ".claude_tool", "node")
    cases = [
        ("winget 那个包目录里", os.path.join(
            PROFILE, "AppData", "Local", "Microsoft", "WinGet", "Packages",
            "Anthropic.ClaudeCode_8wekyb3d8bbwe", "claude.exe"), "winget"),
        ("macOS 的 Caskroom 里", "/opt/homebrew/Caskroom/claude/1.2.3/claude", "brew"),
        ("Linuxbrew 的 Cellar 里", "/home/me/.linuxbrew/Cellar/claude/1.0/bin/claude",
         "brew"),
        ("npm 的 node_modules 里",
         "/usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js", "npm"),
        ("~/.local/bin 里（官方原生脚本）", "/home/me/.local/bin/claude", "native"),
        ("启动器自己的便携 Node 目录里",
         os.path.join(node_dir, "claude.cmd"), "node"),
        ("一个没特征的地方", "C:/tools/claude/claude.exe", None),
        ("压根不知道在哪儿", None, None),
    ]
    for label, path, want in cases:
        got = I.installed_via(path, node_dir=node_dir)
        check("{} -> {}".format(label, want), got == want, got)

    # 便携 Node 那条的 shim 也是 npm 生成的那种 .cmd，光看扩展名会认成系统 npm
    # 装的（然后让用户拿一个不存在的 npm 去升级）。这条钉住那个先后顺序。
    check("便携 Node 那份不会被认成「系统 npm 装的」",
          I.installed_via(os.path.join(node_dir, "claude.cmd"),
                          node_dir=node_dir) == "node")

    print()
    print("--- 升级命令：按装法给对应的那条 ---")
    win = dict(platform="win32", which=which_maker("claude", "winget", "npm"))
    by_key = dict((r["key"], r) for r in I.routes(claude_path="/x/claude.exe",
                                                  **win))
    check("装的时候 winget 那条是 install",
          "winget install --id" in by_key["winget"]["show"],
          by_key["winget"]["show"])
    up = dict((r["key"], r) for r in I.routes(upgrade=True,
                                             claude_path="/x/claude.exe", **win))
    check("升级的时候 winget 那条变成 upgrade",
          "winget upgrade --id" in up["winget"]["show"], up["winget"]["show"])
    check("原生脚本那条命令不变（它本来就是「再跑一遍」）",
          up["native"]["show"] == by_key["native"]["show"], up["native"]["show"])
    check("npm 那条命令不变（包再装一遍就是最新）",
          up["npm"]["show"] == by_key["npm"]["show"], up["npm"]["show"])
    check("升级模式下每条都带上了 key（认装法全靠它）",
          all(r["key"] for r in up.values()), list(up))

    print()
    print("--- 认出来了就把那条预选上，其余各写一句 ---")
    winget_path = os.path.join(
        PROFILE, "AppData", "Local", "Microsoft", "WinGet", "Packages",
        "Anthropic.ClaudeCode_8wekyb3d8bbwe", "claude.exe")
    routes = I.routes(upgrade=True, claude_path=winget_path, **win)
    picked = [r for r in routes if r["pick"]]
    check("只预选一条", len(picked) == 1, [r["name"] for r in picked])
    check("预选的正是 winget 那条（这份就是它装的）",
          picked and picked[0]["key"] == "winget",
          [r["name"] for r in picked])
    check("预选那条的说明承认「你现在这份就是这条装的」",
          picked and "你现在这份" in picked[0]["note"], picked and picked[0]["note"])
    others = [r for r in routes if not r["pick"]]
    check("其余每条都写着选它会在旁边再装一份",
          all("再装一份" in r["note"] for r in others),
          [(r["name"], r["note"]) for r in others])

    print()
    print("--- 认不出来就不替用户选 ---")
    routes = I.routes(upgrade=True, claude_path="C:/tools/claude/claude.exe", **win)
    check("一条都不预选", not any(r["pick"] for r in routes),
          [r["name"] for r in routes if r["pick"]])
    check("也就不摆「不是这条装的」那种话（没根据，说出来是瞎猜）",
          not any(r["note"] for r in routes), [r["note"] for r in routes])
    routes = I.routes(upgrade=True, claude_path=None, **win)
    check("压根不知道 claude 在哪儿时也不预选", not any(r["pick"] for r in routes))

    print()
    print("--- 装的时候照旧默认第一条能走的 ---")
    routes = I.routes(**win)
    check("装的时候第一条（官方原生脚本）预选",
          routes[0]["pick"] and not any(r["pick"] for r in routes[1:]),
          [r["name"] for r in routes if r["pick"]])

    print()
    print("--- 便携版 Node 那条路 ---")
    sh = I.node_setup("darwin")
    check("非 Windows 那份是给 bash 的", "uname" in sh, flat(sh)[:60])
    ps = I.node_setup("win32")
    check("Windows 那份是给 PowerShell 的", "Invoke-RestMethod" in ps, ps[:60])
    check("两个源都写进去了（官方连不上退 npmmirror）",
          "nodejs.org" in ps and "npmmirror" in ps)
    check("npm 装到 node 自己那个目录里（--prefix，不然 shim 找不到 node）",
          "--prefix" in ps and "--prefix" in sh)
    check("装的还是官方那个包", I.NPM_PACKAGE in ps and I.NPM_PACKAGE in sh)
    check("这条自己定的超时比默认那条长（要下几十兆）",
          by_key_timeout("node") > I.INSTALL_TIMEOUT, by_key_timeout("node"))
    check("命令那一格摆的是 npm 那条（能在别处照着敲）",
          "npm" in I._node_show("win32") and "npm" in I._node_show("darwin"))


def by_key_timeout(key):
    route = next(r for r in I.routes() if r["key"] == key)
    return route["timeout"] or I.INSTALL_TIMEOUT


def checkup_rows():
    print("--- 体检表 ---")
    rows = I.checkup("win32", which_maker("winget"), node=(20, 11, 0),
                     claude_path="C:/x/claude.exe")
    table = dict((row[0], row[1]) for row in rows)
    check("有 claude 时报出它在哪儿", table.get("claude") == "C:/x/claude.exe",
          table)
    check("Windows 上报 winget", "winget" in table, list(table))
    mac_table = dict((r[0], r[1]) for r in
                     I.checkup("darwin", which_maker("brew"), node=None))
    check("macOS 那行是 brew，不摆 winget", "brew" in mac_table
          and "winget" not in mac_table, list(mac_table))
    check("没装 Node 就写没装", mac_table.get("Node") == "没装", mac_table)
    check("系统那行是人话", mac_table.get("系统") == "macOS", mac_table)

    # 这一条是给"装完但 PATH 没刷"那个场景的：claude 找不到，但 ~/.local/bin 里
    # 有。启动器得能认出这种，别让用户以为没装上。
    bin_dir = os.path.join(PROFILE, "local2")
    os.makedirs(bin_dir, exist_ok=True)
    with open(os.path.join(bin_dir, "claude"), "w", encoding="utf-8") as f:
        f.write("假的")
    rows = I.checkup("linux", which_maker(), node=None, bin_dir=bin_dir)
    table = dict((row[0], row[1]) for row in rows)
    check("找不着 claude 时如实说还没找到", table.get("claude") == "还没找到", table)
    check("但 ~/.local/bin 里躺着的那份得单独说一句",
          "~/.local/bin" in table and "PATH" in table.get("~/.local/bin", ""),
          table)


def streaming():
    print("--- 流式输出 ---")
    lines = []
    code = I.run_stream([sys.executable, "-c",
                         "print('第一行'); print('第二行'); print('中文也不该乱')"],
                        lines.append)
    check("退出码是 0", code == 0, code)
    check("三行都到了", len(lines) >= 3, lines)
    check("中文没乱", any("中文也不该乱" in line for line in lines), lines)
    check("按行分的（不是一整坨）", lines[0] == "第一行", lines[:2])

    lines = []
    code = I.run_stream([sys.executable, "-c",
                         "import sys; print('错的那一路'); sys.exit(3);"], lines.append)
    check("非零退出码如实报回来", code == 3, code)
    check("stderr 也进了同一个流（安装脚本的报错不能吞掉）",
          any("错的那一路" in line for line in lines), lines)

    lines = []
    code = I.run_stream(["根本没这个程序xyz"], lines.append)
    check("拉不起来的返回 None 而不是炸掉", code is None, code)
    check("拉不起来的原因摆给用户看", lines and "拉不起来" in lines[0], lines)

    print("--- 不会为了等输出卡死 ---")
    # 只吐一行、然后一直不说话：读数循环得照常把这行交出来，并且不阻塞到最后
    # 那个 timeout（这里给 5 秒，正常应该秒回）。
    import time
    lines = []
    started = time.monotonic()
    code = I.run_stream([sys.executable, "-c", "print('出来了')"], lines.append,
                        timeout=5)
    check("一条立即结束的命令不会等到超时才回来",
          time.monotonic() - started < 4, "{:.1f}s".format(time.monotonic() - started))
    check("那行拿到了", lines == ["出来了"], lines)
    check("退出码 0", code == 0, code)


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def by_class(parent, name):
    return [w for w in walk(parent) if w.winfo_class() == name]


def labels(parent):
    import tkinter as tk
    return [str(w.cget("text")) for w in walk(parent) if isinstance(w, tk.Label)]


def fake_route(name, why="说明。", needs="前提", ready=True,
               argv=("x",), show="x", pick=False, note="", timeout=None,
               **extra):
    """手艺活的一条路，字段跟 install._route 摆出来的一模一样。

    **每加一个字段都得在这儿补上默认值**：面板是照着 routes 给的这份数据画的，
    少一个键当场 KeyError，而且探针里看着像"面板坏了"，其实只是这份假数据旧了。
    extra 是留给各节按需覆盖的，字段名写错也不会静默吞掉。
    """
    route = {"name": name, "why": why, "needs": needs, "ready": ready,
             "argv": list(argv), "show": show, "pick": pick, "note": note,
             "timeout": timeout}
    route.update(extra)
    return route


def open_install_panel(app, updating=False):
    """从主窗口那颗按钮把面板开出来，返回那个 Toplevel。

    updating 走的是同一颗「帮我装 claude」（横幅只在没装 claude 时挂），
    所以面板本身的两种模式分开测：这儿只管把窗口开出来，措辞那几处按 updating
    断言。
    """
    button = next((b for b in walk(app)
                   if getattr(b, "_text", "") == "帮我装 claude"), None)
    if button is None:
        return None, False
    primary = bool(getattr(button, "_primary", False))
    # 面板本身是 _open_install_dialog(updating=...)，主窗口那边没挂 updating；
    # 升级模式直接叫它，测的是同一个面板的另一种措辞。
    if updating:
        app._open_install_dialog(updating=True)
    else:
        button._command()
    want = "帮你{} claude".format("升级" if updating else "装")
    return next((w for w in app.winfo_children()
                 if getattr(w, "title", None)
                 and w.title() == want), None), primary


def panel():
    print("--- 横幅上那颗按钮开出来什么 ---")
    import threading
    import time

    import claude_tool.ui.dialogs as D
    import claude_tool.ui.launcher as L
    from claude_tool.ui.launcher import Launcher

    # 装着装着不让那扇窗口跑完：测"正在装"那一刻的状态（锁住的单选、点第二次
    # 不该再跑一遍）都发生在这中间。
    gate = threading.Event()

    # 主窗口和面板都会自己去问「这台机器装没装 claude」。这台是有 claude 的
    # （探针就跑在仓库里，PATH 上有），可要验的正是"没装"那一条路，所以两边都
    # 按没装来。find_claude 自己认不认得出，上面 finder() 那节已经验过了。
    real_launcher_find, real_dialog_find = L.find_claude, D.find_claude
    real_routes, real_stream = I.routes, I.run_stream
    rebind("find_claude", lambda *a, **k: None)

    app = Launcher()
    app.update()
    app.track_running = lambda *a, **k: None
    app._watch_terminal = lambda *a, **k: None
    # 界面自己抛出来的异常默认是打一行到 stderr——打包成 pythonw 之后那行没人
    # 看得见。这里全收下来，最后那节拿它验"关窗之后读数的回调还会不会醒过来"。
    errors = []
    app.report_callback_exception = lambda *a: errors.append(a)

    # 面板上每一处 messagebox 都换成记账的。**一个真的模态框都不留**：探针里没有
    # 人去点它，弹出来就是永久停在那儿（踩过：最后一节手滑把真的 askyesno 装回去
    # 了，第三次点「开始安装」当场卡死）。
    asked, complained = [], []
    answer = [True]       # 问「装吗」的时候答什么，各节自己翻
    real_boxes = {}
    for name in ("askyesno", "showerror", "showinfo", "showwarning"):
        real_boxes[name] = getattr(D.messagebox, name)
    D.messagebox.askyesno = lambda *a, **k: (asked.append(a), answer[0])[1]
    D.messagebox.showerror = lambda *a, **k: complained.append(a)
    D.messagebox.showinfo = lambda *a, **k: complained.append(a)
    D.messagebox.showwarning = lambda *a, **k: complained.append(a)

    try:
        check("没找到 claude 时横幅挂着", app.banner.winfo_ismapped())
        old = [b for b in walk(app) if getattr(b, "_text", "") == "一键安装"]
        check("那颗 winget 一键装撤掉了", not old, [b._text for b in old])
        check("_install_claude 也跟着撤了", not hasattr(app, "_install_claude"))

        print()
        print("--- 装完成功了：横幅得自己撤掉 ---")
        # 用户点完装、npm 报成功之后走的就是这一下。横幅不撤 = 界面上还在说
        # 「没找到 claude，装好才能启动」，用户看着就是"提示装成功了可它说没装"。
        shim_dir = os.path.join(PROFILE, ".claude_tool", "node")
        os.makedirs(shim_dir, exist_ok=True)
        shim = os.path.join(shim_dir, "claude.cmd")
        with open(shim, "w", encoding="utf-8") as f:
            f.write("假的 shim")
        real_probe = app._probe_claude_version
        app._probe_claude_version = lambda: None      # 那一下单独在 _probe_version 里验
        try:
            rebind("find_claude", lambda *a, **k: shim)
            app.feedback_var.set("")
            app._recheck_claude()
            app.update()
            check("重新检测认得刚装上的那份", app.claude_path == shim, app.claude_path)
            check("横幅撤掉了", not app.banner.winfo_ismapped(),
                  app.banner.winfo_ismapped())
            check("反馈行说了找到在哪儿", shim in app.feedback_var.get(),
                  app.feedback_var.get())
        finally:
            app._probe_claude_version = real_probe
            rebind("find_claude", lambda *a, **k: None)
            os.remove(shim)

        dialog, primary = open_install_panel(app)
        app.update()
        check("点它开出来的就是「帮你装 claude」面板", dialog is not None)
        check("它是主按钮（横幅上最显眼那颗）", primary)
        if dialog is None:
            return

        routes = I.routes()
        print()
        print("--- 面板照真实路数画（这台机器三条都走得通）---")
        radios = by_class(dialog, "Radiobutton")
        check("有几条路就摆几颗单选", len(radios) == len(routes),
              (len(radios), len(routes)))
        for radio, route in zip(radios, routes):
            caption = str(radio.cget("text"))
            check("「{}」这颗照 ready={} 画".format(route["name"], route["ready"]),
                  ("走不了" in caption) != route["ready"]
                  and str(radio.cget("state")) == ("normal" if route["ready"]
                                                   else "disabled"),
                  (caption, radio.cget("state")))
        check("第一条标着「推荐」（它就是官方那条原生脚本）",
              "推荐" in str(radios[0].cget("text")), radios[0].cget("text"))
        check("每条底下都跟着一句说明和前提",
              all(route["why"][:6] in "".join(labels(dialog)) for route in routes))
        check("前提那句也摆出来了（不是只摆说明）",
              all(route["needs"] in "".join(labels(dialog)) or not route["needs"]
                  for route in routes))
        table = "".join(labels(dialog))
        check("体检表说了这台是什么系统", "Windows" in table or I.platform_name() in table)
        check("体检表如实说 claude 还没找到", "还没找到" in table)

        box = by_class(dialog, "Entry")
        check("有一格只读的命令行", len(box) == 1 and str(box[0].cget("state"))
              == "readonly", [str(w.cget("state")) for w in box])
        check("默认摆的是第一条能走的路的命令", box[0].get() == routes[0]["show"],
              box[0].get())
        if len(routes) > 1:
            radios[1].invoke()
            app.update()
            check("换一条，命令跟着换", box[0].get() == routes[1]["show"],
                  box[0].get())
            radios[0].invoke()
            app.update()
            check("换回来也对", box[0].get() == routes[0]["show"], box[0].get())

        copy = next((b for b in walk(dialog)
                     if getattr(b, "_text", "") == "复制"), None)
        check("命令旁边有颗「复制」", copy is not None)
        if copy is not None:
            app.clipboard_clear()
            app.clipboard_append("先占住，看它会不会被覆盖")
            copy._command()
            app.update()
            check("复制真的进了剪贴板", app.clipboard_get() == routes[0]["show"],
                  app.clipboard_get())
            check("复制完面板还开着（早先那版会顺手把它关掉）",
                  any(getattr(w, "title", lambda: "")() == "帮你装 claude"
                      for w in app.winfo_children()))
        dialog.destroy()
        app.update()

        print()
        print("--- 前提不满足的那条：灰着、写明为什么、装完也不许点亮 ---")
        # 这台机器三条路都走得通，撞不出"走不了"那一支，所以这一段把 routes
        # 换成一份手艺活——面板画的是 routes 给的这份数据，跟它从哪儿来无关。
        # argv 挑一个铁定不存在的：万一下面 run_stream 那层假替没挂上，也只是
        # 一句"拉不起来"，不会在这台机器上真装一次东西。
        I.routes = lambda *a, **k: [
            fake_route("官方原生脚本", why="官方推荐的那条。",
                       needs="不需要装别的东西", ready=True,
                       argv=["压根没有这个程序xyz"], show="压根没有这个程序xyz"),
            fake_route("npm", why="要有 Node 才行。",
                       needs="这台机器上没有 npm", ready=False,
                       argv=["npm", "install", "-g", "x"], show="npm install -g x"),
        ]
        dialog, _ = open_install_panel(app)
        app.update()
        radios = by_class(dialog, "Radiobutton")
        check("两条就两颗", len(radios) == 2, len(radios))
        check("能走的那颗是亮的", str(radios[0].cget("state")) == "normal")
        check("走不了的那颗灰着", str(radios[1].cget("state")) == "disabled",
              radios[1].cget("state"))
        check("灰着的那颗自己也写着「走不了」", "走不了" in str(radios[1].cget("text")))
        check("为什么走不了摆在明处（不是灰着让人猜）",
              "这台机器上没有 npm" in "".join(labels(dialog)))
        check("默认选的是能走的那颗", by_class(dialog, "Entry")[0].get()
              == "压根没有这个程序xyz", by_class(dialog, "Entry")[0].get())
        radios[1].invoke()
        app.update()
        check("灰着那颗点不动（命令没被它换走）",
              by_class(dialog, "Entry")[0].get() == "压根没有这个程序xyz",
              by_class(dialog, "Entry")[0].get())

        print()
        print("--- 装之前问一句，不偷偷跑 ---")
        calls = []
        # 每条路自己定的超时有没有真送到 run_stream 手上：便携版 Node 那条要下
        # 几十兆，用默认那 600 秒会在慢网上被拦腰掐掉，所以这个参数不是摆设。
        seen_timeouts = []

        def blocking_stream(argv, on_line, timeout=None):
            calls.append(argv)
            seen_timeouts.append(timeout)
            # 卡在这儿，把"正在装"那一刻留给主线程去看。**这层里一个 Tk 控件都
            # 不碰**——它是工作线程，碰了轻则读不到东西，重则当场把线程打死
            # （踩过：在这儿 cget 那几颗单选，整个安装就再也不往下走了）。
            gate.wait(3)
            on_line("假的一行，中文原样出来")
            return 0

        I.run_stream = blocking_stream
        button = next(b for b in walk(dialog) if getattr(b, "_text", "") == "开始装")

        answer[0] = False
        asked.clear()
        button._command()
        app.update()
        check("先弹了个问的", bool(asked), asked)
        check("问的话里有那条命令本身",
              any("压根没有这个程序xyz" in "".join(str(x) for x in a)
                  for a in asked), asked)
        check("答了「不」就什么都不跑", not calls, calls)

        answer[0] = True
        button._command()
        app.update()
        check("答「是」才真去跑，跑的正是选中那条",
              calls == [["压根没有这个程序xyz"]], calls)
        check("没自己定超时的那条用默认那档",
              seen_timeouts == [I.INSTALL_TIMEOUT], seen_timeouts)
        started = time.monotonic()
        while not calls and time.monotonic() - started < 2:
            app.update()
            time.sleep(0.02)
        locked = [str(r.cget("state")) for r in by_class(dialog, "Radiobutton")]
        check("跑起来的时候那几颗都锁上了", locked == ["disabled", "disabled"], locked)
        button._command()
        app.update()
        check("跑到一半再点一次不会又跑一遍（也没再问一遍）",
              len(calls) == 1 and len(asked) == 2, (calls, len(asked)))

        out = by_class(dialog, "Text")[0]

        def shown():
            return out.get("1.0", "end")

        gate.set()
        started = time.monotonic()
        while "退出码" not in shown() and time.monotonic() - started < 4:
            app.update()
            time.sleep(0.02)
        text = shown()
        check("装的过程打进了面板里（不是另开一扇窗口）", "假的一行，中文原样出来" in text,
              text)
        check("把跑的那条命令也先记了一笔", "压根没有这个程序xyz" in text, text)
        check("退出码报出来了", "退出码 0" in text, text)
        check("装完自己又找了一遍 claude，没找到就照实说", "还是没找到" in text, text)
        check("走不了的那颗装完还是灰的（没顺手替它点亮）",
              str(radios[1].cget("state")) == "disabled",
              str(radios[1].cget("state")))
        check("能走的那颗装完放开了", str(radios[0].cget("state")) == "normal",
              str(radios[0].cget("state")))
        check("整段下来一个真弹框都没冒出来", not complained, complained)
        # "装"不是"升级"：装完了不该去触发那次强制的版本复查——那是用户手动点
        # 「有新版」过来的才有的待遇（升级模式那段验这个）。
        check("装完了不强行去查新版", app._force_version_check is False,
              app._force_version_check)
        dialog.destroy()
        app.update()

        print()
        print("--- 升级模式：措辞跟着变、默认落在「你这份用的那条」---")
        # 顶栏那颗「有新版」走的就是这条路：同一个面板，updating=True。这里把
        # routes 换成一份标了 pick 的手艺活，验的是**面板照数据画成了什么样**，
        # 跟 routes 自己怎么认出来那份 claude 无关（上面那样验过了）。
        I.routes = lambda *a, **k: [
            fake_route("官方原生脚本", why="再跑一遍就是升级。", ready=True,
                       argv=["压根没有这个程序xyz"], show="irm 官方那条"),
            fake_route("winget", why="winget 管的走 upgrade。", ready=True,
                       argv=["压根没有这个程序xyz"], show="winget upgrade --id X",
                       pick=True, note="你现在这份就是这条装的。",
                       timeout=I.NODE_TIMEOUT),
        ]
        up, _ = open_install_panel(app, updating=True)
        app.update()
        check("开出来的是「帮你升级 claude」", up is not None)
        if up is not None:
            text = "".join(labels(up))
            radios = by_class(up, "Radiobutton")
            check("标题说「怎么升级」，不是「怎么装」",
                  "怎么升级" in text and "怎么装" not in text)
            check("过程那块也说「升级的过程」", "升级的过程" in text)
            check("预选的是标了 pick 的那条（不是第一条）",
                  str(radios[1].cget("state")) == "normal"
                  and by_class(up, "Entry")[0].get() == "winget upgrade --id X",
                  by_class(up, "Entry")[0].get())
            check("预选那条的说明也摆出来了", "你现在这份就是这条装的。" in text)
            check("那颗按钮写的是「开始升级」",
                  any(getattr(b, "_text", "") == "开始升级" for b in walk(up)),
                  [getattr(b, "_text", "") for b in walk(up)
                   if getattr(b, "_text", "")])
            out = by_class(up, "Text")[0]
            check("还没开始跑就先说清楚它跑在哪儿",
                  "点「开始升级」之后" in out.get("1.0", "end"),
                  flat(out.get("1.0", "end"))[:40])

            print()
            print("--- 升级跑成功了：再去网上问一次，好让「有新版」掉下去 ---")
            app._force_version_check = False
            upgraded = []
            I.run_stream = lambda argv, on_line, timeout=None: (
                upgraded.append((argv, timeout)) or 0)
            answer[0] = True
            next(b for b in walk(up)
                 if getattr(b, "_text", "") == "开始升级")._command()
            started = time.monotonic()
            while "退出码" not in out.get("1.0", "end") and time.monotonic() - started < 4:
                app.update()
                time.sleep(0.02)
            check("退出码报出来了（说「升级完了」不是「装完了」）",
                  "— 升级完了，退出码 0 —" in out.get("1.0", "end"),
                  flat(out.get("1.0", "end")))
            check("升完了置上了「下次会话起来强查一次版本」的标记",
                  app._force_version_check is True, app._force_version_check)
            check("这条路自己定的超时真送到了 run_stream 手上",
                  upgraded == [(["压根没有这个程序xyz"], I.NODE_TIMEOUT)],
                  upgraded)
            check("升级跑完这扇窗口还开着（用户得看得见结果）",
                  any(getattr(w, "title", lambda: "")() == "帮你升级 claude"
                      for w in app.winfo_children()))
            up.destroy()
            app.update()

            print()
            print("--- 那颗标记真的会被心跳消费掉（勾没开也查）---")
            # 这一条是接面板那半截和启动器那半截的：面板置了标记，_poll_running
            # 里得真的去查一次，不然用户手动点的这次升级白点了——顶上那颗「有新
            # 版」会一直杵着。只验"要不要发起"，真发起的那次联网在这儿戳掉。
            fired = []
            app._start_version_check = lambda: fired.append(True)
            app.auto_version_var.set(False)
            app._force_version_check = True
            app.version_queue.put("9.9.9")
            app._poll_running()
            check("勾没开着也照查一次", fired == [True], fired)
            check("查完标记就清了（不然每次心跳都要查）",
                  app._force_version_check is False, app._force_version_check)

            fired.clear()
            app.version_queue.put("9.9.9")
            app._poll_running()
            check("下一趟就不查了（勾还是没开）", not fired, fired)

        print()
        print("--- 装到一半把窗口关了 ---")
        # 读数线程还挂在 gate 上，这时候把窗口拆了。要的是：它跑完之后那一下不
        # 会翻出什么幺蛾子来（这条 after 链会被 tkinter 跟着窗口一起撤掉，见
        # dialogs 那边 drain 的注释）。
        #
        # 上面那节把 routes、run_stream 都换过、面板也拆了，这儿重新开一扇同样
        # 配置的：要验的是"跑到一半关窗"那一刻，跟上面那扇是谁没关系。
        I.routes = lambda *a, **k: [
            fake_route("官方原生脚本", why="官方推荐的那条。", ready=True,
                       argv=["压根没有这个程序xyz"], show="压根没有这个程序xyz"),
            fake_route("npm", why="要有 Node 才行。",
                       needs="这台机器上没有 npm", ready=False,
                       argv=["npm", "install", "-g", "x"], show="npm install -g x"),
        ]
        I.run_stream = blocking_stream
        dialog, _ = open_install_panel(app)
        app.update()
        button = next(b for b in walk(dialog) if getattr(b, "_text", "") == "开始装")
        gate.clear()
        calls.clear()
        button._command()
        app.update()
        started = time.monotonic()
        while not calls and time.monotonic() - started < 2:
            app.update()
            time.sleep(0.02)
        check("第二趟又跑起来了", bool(calls), calls)
        dialog.destroy()
        gate.set()
        started = time.monotonic()
        while time.monotonic() - started < 0.5:
            app.update()
            time.sleep(0.02)
        check("关窗之后没有异常冒出来", not errors, errors)
        check("启动器自己还活着", app.winfo_exists() == 1)
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        for name, function in real_boxes.items():
            setattr(D.messagebox, name, function)
        rebind("find_claude", real_launcher_find)
        I.routes, I.run_stream = real_routes, real_stream


def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    print("沙箱:", PROFILE)
    print()
    commands()
    print()
    guards()
    print()
    versions()
    print()
    finder()
    print()
    upgrade()
    print()
    checkup_rows()
    print()
    streaming()
    print()
    panel()
    shutil.rmtree(PROFILE, ignore_errors=True)
    print()
    print("结果:", "全过" if OK[0] else "有失败")
    return 0 if OK[0] else 1


if __name__ == "__main__":
    sys.exit(main())
