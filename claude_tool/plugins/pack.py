"""插件包（`.zip`）的导入 —— 插件管理里那个「导入」按钮背后的一步。

## 为什么要有这一步

插件原来只能"往插件目录里丢一个文件夹"。这对**写**插件的人还行，对**用**插件的人
不行：他拿到的是一个下载下来的压缩包，得自己知道往哪儿解，解完还得保证目录名跟
`id` 一模一样（见 manifest.read），差一个字母就报"id 跟目录名对不上"。zip 导入把
这几步收成一次点击。

包里的形状**两种都吃**：

    novel_assistant.zip                 novel_assistant.zip
    ├── plugin.json                     └── novel_assistant/
    ├── __init__.py                         ├── plugin.json
    └── ...                                 └── ...

只认**一层**包裹目录。再深就说不清哪个是插件的根了，宁可让作者把包打对。

## 这个文件只做一件事，但要做对：别让包写到外面去

zip 里的成员名是**别人写的字符串**，可以是 `../../乱七八糟`、`C:\\Windows\\...`，
或者（POSIX 上打的包）一个指向 `/etc` 的符号链接。所以这里**不用 `extractall`**：
逐条解，每条都自己验目标路径确实落在插件目录里面。再加上文件数、解压后体积两道
闸——防的是一个几十 KB 的包解出几十 GB 的"压缩炸弹"。

## 装的过程是"先搭好、再换上去"

解压全部落在 `.<id>.staging-<pid>/`，**成功了**才把 `<id>/` 换成它。于是任何一步
失败（包坏了、磁盘满了、权限不对）既不会留下半截插件，也不会把已经装好的那份毁掉。

## 导入 ≠ 信任

装进来只说明"这东西在磁盘上了"。它**仍然是未启用**——信任模型那一条（见
registry.py 开头）不因为来源变成 zip 就放松：用户还得在插件管理里点一下「启用」。
"""
import json
import os
import re
import shutil
import zipfile

from claude_tool.plugins import manifest as manifest_mod

# 清单文件名。跟 manifest 那边是同一个东西，取个短名字只是让下面的判断读着顺。
FILE_NAME = manifest_mod.FILE_NAME

# 两道闸。**这不是性能优化，是安全**：一个几十 KB 的 zip 能解出几十 GB（压缩炸弹），
# 用户点一下导入就能把磁盘塞满。这两个数比任何正常插件都宽出一大截，正常包碰不到。
MAX_FILES = 2000
MAX_BYTES = 32 * 1024 * 1024

# 打 zip 的机器留下的垃圾（macOS 的 `__MACOSX/`、`.DS_Store`，Windows 的 `Thumbs.db`），
# 跟着包一起进来只会碍事。
JUNK_NAMES = ("__MACOSX", ".DS_Store", "Thumbs.db")

# 建 staging 目录时用的后缀。`.` 开头——`discover()` 不扫 `.` 开头的目录，所以哪怕
# 上一次导入崩了留下一坨，也不会被当成插件扫出来。
STAGING_MARK = "."


class BadPackage(Exception):
    """这个包不能当插件装。`str(e)` 是给用户看的一句话。"""


def _norm(name):
    """把 zip 成员名归一化：统一成 `/` 分隔、去掉开头的 `./`。"""
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name


def _unsafe_reason(name):
    """成员名"想写到解压目录外面"的话返回一句原因；正常返回 None。"""
    if name.startswith("/"):
        return "绝对路径"
    if re.match(r"^[A-Za-z]:", name):
        return "带盘符的绝对路径"
    if any(part == ".." for part in name.split("/")):
        return "用 .. 往回走"
    return None


def _is_symlink(info):
    """这一项是不是符号链接。

    读的是成员外部属性里的 Unix 类型位。Windows 上打的包这些位是 0，所以只有 POSIX
    上打的包可能命中——而正是那种包能在 Linux 上借符号链接写到解压目录外面。
    """
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def find_root(names):
    """找插件根在包里的前缀，返回 `""`（根就是插件）或 `"目录/"`。

    找不着、或者找到不止一个，抛 `BadPackage`。一个包只装一个插件——包里有两个
    `plugin.json` 就说不清哪个是根，让作者重打比猜要靠谱。
    """
    candidates = []
    for name in names:
        if name == FILE_NAME:
            prefix = ""
        elif name.endswith("/" + FILE_NAME) and name.count("/") == 1:
            prefix = name[:-len(FILE_NAME)]
        else:
            continue
        if prefix not in candidates:
            candidates.append(prefix)
    if not candidates:
        raise BadPackage(
            "包里没有 {} —— 插件包的最外层，或者里面唯一那一层目录里，必须有它"
            .format(FILE_NAME))
    if len(candidates) > 1:
        raise BadPackage(
            "包里有不止一个 {}（{}）—— 一个包只装一个插件，请重打一个只含一个的"
            .format(FILE_NAME, "、".join(repr(c) for c in candidates)))
    return candidates[0]


def _open(zip_path):
    """打开 zip；"打不开"一律变成 `BadPackage`（对用户是同一件事：这个包不能用）。"""
    if not os.path.isfile(zip_path):
        raise BadPackage("找不到这个文件：{}".format(zip_path))
    try:
        return zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BadPackage("这不是一个能打开的 zip 包：{}".format(exc))


def _read_manifest(zf, where):
    """从已打开的包里读清单并校验，返回 `manifest.Manifest`。"""
    names = [_norm(info.filename) for info in zf.infolist()]
    prefix = find_root(names)
    member = prefix + FILE_NAME
    try:
        raw = zf.read(member)
    except KeyError:
        # 名字是从 infolist 里数出来的，正常到不了这儿；包在两步之间被换掉之类才
        # 可能。照实说，别抛一个 KeyError 出去。
        raise BadPackage("包里的 {} 读不出来".format(member))
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise BadPackage("包里的 {} 不是 UTF-8 编码：{}".format(FILE_NAME, exc))
    except ValueError as exc:
        raise BadPackage("包里的 {} 不是合法 JSON：{}".format(FILE_NAME, exc))
    try:
        # 校验规则跟磁盘上那份是同一套（manifest.parse），这里只管把 `id` 拿到手
        # ——它就是接下来要建的目录名。
        return manifest_mod.parse(data, "{}!{}".format(where, member))
    except manifest_mod.BadManifest as exc:
        raise BadPackage(str(exc))


def _unpack(zf, infos, prefix, staging):
    """把 `prefix` 底下的东西解到 `staging`，逐条验目标路径。"""
    root = os.path.abspath(staging)
    for info in infos:
        name = _norm(info.filename)
        if not name.startswith(prefix):
            continue                    # 包裹目录之外的东西（README 之类）不装
        rel = name[len(prefix):]
        if not rel or rel.endswith("/") or info.is_dir():
            continue                    # 目录项：用到的时候按文件路径建
        parts = rel.split("/")
        if parts[0] in JUNK_NAMES or parts[-1] in JUNK_NAMES:
            continue
        target = os.path.join(staging, *parts)
        # 双保险：成员名那一遍已经挡了一道，这里再按"解出来的真实路径"验一次。
        # abspath 会把中间的 `.`、`//` 归一化，所以这一道看的是落盘时真正的位置。
        if not os.path.abspath(target).startswith(root + os.sep):
            raise BadPackage("包里有写到解压目录外面的路径（{}）".format(info.filename))
        parent = os.path.dirname(target)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _swap_in(staging, dest_dir, pid):
    """把 `staging` 换成 `dest_dir/<pid>`；已经有一份就整体替换。"""
    target = os.path.join(dest_dir, pid)
    old = None
    if os.path.exists(target):
        old = os.path.join(dest_dir, "{}{}.old-{}".format(STAGING_MARK, pid,
                                                          os.getpid()))
        shutil.rmtree(old, ignore_errors=True)
        os.rename(target, old)
    try:
        os.rename(staging, target)
    except OSError:
        # 换不上去（权限、占用……）就把旧的原样放回，别让用户两头落空。
        if old is not None and not os.path.exists(target):
            os.rename(old, target)
        raise
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
    return target


def import_zip(zip_path, dest_dir, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """把包装进 `dest_dir`，返回 `(插件 id, 落点目录)`。

    装好之后插件是**未启用**的（见文件头"导入 ≠ 信任"）。同名插件已存在就整体
    替换——"导入新版本"走的就是这条路，替换完版本号变了，信任那条自然要求用户
    重新确认一次（见 registry.is_enabled）。

    两个上限做成参数**不是给调用方调松紧的**（默认值就是上头的常量），是让探针能
    拿小值把这两道闸真正跑一遍——不然验一次得造两千个文件。
    """
    with _open(zip_path) as zf:
        infos = zf.infolist()

        # 先过一遍所有成员：不安全的一律先揪出来，别解到一半才炸。
        for info in infos:
            if _is_symlink(info):
                raise BadPackage("包里有符号链接（{}）—— 不收这种包"
                                 .format(info.filename))
            reason = _unsafe_reason(_norm(info.filename))
            if reason:
                raise BadPackage("包里有写到解压目录外面的路径（{}：{}）—— 不收这种包"
                                 .format(info.filename, reason))

        if len(infos) > max_files:
            raise BadPackage("包里有 {} 项，超过上限 {} —— 这个包不对劲"
                             .format(len(infos), max_files))
        total = sum(info.file_size for info in infos)
        if total > max_bytes:
            raise BadPackage("包解开之后有 {:.1f} MB，超过上限 {} MB —— 这个包不对劲"
                             .format(total / 1024.0 / 1024.0,
                                     int(max_bytes) // 1024 // 1024))

        manifest = _read_manifest(zf, zip_path)
        pid = manifest.id

        os.makedirs(dest_dir, exist_ok=True)
        staging = os.path.join(dest_dir, "{}{}.staging-{}".format(
            STAGING_MARK, pid, os.getpid()))
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        try:
            _unpack(zf, infos, find_root([_norm(i.filename) for i in infos]),
                    staging)
            target = _swap_in(staging, dest_dir, pid)
        except BaseException:
            # 连 Ctrl-C 也要把半截的收掉，否则下次扫描会看见一坨没名字的东西。
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return pid, target
