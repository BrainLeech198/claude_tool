"""没装 claude 的时候，帮着照这台机器装一个。

这层只回答两件事：这台机器能走哪几条装法、每条那张命令长什么样。不含 tkinter，
也不自己开窗口——面板那半在 ui/dialogs.py，跑起来之后的输出往界面上流。

**参数都能塞假的进来**（platform / which / node）：探针靠这个在三种平台上各撞
一遍，不用真装一次 claude。

装法全照官方那套（见 DOCS_URL）：官方现在推**原生安装脚本**——不依赖 Node、
装完自己管更新；winget / brew / npm 是备选，装完更新归各自的包管理器管。这一点
在面板上得跟用户说清楚，不然它跟启动器里「查更新」看到的口径会对不上。
"""
import os
import re
import shutil
import subprocess
import sys
import threading

from claude_tool.claude import CREATE_NO_WINDOW
from claude_tool.paths import LOCAL_BIN, LOCAL_NAMES, NODE_DIR

DOCS_URL = "https://code.claude.com/docs/en/setup"
NATIVE_PS1 = "https://claude.ai/install.ps1"
NATIVE_SH = "https://claude.ai/install.sh"
WINGET_ID = "Anthropic.ClaudeCode"
BREW_CASK = "claude-code"
NPM_PACKAGE = "@anthropic-ai/claude-code"

# 便携版 Node 那条路从哪儿拿包。先官方，不通再退 npmmirror（国内那个 Node 镜像，
# 目录结构跟官方一模一样，index.json 也是）。两个都连不上这条路才真走不了。
NODE_DIST = ("https://nodejs.org/dist", "https://npmmirror.com/mirrors/node")

# npm 那条路要 Node 18 以上：Claude Code 的包用了老版本 Node 不认的语法，
# 低了会在启动时报一句语法错误，看着像"装了但坏了"。
MIN_NODE = (18,)

# 安装命令有时候会挂着不动（等一个永远不来的网络）。兜一个上限，到点掐掉，
# 别让面板一直转下去。
INSTALL_TIMEOUT = 600

# 便携版 Node 那条要下整个 Node（几十兆）再让 npm 拉一次包，600 秒在慢网上不够
# 用——半路被掐掉比慢更难受。单独给它一个更宽的。
NODE_TIMEOUT = 1800

PLATFORM_NAMES = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}

# "自己查"和"查出来是没装"得分开——前者要真去问一次机器，后者是探针塞进来的答案。
UNSET = object()


def platform_name(platform=None):
    platform = platform or sys.platform
    return PLATFORM_NAMES.get(platform, platform)


def node_version(which=shutil.which):
    """本机 Node 的版本 (20, 11, 0)，没装或者读不出来返回 None。"""
    exe = which("node")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "--version"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,
                              creationflags=CREATE_NO_WINDOW, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_version(proc.stdout)


def parse_version(raw):
    """从 "v20.11.0" / "20.11.0" 里揪出版本号，一个数字都没有返回 None。

    跟 versions.py 那个 parse 是一个路数，但那边认的是 claude 自己的版本串；
    这里只要 node 那种干干净净的 "vX.Y.Z"，不值得为它把两边焊在一起。
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    found = re.search(r"\d+(?:\.\d+)*", str(raw or ""))
    return tuple(int(part) for part in found.group(0).split(".")) if found else None


def version_text(parts):
    return "v" + ".".join(str(part) for part in parts)


def local_bin_claude(bin_dir=None):
    """~/.local/bin 底下那个 claude 的路径，没有返回 None。

    装完就是"在硬盘上但 PATH 里还没有"的那种：find_claude 会认它（所以装完当场
    就能用），这里专门问一遍是为了在体检表上把这句话摆出来。位置跟 find_claude
    认的是同一组，别各写一份。
    """
    bin_dir = bin_dir or LOCAL_BIN
    for name in LOCAL_NAMES:
        candidate = os.path.join(bin_dir, name)
        if os.path.exists(candidate):
            return candidate
    return None


def _route(name, why, needs, ready, argv, show, key=None, timeout=None):
    """一条路。key 是"这条路是哪一家"（native / winget / brew / npm / node），
    认装法（installed_via）和"该选哪条"都是拿它对上的；timeout 不给就用
    面板那个默认值。pick / note 见 routes() 结尾。"""
    return {"name": name, "why": why, "needs": needs, "ready": ready,
            "argv": argv, "show": show, "key": key, "timeout": timeout,
            "pick": False, "note": ""}


def ps_guard(url=NATIVE_PS1):
    """Windows 上跑原生脚本那条真正要跑的东西：取回来、验一眼、再执行。

    **为什么不能就写 `irm <url> | iex`**：这个地址不是对所有网络都开着。从国内
    直连会被 302 到 claude.com/app-unavailable-in-region，那页是 Webflow 的
    HTML，HTTP 状态还是 200——`irm` 高高兴兴把它当内容交给 `iex`，屏幕上刷一屏
    「此语言版本中不支持 var」「参数列表中缺少参量」，用户只看到「退出码 1」，
    完全不知道发生了什么（这是真事，0.2.0 上被报回来的就是这个）。

    官方自己的 bootstrap.ps1 里也有一模一样的一道防御（搜它那句 "Reject
    non-version content"），只是那道防线在第二跳——第一跳就被换成网页的时候它
    根本轮不到跑。

    url 能换是给探针用的：喂一张 HTML 进去，看它拒不拒。默认就是官方那个地址。
    """
    return r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    $raw = (Invoke-WebRequest -UseBasicParsing -Uri '__URL__').Content
} catch {
    [Console]::WriteLine('安装脚本没取回来：' + $_.Exception.Message)
    exit 1
}
# 那份脚本回来时带的是 application/octet-stream，这种没有 charset 的头，
# PowerShell 会把 .Content 给成 byte[] 而不是字符串——不先摆平它，下面那些
# -match 就是在数组上做匹配，好好的脚本会被当成"不像脚本"拒掉（踩过，探针
# 当场抓住的）。脚本是纯 ASCII，按 UTF-8 解不会有歧义。
if ($raw -is [byte[]]) { $raw = [Text.Encoding]::UTF8.GetString($raw) }
if (-not $raw -or $raw -match '<!DOCTYPE|<html|<\?xml' -or
    $raw -notmatch 'param\s*\(|\$env:|\$ErrorActionPreference') {
    [Console]::WriteLine('取回来的不是安装脚本，是一张网页——没有执行它。')
    [Console]::WriteLine('这个地址从国内直连会被引到「你所在的地区用不了」那一页，')
    [Console]::WriteLine('挂上代理再点一次，或者换下面 winget / npm 那两条路。')
    [Console]::WriteLine('这几条都走不通的话，最后那条「便携版 Node + npm」不挑网络。')
    exit 1
}
Invoke-Expression $raw
""".strip().replace("__URL__", url)


def sh_guard(url=NATIVE_SH):
    """Linux/macOS 上那条真正要跑的东西，跟 ps_guard 是同一件事两种写法。

    `curl -fsSL ... | bash` 一样挡不住：那边被换成的是一张 HTML 网页，`bash`
    咬下去就是一屏语法错误。这里的判断是"第一行得以 #! 开头"——真脚本都带
    shebang，网页没有。用 read 和 [[ ]] 都是 bash 自带的，不额外要 head/grep。

    **别指望 curl 的 -f 帮你挡**：那个地区限制页回的 HTTP 状态是 200。
    """
    return r"""
tmp=$(mktemp) || { echo '建不了临时文件，没往下走。'; exit 1; }
if ! curl -fsSL -o "$tmp" '__URL__'; then
    echo '安装脚本没取回来。'
    rm -f "$tmp"
    exit 1
fi
IFS= read -r first < "$tmp" || first=''
if [[ "$first" != '#!'* ]]; then
    echo '取回来的不是安装脚本，是一张网页——没有执行它。'
    echo '这个地址从国内直连会被引到「你所在的地区用不了」那一页，'
    echo '挂上代理再试一次，或者换下面 npm 那条路。'
    rm -f "$tmp"
    exit 1
fi
bash "$tmp"
code=$?
rm -f "$tmp"
exit $code
""".strip().replace("__URL__", url)


def node_setup(platform=None, bases=NODE_DIST):
    """便携版 Node + npm 那条路要跑的东西。

    **为什么要有这条路**：前面几条各有各的门槛，实机上撞见过前两条一起走不通
    （原生脚本被地区限制挡住、winget 也拉不下来），而 npm 那条又因为机器上没有
    Node 灰着——用户就卡在那儿了。这条把官方那个 Node 便携包整个下下来，用它
    自带的 npm 把 claude 装上，不需要用户先去自己折腾一个 Node。

    **为什么 claude 要装进 Node 那个目录**：npm 生成的启动 shim 是"去自己旁边找
    node.exe，找不到才回头问 PATH"。Node 摆在别处、claude 摆在别处，shim 就找
    不到 node，装完了也是一敲就报错。两个放同一个目录（npm 的 --prefix 直接指
    过去），这条链子才是通的。

    **只写 ~/.claude_tool 里面**：不动系统 PATH、不往 Program Files 里塞东西、
    不碰注册表。卸载就是把那个目录删掉。想让终端里也敲得出 claude，得用户自己
    把那个目录加进 PATH——脚本最后会把路径打出来，但不会替他改。

    bases 是包的来源，探针靠它指到本地那个小服务上，不真去下 Node。
    """
    platform = platform or sys.platform
    listing = ", ".join("'{}'".format(base) for base in bases)
    if platform == "win32":
        return _NODE_PS.replace("__BASES__", "@(" + listing + ")")
    return _NODE_SH.replace("__BASES__", " ".join(bases))


# Windows 那份。分号、花括号、$ 都是 PowerShell 自己的语法，所以整段做成模板、
# 只把来源那处 __BASES__ 换掉（.format() 会被那些花括号当场搅乱）。
_NODE_PS = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$tool = Join-Path $env:USERPROFILE '.claude_tool'
$root = Join-Path $tool 'node'
$bases = __BASES__

# 版本清单：第一个还挂着 LTS 的那条就是我们要的。Invoke-RestMethod 自己就把
# JSON 解好了，不用在这儿抠字符串。
$index = $null
foreach ($base in $bases) {
    try { $index = Invoke-RestMethod -Uri "$base/index.json" -ErrorAction Stop; break } catch { }
}
if (-not $index) {
    [Console]::WriteLine('没取到 Node 的版本清单：nodejs.org 和 npmmirror 都连不上。')
    exit 1
}
$ver = ($index | Where-Object { $_.lts } | Select-Object -First 1).version
if (-not $ver) { [Console]::WriteLine('版本清单里没找到 LTS 那条。'); exit 1 }

$name = "node-$ver-win-x64"
$zip = Join-Path $env:TEMP "$name.zip"
$tmp = Join-Path $env:TEMP 'claude_tool_node'
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

$got = $false
foreach ($base in $bases) {
    try {
        [Console]::WriteLine("正在下 Node $ver ……")
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$ver/$name.zip" -OutFile $zip -ErrorAction Stop
        $got = $true
        break
    } catch {
        [Console]::WriteLine('这个源没下来：' + $_.Exception.Message)
    }
}
if (-not $got) { [Console]::WriteLine('Node 的包没下下来。'); exit 1 }

[Console]::WriteLine('下好了，正在解开……')
Expand-Archive -Path $zip -DestinationPath $tmp -Force
if (Test-Path $root) { Remove-Item -Recurse -Force $root }
New-Item -ItemType Directory -Force -Path $tool | Out-Null
Move-Item (Join-Path $tmp $name) $root
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Remove-Item -Force $zip -ErrorAction SilentlyContinue
[Console]::WriteLine("Node 解好了：$root")

& (Join-Path $root 'npm.cmd') install -g --prefix $root --no-fund --no-audit '@anthropic-ai/claude-code'
if ($LASTEXITCODE -ne 0) { [Console]::WriteLine('npm 没装上 claude。'); exit $LASTEXITCODE }

[Console]::WriteLine("装好了：$root\claude.cmd")
[Console]::WriteLine("想在自己终端里也敲得出 claude，就把这个目录加进 PATH：$root")
[Console]::WriteLine('（不改也没关系，启动器自己认得这个位置。）')
""".strip()


# 别的平台那份，跟 _NODE_PS 是同一件事两种写法。版本清单一样是"第一个还挂着
# lts 的那条"，只是没有 json 解析器可用：index.json 正好一条记录一行，抓第一行
# 带 "lts":" 的，再从里面抠 version。
_NODE_SH = r"""
set -e
root="$HOME/.claude_tool/node"
bases="__BASES__"

case "$(uname -s)" in
    Darwin) os=darwin ;;
    *)      os=linux ;;
esac
case "$(uname -m)" in
    arm64|aarch64) arch=arm64 ;;
    *)             arch=x64 ;;
esac

index=
for base in $bases; do
    if index=$(curl -fsSL "$base/index.json"); then break; fi
done
if [ -z "$index" ]; then
    echo '没取到 Node 的版本清单：nodejs.org 和 npmmirror 都连不上。'
    exit 1
fi
ver=$(printf '%s\n' "$index" | grep '"lts":"' | head -1 | grep -o '"version":"v[0-9.]*"' | head -1 | cut -d'"' -f4)
if [ -z "$ver" ]; then echo '版本清单里没找到 LTS 那条。'; exit 1; fi

name="node-$ver-$os-$arch"
tgz="$HOME/.claude_tool/$name.tar.gz"
# 这个目录正常是启动器启动时建的，但这段脚本可能被单独拿去敲——curl 不会替你
# 建父目录，缺了就只报一句"打不开文件"，看不出来是缺目录。
mkdir -p "$HOME/.claude_tool"

got=
for base in $bases; do
    echo "正在下 Node $ver ……"
    if curl -fsSL -o "$tgz" "$base/$ver/$name.tar.gz"; then got=1; break; fi
    echo '这个源没下来，换下一个。'
done
if [ -z "$got" ]; then echo 'Node 的包没下下来。'; exit 1; fi

echo '下好了，正在解开……'
rm -rf "$root"
tar -xzf "$tgz" -C "$HOME/.claude_tool"
mv "$HOME/.claude_tool/$name" "$root"
rm -f "$tgz"
echo "Node 解好了：$root"

"$root/bin/npm" install -g --prefix "$root" --no-fund --no-audit '@anthropic-ai/claude-code' || {
    echo 'npm 没装上 claude。'
    exit 1
}

echo "装好了：$root/bin/claude"
echo "想在自己终端里也敲得出 claude，就把这个目录加进 PATH：$root/bin"
echo '（不改也没关系，启动器自己认得这个位置。）'
""".strip()


def _node_show(platform):
    """便携版那条摆给用户看的那行。

    这条路没有一个"官方原样一条命令"可抄（官方只管给你 Node，装 claude 是另一
    步），所以摆的是**真正干这事的那条**——本机这份 Node 自己的 npm 装 claude。
    前半截下 Node、解压那几步在 why 和输出的路径里说清楚了，不塞进这一行：塞进去
    就不是一句人看得懂的话了。
    """
    if platform == "win32":
        npm = r"%USERPROFILE%\.claude_tool\node\npm.cmd"
        prefix = r"%USERPROFILE%\.claude_tool\node"
    else:
        npm = "~/.claude_tool/node/bin/npm"
        prefix = "~/.claude_tool/node"
    return "{} install -g --prefix {} {}".format(npm, prefix, NPM_PACKAGE)


def installed_via(claude_path, node_dir=None):
    """这份 claude 当初是怎么装上的：native / winget / brew / npm / node，认不出
    来返回 None。

    **只看它在盘上的位置**——各家的落地目录不一样，这是唯一能从外面看出来的
    线索，不去翻注册表、也不去问包管理器（那要花好几秒，还可能问到一半被墙住）。

    先把软链解开再认：npm 和 brew 装出来的多半是个软链，链子本身躺在
    /usr/local/bin，真身才在 node_modules / Caskroom 里——不 realpath 就只看得见
    一个毫无特征的路径，什么都认不出。

    认不出来就照实返回 None，界面据此退回到"不替你选"。猜错比不选坏：选错了
    用户可能照着一条不相干的命令把 claude 在旁边又装一份。
    """
    if not claude_path:
        return None
    path = os.path.realpath(claude_path).replace("\\", "/").lower()
    ours = (node_dir or NODE_DIR).replace("\\", "/").lower()
    if path.startswith(ours + "/"):
        # 便携版 Node 那条路装的。得排在 npm 前面认：它的 shim 也是 npm 生成的
        # 那种 .cmd，光看扩展名会认成"系统 npm 装的"，然后让人拿一个不存在的
        # npm 去升级。
        return "node"
    if "winget" in path:
        return "winget"
    if "caskroom" in path or "cellar" in path or "homebrew" in path:
        return "brew"
    if "node_modules" in path or "/npm/" in path:
        return "npm"
    if "/.local/bin/" in path:
        return "native"
    if path.endswith(".cmd") or path.endswith(".ps1"):
        return "npm"
    return None


def routes(platform=None, which=shutil.which, node=UNSET, upgrade=False,
           claude_path=None):
    """这台机器能走的装法，一条一项。upgrade=True 时拿到的是一组升级的路。

    platform / which / node 传进来就是"照这个模拟"，不传就照本机真实情况。
    node 传 None 表示"没装"，传 UNSET（默认）表示自己去问机器。

    **upgrade=True 是干嘛的**：顶栏那颗「有新版」点开时用。命令换成各家升级用的
    那一条——winget 是 upgrade 子命令、brew 是 brew upgrade；原生脚本和 npm 那两
    条不用换，它们本来就是"再跑一遍就是升级到最新"。同时按 claude_path 认出来
    这份 claude 是怎么装的（installed_via），认出来的那一条 pick 置真、其余的
    note 里写一句"不是这条装的，选它会再装一份"——认不出来就都不标，界面退回到
    "第一条能走的"，不替用户瞎选。

    每一项：
      name    装法叫什么        why    一句人话说明
      needs   前提（说明白了灰着也是有用的信息）
      ready   能不能走          argv   真要跑的命令
      show    摆给用户看/复制的那一行——官方那条命令的原样。argv 可能比它多一层
              校验（见 ps_guard / sh_guard），所以这两者不保证一字不差：show 得短
              到塞得进面板那一格单行 Entry，还得是用户能拿去别处照着敲的那句。
      key     这条路是哪一家，认装法、配对用的
      timeout 这条最多跑多久，不给就用 INSTALL_TIMEOUT
      pick    面板一打开默认选它
      note    附在 why 后面的一句补充，升级模式下才有内容

    第一条永远是原生脚本——官方推荐它，而且它不需要任何前提。顺序就是面板上
    从上到下的顺序。
    """
    platform = platform or sys.platform
    if node is UNSET:
        node = node_version(which)
    found = []

    if platform == "win32":
        # irm 是 PowerShell 自带的，Windows 上这条不需要前提。
        found.append(_route(
            "官方原生脚本",
            ("再跑一遍官方那条就是升级——那份脚本自己写着 always download "
             "latest，跑一次就把本机这份换成最新的。"
             if upgrade else
             "官方推荐的那条，装完它自己会更新，不用你管。")
            + "跑之前会先验一眼取回来的是不是脚本。",
            "不需要装别的东西",
            True,
            ["powershell", "-NoProfile", "-Command", ps_guard()],
            "irm {} | iex".format(NATIVE_PS1),
            key="native"))
    else:
        # install.sh 自己就是 curl 拉下来的，所以 curl 是这条唯一的前提。
        have_curl = which("curl") is not None
        found.append(_route(
            "官方原生脚本",
            ("再跑一遍官方那条就是升级——那份脚本自己写着 always download "
             "latest，跑一次就把本机这份换成最新的。"
             if upgrade else
             "官方推荐的那条，装完它自己会更新，不用你管。")
            + "跑之前会先验一眼取回来的是不是脚本。",
            "不需要装别的东西" if have_curl else "没找到 curl，这条走不了",
            have_curl,
            ["bash", "-c", sh_guard()],
            "curl -fsSL {} | bash".format(NATIVE_SH),
            key="native"))

    if platform == "win32":
        have_winget = which("winget") is not None
        verb = "upgrade" if upgrade else "install"
        found.append(_route(
            "winget",
            ("这份 claude 是 winget 管的，升级就走 winget upgrade——"
             "它跟 claude 自己的自动更新不是一条路，各管各的。"
             if upgrade else
             "Windows 自带的包管理器。装完更新得靠 winget upgrade，"
             "它跟 claude 自己的自动更新不是一条路。"),
            "这台机器有 winget" if have_winget else "这台机器上没有 winget",
            have_winget,
            ["winget", verb, "--id", WINGET_ID,
             "--accept-package-agreements", "--accept-source-agreements"],
            "winget {} --id {} --accept-package-agreements "
            "--accept-source-agreements".format(verb, WINGET_ID),
            key="winget"))

    if platform == "darwin":
        have_brew = which("brew") is not None
        verb = "upgrade" if upgrade else "install"
        found.append(_route(
            "Homebrew",
            ("这份 claude 是 Homebrew 管的，升级就走 brew upgrade。"
             if upgrade else
             "macOS 上最顺手的一条。装完更新走 brew upgrade。"),
            "这台机器有 brew" if have_brew else "这台机器上没有 brew",
            have_brew,
            ["brew", verb, "--cask", BREW_CASK],
            "brew {} --cask {}".format(verb, BREW_CASK),
            key="brew"))

    # npm 三个平台都摆——Linux 上它往往是唯一一条现成的路。
    have_npm = which("npm") is not None
    if not have_npm:
        node_note, node_ready = "没装 Node/npm，这条走不了", False
    elif node is None:
        # npm 在、node 问不出来：少见（nvm 的 shim 有时这样），当作能用，
        # 别为一句读不到的话把唯一一条路堵死。
        node_note, node_ready = "有 npm", True
    elif node < MIN_NODE:
        node_note = "Node 太旧（{}），要 18 以上".format(version_text(node))
        node_ready = False
    else:
        node_note, node_ready = "Node {}".format(version_text(node)), True
    found.append(_route(
        "npm",
        ("这份 claude 是 npm 装的，升级就把这个包再装一遍——npm 会换成最新那版。"
         if upgrade else
         "要有 Node 才行。装完更新走 npm，跟 claude 自己的自动更新不是一条路。"),
        node_note,
        node_ready,
        ["npm", "install", "-g", NPM_PACKAGE],
        "npm install -g {}".format(NPM_PACKAGE),
        key="npm"))

    # 便携版 Node：留给"前几条都走不通、机器上又没有 Node"那种情况——实机上
    # 撞见过原生脚本和 winget 一起失败、npm 又因为没 Node 灰着，用户就卡住了。
    found.append(_route(
        "便携版 Node + npm",
        "上一条得先有 Node。这条把官方那个 Node 便携包整个下到启动器自己的"
        "目录（~/.claude_tool/node）里，再用它自带的 npm 装 claude——不用你先"
        "自己去折腾一个 Node。只写 ~/.claude_tool 里面，不动系统的 PATH，"
        "卸载就是把这个目录删掉。",
        "需要能连上 nodejs.org，连不上自动换 npmmirror",
        True,
        (["powershell", "-NoProfile", "-Command", node_setup(platform)]
         if platform == "win32" else ["bash", "-c", node_setup(platform)]),
        _node_show(platform),
        key="node", timeout=NODE_TIMEOUT))

    if upgrade:
        who = installed_via(claude_path)
        if who is not None:
            for route in found:
                route["pick"] = route["key"] == who
                route["note"] = ("你现在这份就是这条装的。" if route["pick"] else
                                 "不是这条装的，选它会再装一份。")
    else:
        found[0]["pick"] = True

    return found


def checkup(platform=None, which=shutil.which, node=UNSET, claude_path=None,
            bin_dir=None):
    """面板顶上那张体检表，一行一项：(项目, 结果, 这一项算不算好)。

    只报"这台机器上是什么"，不动手也不拦着。哪条路走不了，是上面 routes() 那
    几项自己的事，这里只是把理由摆在明处。
    """
    platform = platform or sys.platform
    if node is UNSET:
        node = node_version(which)
    rows = [("系统", platform_name(platform), True)]

    if claude_path:
        rows.append(("claude", claude_path, True))
    else:
        rows.append(("claude", "还没找到", False))
        # 装到 PATH 外面那种：硬盘上有、`claude` 这五个字母敲不出来。这一条
        # 正是启动器能帮上忙的地方，得单独说。
        came_out = local_bin_claude(bin_dir)
        if came_out:
            rows.append(("~/.local/bin", "有 claude，只是 PATH 里还没有", True))

    if platform == "win32":
        rows.append(("winget", "有" if which("winget") else "没有",
                     bool(which("winget"))))
    if platform == "darwin":
        rows.append(("brew", "有" if which("brew") else "没有", bool(which("brew"))))
    rows.append(("npm", "有" if which("npm") else "没有", bool(which("npm"))))
    rows.append(("Node", version_text(node) if node else "没装", node is not None))
    return rows


def decode(raw):
    """子进程吐出来那行字节 -> 给人看的字符串。

    先按 UTF-8 解，解不动换 cp936（这台机器控制台那套编码），再不行才 replace。
    安装脚本是别人写的，它按什么编码吐中文我们管不了，能认出中文就够了。

    别改成让 subprocess 自己 text=True：那按 locale 解，这台机器上是 cp936，
    而 npm / powershell 那些多半吐 UTF-8，一对不上就是一片乱码。
    """
    raw = raw.rstrip(b"\r")
    for encoding in ("utf-8", "cp936"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run_stream(argv, on_line, timeout=INSTALL_TIMEOUT):
    """跑一条安装命令，每出一行就叫一次 on_line，返回退出码。

    拉不起来（没有这个程序、权限不够）返回 None，并且已经把原因交给 on_line 了。

    读数用 os.read 而不是 proc.stdout.read(n)：后者会一直等到凑满 n 个字节或者
    进程结束，安装脚本慢吞吞吐一行的时候界面上什么都看不见。os.read 是有多少
    拿多少，这才叫流式。

    **别在这层碰界面**：调用方把它丢进一个线程里，on_line 只管往队列里塞，
    取和画都在主线程（Tk 的东西不能跨线程碰）。
    """
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
    except OSError as error:
        on_line("拉不起来：{}".format(error))
        return None

    # 挂死的安装总得有个头：到点掐掉，下面那次 read 会立刻返回空、循环就出去了。
    killer = threading.Timer(timeout, proc.terminate)
    killer.start()
    pending = b""
    try:
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                on_line(decode(line))
        if pending:
            on_line(decode(pending))
    except OSError:
        # 掐进程那一下可能把管道也带走了，这时候 read 会抛。装完的账照记，
        # 不为了收尾这点噪音把整个安装判成失败。
        pass
    finally:
        killer.cancel()
        proc.stdout.close()
    return proc.wait()
