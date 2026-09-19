"""搬工作区：复制 → 对账 → 旧的丢回收站。

要搬的是两样东西：工作区那个文件夹本身，和 `~/.claude/projects` 下面存它会话
记录的那个目录。后者不跟着搬的话，新位置在启动器眼里就是个没聊过的新工作区，
「接着上次聊」接不上。

**先复制、后删旧的，中间还要对一次账。** 顺序是故意的：任何一步失败就停在那
儿——新复制的那份留着、旧的一个不动，最坏是"多了一份拷贝，你自己删一下"，不会
是"东西没了"。所以这里没有回滚：走到丢回收站那一步时新的那份已经对过账了，
这时候把旧的搬回来才是真危险（配置指着新的，旧的又冒出来，两边都在）。

配置的改动不在这层：那得动界面手里那份 config_data。调用方的顺序是
check → copy → 存配置 → discard（所以 discard 排最后）。

这一层不 import tkinter，也不弹任何窗口——拒了就抛 MoveError，上面把 reason
摆到界面上。理由跟 handoff 那层一样：能单独跑、能单独测。
"""
import os
import shutil

from claude_tool.config import project_dir
from claude_tool.host import trash_path


class MoveError(Exception):
    """这次搬不成，理由在 args[0] 里，是给人看的一句话。"""


def _inside(path, root):
    """path 是不是在 root 里面（含相等）。大小写和正反斜杠的差异不算差异。"""
    path = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root + os.sep)


def _tree_size(root):
    """整棵树里所有普通文件加起来多大。软链接不算（它自己没占地方）。"""
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(base, name)
            if not os.path.islink(full):
                total += os.path.getsize(full)
    return total


def check(source, target, project_from, project_to):
    """搬之前该拒的都在这儿拒掉，拒不了就把话说明白。返回归一化过的新路径。

    只查文件系统和配置这一层的事。"有没有会话正开着""它的 hook 是不是正在写
    交接文档"那些只有界面知道，调用方得在这之前问清楚——那两条比这里的任何
    一条都要紧：文件夹正被写的时候复制，复制出来的是半截的东西，对账也可能
    通不过。
    """
    if not source or not os.path.isdir(source):
        raise MoveError("找不到要搬的目录：{}".format(source))
    if not target:
        raise MoveError("没填新位置。")
    target = os.path.abspath(target)
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(target):
        raise MoveError("新位置跟现在一模一样，没什么可搬的。")
    if os.path.exists(target):
        raise MoveError("新位置已经存在了：{}（不能盖掉已有的东西）".format(target))
    parent = os.path.dirname(target) or "."
    if not os.path.isdir(parent):
        raise MoveError("新位置上面那层目录不存在：{}".format(parent))
    # 搬进自己的子目录等于一边复制一边把刚复制出来的又复制一遍，越滚越大。
    if _inside(target, source):
        raise MoveError("新位置在旧目录里面（{} 底下），那样会越搬越多。"
                        .format(source))
    needed = _tree_size(source)
    # 留点余量：小文件多的时候目录项本身也占地方，照着字节数卡死会正好装不下。
    needed += max(needed // 20, 32 * 1024 * 1024)
    free = shutil.disk_usage(parent).free
    if free < needed:
        raise MoveError("目标盘装不下：要 {}，只剩 {}。"
                        .format(_size(needed), _size(free)))
    # 会话记录那两份指向同一个位置时不用搬（路径里 - 和别的字符换一下就撞一起
    # 了，比如 D:\\a-b 和 D:\\a\\b 都是 D--a-b），这时不算错。
    if project_from and project_to and \
            not _same_dir(project_from, project_to) and os.path.exists(project_to):
        raise MoveError("新位置那边已经有一份会话记录了（{}），不合并——"
                        "两份掺在一起分不清谁是谁。".format(project_to))
    return target


def _same_dir(one, two):
    return os.path.normcase(os.path.abspath(one)) == os.path.normcase(
        os.path.abspath(two))


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{:.1f} {}".format(n, unit) if unit != "B" else "{} B".format(n)
        n /= 1024.0


def _manifest(root):
    """整棵树 -> {相对路径: 大小}。目录记 None，软链接记它指向哪儿。

    只认路径和大小，不认 mtime：跨文件系统复制时 mtime 的精度对不上（FAT 两秒
    一颗、ext4 纳秒），拿它当判据会误报一片。
    """
    out = {}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                out[rel] = "->" + os.readlink(full)
            elif os.path.isdir(full):
                out[rel] = None
            else:
                out[rel] = os.path.getsize(full)
    return out


def _verify(source, target):
    """复制完再走一遍两棵树，逐条对路径和大小。对不上就报出来。

    copytree 那一套要么抛异常要么复制完，所以这次对账主要是兜住三件事：复制
    过程中源还在被写、盘满了写了一半、权限之类的边角没带过去。
    """
    want = _manifest(source)
    got = _manifest(target)
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    differ = sorted(k for k in set(want) & set(got) if want[k] != got[k])
    if missing or differ or extra:
        detail = []
        if missing:
            detail.append("少了 {} 项（{}）".format(
                len(missing), "、".join(missing[:3])))
        if differ:
            detail.append("对不上的 {} 项（{}）".format(
                len(differ), "、".join(differ[:3])))
        if extra:
            detail.append("多出来的 {} 项".format(len(extra)))
        raise MoveError("复制完了对账没通过：{}。新位置那份先别用，旧的没动。"
                        .format("；".join(detail)))


def copy_tree(source, target, progress=None):
    """把整棵树复制到新位置，复制完对一次账。

    自己走树而不用 shutil.copytree：copytree 是黑盒，中间一点消息都报不出来，
    搬一个几个 G 的目录就是干等着、界面看着像死了。自己走就能一路把"到哪儿了"
    报给调用方。目录的时间戳留到最后再补——先补的话，只读的目录后面就写不进去
    了（copytree 也是这么干的，这里只是拆开摊平了）。
    """
    os.makedirs(target)
    dirs_done = []
    count = 0
    for base, dirs, files in os.walk(source):
        rel = os.path.relpath(base, source)
        here = target if rel == "." else os.path.join(target, rel)
        for name in dirs:
            src = os.path.join(base, name)
            dst = os.path.join(here, name)
            if os.path.islink(src):
                os.symlink(os.readlink(src), dst)
            else:
                os.makedirs(dst)
                dirs_done.append((src, dst))
        for name in files:
            src = os.path.join(base, name)
            dst = os.path.join(here, name)
            if os.path.islink(src):
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst)
            count += 1
            if progress:
                progress(count)
    for src, dst in reversed(dirs_done):
        shutil.copystat(src, dst)
    shutil.copystat(source, target)
    _verify(source, target)
    return count


def copy_project(source, target, progress=None):
    """把会话记录那份也搬过去。返回搬没搬——没聊过就没有这一份，不是错。

    挪的是整个目录而不是挑几个 .jsonl：那里面还有 claude 自己的 memory 之类的
    子目录，一起走才叫"记忆跟着搬"。
    """
    if not source or not os.path.isdir(source):
        return False
    if _same_dir(source, target):
        return False
    copy_tree(source, target, progress)
    return True


def discard(source, project_from):
    """把旧的那两份丢进回收站。返回没丢成的那几个路径。

    到这一步新的那份已经复制好、对过账、配置也改了，所以这里失败【不回滚】——
    丢不成的如实报出来让用户自己处理，绝不改成硬删。
    """
    stuck = []
    for path in (source, project_from):
        if not path or not os.path.exists(path):
            continue
        if not trash_path(path):
            stuck.append(path)
    return stuck


def project_paths(source, target):
    """新旧位置各自对应哪份会话记录目录。"""
    return project_dir(source), project_dir(target)
