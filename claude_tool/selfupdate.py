"""把新版启动器的安装包下下来，然后打开它。

跟 versions.py 的分工：那边管"要不要"（网上最新是几版、比本机新没有），这边管
"怎么办"（去哪下、下到哪、下完怎么打开）。界面拿到 versions.launcher() 那个结果
原样递进来就行。

地址不写死在这儿——官网上那份 releases.js 里写着两个基地址（Gitee 主源、GitHub
镜像），记录里那一格写着这一版这个平台的产物叫什么、多大。拼法就是
`<基地址>/<版本号>/<文件名>`，官网页面上的下载链接也是这么拼的。**附件没传上去
就是 404**，这一步没有别的魔法。

两个源是"一个不行换下一个"，不是"分一半流量"：国内 Gitee 通常通，GitHub 那份是
镜像、备着。记录里那个可选的 `url` 字段排在最前面（指到别处时填它）。

下完的包留在 paths.DOWNLOAD_DIR（不是临时目录）：用户没点"是"、或者装到一半失败
了，文件还在，他自己能找过去再双击一次。

不碰 tkinter。下载跑在后台线程里，进度靠回调扔出去，画的事归界面。
"""
import os
import subprocess
import sys
import urllib.request

from claude_tool.paths import DOWNLOAD_DIR

# 读一块写一块，别把整个包读进内存——Windows 那个安装包现在十来兆，以后只会更大。
CHUNK = 64 * 1024
# 单次连接/读取的上限，不是整个下载的上限：慢网下十来兆要几分钟是正常的，卡住
# 不动才是异常。所以给的是 socket 级的超时，整件事不限时。
TIMEOUT = 20

# 打开产物到底是什么意思，两个平台不一样：Windows 上那是个安装包，打开就是装上
# 去（等于替掉正在跑的这个程序）；别处那个产物是个 tar.gz，没有安装器，"打开"
# 只能是弹出它所在的文件夹让用户自己解。
INSTALLER = sys.platform == "win32"

PLATFORM = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(
    sys.platform, "windows")


def platform_key():
    """本机在 releases.js 的记录里是哪个格子。认不出来当 windows——那是最早发的
    那个平台，也是这份代码主要在跑的机器。"""
    return PLATFORM


def asset(entry, key=None):
    """记录里本平台那一格：{"file": ..., "size": ..., "url"?: ...}；没有给 None。

    那一格可能是 null（这一版还没给这个平台打包），也可能缺字段。文件名为空一律
    当没打——拼出来的地址会是 `<基地址>/<版本号>/`，点下去就是 404。
    """
    cell = entry.get(key or platform_key())
    if not isinstance(cell, dict):
        return None
    if not isinstance(cell.get("file"), str) or not cell["file"].strip():
        return None
    return cell


def urls(release, cell):
    """按顺序该试的下载地址。

    三种来源，先手填后拼接：记录里那个可选的 `url` 是**整条下载地址**（"填了就
    直接拿它当下载地址，不填才按 DOWNLOAD_BASE 拼"——docs/releases.js 上就是这么
    说的），所以它原样进来、不拼版本号和文件名；两个基地址才要拼。

    去重是为了省一次重试：手填的那个指回主源是常见情形（同一份包换个子域名）。
    """
    version = release.latest
    name = cell["file"]
    inline = cell.get("url")
    found = [inline.strip()] if isinstance(inline, str) and inline.strip() else []
    for base in (release.base, release.mirror):
        if not isinstance(base, str) or not base.strip():
            continue
        full = "{}/{}/{}".format(base.strip().rstrip("/"), version, name)
        if full not in found:
            found.append(full)
    return found


def _already_there(target, size):
    """这份包已经在盘上了吗。

    只在记录里写了字节数、且对得上时才算"下过了"：上次下到一半断掉留下的文件
    大小必然不对，会重新下。用户点了"是"又反悔、隔天再点一次，这一步能省掉十来兆
    的重复流量。
    """
    try:
        return bool(size) and os.path.getsize(target) == size
    except OSError:
        return False


def fetch(candidates, target, size, on_progress=None):
    """把 candidates 里第一个通的地址下到 target，返回 target。

    size 是记录里写着的字节数：下完对不上就删掉重来（下次换下一个源）。这不是
    校验和，只挡"下了一半"和"下回来的是一张错误页"这两种——releases.js 里没有
    更硬的东西可对，真要防篡改得先有签名，那是另一件事。

    失败不吞：最后一个源也挂了就把它的异常原样抛出去，界面拿去显示。
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if _already_there(target, size):
        if on_progress:
            on_progress(size or 0, size or 0)
        return target

    part = target + ".part"
    last = None
    for url in candidates:
        try:
            _pull(url, part, size, on_progress)
        except Exception as error:
            last = error
            continue
        os.replace(part, target)
        return target
    if last is not None:
        raise last
    raise ValueError("没有可用的下载地址（releases.js 里两个基地址都是空的）")


def _pull(url, part, size, on_progress):
    """一个地址下完。断在半路也好、字节数对不上也好，都算失败——把半截文件清掉
    再抛出去，免得留在盘上被下一次的 _already_there 当成好文件。"""
    request = urllib.request.Request(url, headers={"User-Agent": "claude_tool"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            total = size or _content_length(response)
            done = 0
            with open(part, "wb") as out:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    if on_progress:
                        on_progress(done, total)
    except Exception:
        _discard(part)
        raise
    if size and os.path.getsize(part) != size:
        got = os.path.getsize(part)
        _discard(part)
        raise IOError("下回来的只有 {} 字节，网上那份写着 {} 字节".format(got, size))
    # 进度条按 size 走完了，但真实长度以 response 为准时补最后一下，免得停在 97%
    if on_progress:
        on_progress(size or done, size or done)


def _content_length(response):
    """服务端报的长度，没报给 None（进度条就只显示已下多少）。"""
    raw = response.headers.get("Content-Length")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def download(release, on_progress=None):
    """按 versions.launcher() 拿到的那个结果，把这一版的包装下来，返回文件路径。

    记录里没有本平台那一格时抛 LookupError——界面拿这个当"这版还没给你的系统
    打包"来提示，别当成故障。
    """
    cell = asset(release.entry)
    if cell is None:
        raise LookupError(
            "网上的 {} 里没有 {} 这份包".format(
                release.latest, platform_key()))
    # 落盘就用官网上那个文件名：带版本号，用户翻到 downloads/ 里一眼看得出是哪一版
    target = os.path.join(DOWNLOAD_DIR, cell["file"])
    size = cell.get("size")
    if not isinstance(size, int):
        size = None
    return fetch(urls(release, cell), target, size, on_progress)


def open_artifact(path):
    """打开下下来的东西。

    Windows：那是安装包，打开就是跑起来。它会来替掉这个正在跑的程序，所以界面
    那边得先把启动器自己关掉再调这儿（见 launcher._install_update）。
    别处：产物是个 tar.gz，没有安装器，"打开"就是弹出它所在的那个文件夹。
    """
    directory = os.path.dirname(path) or "."
    if INSTALLER:
        subprocess.Popen([path], close_fds=True)
        return path
    from claude_tool.host import open_path
    open_path(directory)
    return directory
