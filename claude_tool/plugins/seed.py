"""把随启动器附带的插件播种到用户插件目录。

## 为什么要播种

启动器运行时**只认一个插件目录**：`~/.claude_tool/plugins/`。用户在插件管理里
点「启用」之后，得能在文件管理器里看到它、改它、备份它——直接从安装包里加载的
内置插件做不到这一点。所以启动时把包里那份复制过去，之后它就跟用户自己导入的
插件一样，是用户目录里的一份普通文件。

## 三条规矩

1. **只播种一次**。id 记在 `plugin_seeds.json` 里；用户把文件夹删了就不再补回来
   ——那是用户主动删的，不是丢了。
2. **用户改过就不动**。播种时记下每个文件的 sha256；下次启动只有"一个文件都没
   变"才敢用包里那份覆盖。改过就原样留着，内置升级也不碰它。
3. **坏种子跳过**。读不成 `plugin.json` 的目录不复制、也不记账——它本来就不是
   插件（比如打包时多塞了个 README 目录），不该在用户目录里长出一坨。
"""
import hashlib
import json
import os
import shutil

from claude_tool import paths
from claude_tool.plugins import manifest as manifest_mod

# 这些是解释器/编辑器留下的缓存，复制过去只会让哈希对不上，用户看着也碍眼。
_CACHE_DIRS = ("__pycache__",)
_CACHE_SUFFIXES = (".pyc", ".pyo")


def _hashes(root):
    """目录里每个文件 -> sha256。键用 `/` 分隔，换机器也不会因为斜杠对不上。"""
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _CACHE_DIRS]
        for name in files:
            if name.endswith(_CACHE_SUFFIXES):
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            digest = hashlib.sha256()
            try:
                with open(full, "rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        digest.update(chunk)
            except OSError:
                # 读不了就当它没出现。这只会让"没改过"的判断更保守，不会误覆盖。
                continue
            out[rel] = digest.hexdigest()
    return out


def _read_state(state_file):
    if not os.path.isfile(state_file):
        return {}
    try:
        with open(state_file, encoding="utf-8") as handle:
            data = json.load(handle)
    except (ValueError, OSError):
        # 账本坏了就当作没播种过。大不了下次把用户改过的副本当成"已有、不碰"。
        return {}
    if not isinstance(data, dict):
        return {}
    seeded = data.get("seeded")
    return seeded if isinstance(seeded, dict) else {}


def _write_state(state_file, seeded):
    folder = os.path.dirname(state_file)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    with open(state_file, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"seeded": seeded}, handle, ensure_ascii=False, indent=2)


def _ignore(_folder, names):
    return [name for name in names
            if name in _CACHE_DIRS or name.endswith(_CACHE_SUFFIXES)]


def _copy(source, target):
    """把 source 整份换成 target。先搭 staging，成功才换上去。"""
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    tag = "{}.{}".format(os.path.basename(target), os.getpid())
    staging = os.path.join(parent, ".{}.staging".format(tag))
    old = os.path.join(parent, ".{}.old".format(tag))
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    try:
        shutil.copytree(source, staging, ignore=_ignore)
        if os.path.exists(target):
            os.rename(target, old)
            try:
                os.rename(staging, target)
            except OSError:
                # 换不上去就把旧的原样放回，别让用户两头落空。
                if not os.path.exists(target):
                    os.rename(old, target)
                raise
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.rename(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def sync(source_dir=None, dest_dir=None, state_file=None):
    """把 `source_dir` 里附带的插件播种/安全刷新到 `dest_dir`。

    返回这一轮新播种或刷新过的 id 列表（按启动器里的顺序）。参数可注入，探针
    用它跑沙箱；不注入就是用户真实目录。
    """
    source_dir = source_dir or paths.PLUGIN_DIR
    dest_dir = dest_dir or paths.USER_PLUGIN_DIR
    state_file = state_file or paths.PLUGIN_SEED_FILE
    seeded = _read_state(state_file)
    changed = []
    try:
        names = sorted(os.listdir(source_dir))
    except OSError:
        return changed

    for name in names:
        if name.startswith(("_", ".")):
            continue
        source = os.path.join(source_dir, name)
        if not os.path.isdir(source):
            continue
        try:
            # read 会顺带验"id 跟目录名一致"——播种出去的名字必须就是 id，
            # 不然 discover 那边会读成坏插件。
            seed_manifest = manifest_mod.read(source)
        except manifest_mod.BadManifest:
            continue

        pid = seed_manifest.id
        target = os.path.join(dest_dir, pid)
        remembered = seeded.get(pid)

        if not isinstance(remembered, dict):
            if os.path.exists(target):
                # 用户自己已经有一份同 id 的：不碰、也不记账。以后内置再变也
                # 轮不到它——用户那份说了算。
                continue
            try:
                _copy(source, target)
            except OSError:
                continue
            seeded[pid] = {"version": seed_manifest.version, "files": _hashes(target)}
            changed.append(pid)
            continue

        if not os.path.isdir(target):
            # 用户把文件夹删了：尊重这个决定，不再补回来。
            continue
        if _hashes(target) != remembered.get("files"):
            # 用户改过：内置升级也不覆盖。
            continue

        fresh = _hashes(source)
        if fresh == remembered.get("files"):
            continue
        try:
            _copy(source, target)
        except OSError:
            continue
        seeded[pid] = {"version": seed_manifest.version, "files": fresh}
        changed.append(pid)

    if changed or not os.path.isfile(state_file):
        _write_state(state_file, seeded)
    return changed
