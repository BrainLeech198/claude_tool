"""launcher.json 的读写，以及工作区是怎么被扫出来的。

配置里坏了、缺了字段一律回落到默认值，不报错——这是用户手改过的文件。
"""
import json
import os
import re
import time

from claude_tool.paths import (
    CLAUDE_DIR,
    CONFIG_FILE,
    LEGACY_CONFIG,
    TOOL_DIR,
    WORKPLACE_DIR,
)
from claude_tool.permissions import workspace_permission


# ── 工作区 ────────────────────────────────────────────────────────────────


def path_key(path):
    """比较路径用：大小写和正反斜杠的差异都不算差异。"""
    return os.path.normcase(os.path.normpath(path))


def default_config():
    """全新安装时的配置：默认工作区目录建在工具目录下，它本身也算第一个工作区。"""
    return {
        "workplace": WORKPLACE_DIR,
        "roots": [WORKPLACE_DIR],
        "workspaces": [{"name": "默认", "path": WORKPLACE_DIR}],
        "window": None,
        # 自动刷交接文档会多花 token，所以首次装上一定是关的
        "auto_handoff": False,
    }


def load_config():
    """读工具目录下的配置；那儿没有就翻一次旧位置，把老配置原样接过来。

    都没有就返回 None，调用方据此判断这是不是全新安装。
    """
    data = None
    for path in (CONFIG_FILE, LEGACY_CONFIG):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            break
        except Exception:
            continue
    if not isinstance(data, dict):
        return None

    config = default_config()
    workplace = data.get("workplace")
    if isinstance(workplace, str) and workplace.strip():
        config["workplace"] = os.path.normpath(workplace.strip())
    # 没写 roots 就跟着 workplace 走；写了就以写的为准
    config["roots"] = [config["workplace"]]
    window = data.get("window")
    if isinstance(window, dict) and all(
            isinstance(window.get(k), int) and abs(window[k]) < 32768
            for k in ("x", "y", "w", "h")) and window["w"] >= 300 and window["h"] >= 300:
        config["window"] = {k: window[k] for k in ("x", "y", "w", "h")}
    if isinstance(data.get("roots"), list) and data["roots"]:
        config["roots"] = [r for r in data["roots"] if isinstance(r, str)]
    # 默认工作区目录永远在扫描范围内，"重新扫描"才扫得到它下面手建的文件夹
    if all(path_key(r) != path_key(config["workplace"]) for r in config["roots"]):
        config["roots"].insert(0, config["workplace"])
    if isinstance(data.get("workspaces"), list):
        workspaces = []
        for item in data["workspaces"]:
            if isinstance(item, dict) and item.get("path"):
                path = item["path"]
                workspaces.append({
                    "name": str(item.get("name") or os.path.basename(path)),
                    "path": path,
                    "permission": workspace_permission(item),
                })
        config["workspaces"] = workspaces
    config["auto_handoff"] = bool(data.get("auto_handoff"))
    return config


def save_config(config):
    os.makedirs(TOOL_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")


def project_dir(path):
    """工作目录 -> ~/.claude/projects 下面存这个项目会话记录的那个目录。

    Claude Code 的命名规则是把路径里所有非字母数字的字符换成短横：D:\\Desktop
    变成 D--Desktop，D:\\File\\claude\\kk 变成 D--File-claude-kk。猜错了也只是
    显示成"还没聊过"，不会出错。
    """
    return os.path.join(PROJECTS_DIR, re.sub(r"[^A-Za-z0-9]", "-", path))


def last_chat_time(path):
    """这个工作区最近一次会话是什么时候。找不着就返回 None。

    会话记录是目录里一个个 .jsonl，最大 mtime 就是最后一次动过的时间。只算
    .jsonl：那目录里还可能有 memory 之类的子目录，不能一并算进去。
    """
    folder = project_dir(path)
    try:
        stamps = [os.path.getmtime(os.path.join(folder, name))
                  for name in os.listdir(folder) if name.endswith(".jsonl")]
    except OSError:
        return None
    return max(stamps) if stamps else None


def humanize_ago(stamp):
    """时间戳 -> 「刚刚」「3 小时前」「5 天前」这种说法。"""
    delta = max(time.time() - stamp, 0)
    if delta < 90:
        return "刚刚"
    if delta < 3600:
        return "{} 分钟前".format(int(delta // 60))
    if delta < 86400:
        return "{} 小时前".format(int(delta // 3600))
    if delta < 86400 * 30:
        return "{} 天前".format(int(delta // 86400))
    if delta < 86400 * 365:
        return "{} 个月前".format(int(delta // (86400 * 30)))
    return "{} 年前".format(int(delta // (86400 * 365)))


def scan_roots(roots):
    """扫 roots 下的一级子文件夹，返回 [{name, path}]。"""
    found = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root), key=str.lower):
            path = os.path.join(root, entry)
            if not os.path.isdir(path):
                continue
            key = os.path.normcase(os.path.normpath(path))
            if key in seen:
                continue
            seen.add(key)
            found.append({"name": entry, "path": path})
    return found


def merge_scanned(config):
    """把 roots 下新出现的目录并进工作区列表；已存在的按路径去重，名称不动。"""
    known = {os.path.normcase(os.path.normpath(w["path"])) for w in config["workspaces"]}
    added = []
    for item in scan_roots(config["roots"]):
        key = os.path.normcase(os.path.normpath(item["path"]))
        if key not in known:
            known.add(key)
            config["workspaces"].append(item)
            added.append(item["name"])
    return added
