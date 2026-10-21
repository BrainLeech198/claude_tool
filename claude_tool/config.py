"""launcher.json 的读写，以及工作区是怎么被扫出来的。

配置里坏了、缺了字段一律回落到默认值，不报错——这是用户手改过的文件。
"""
import json
import os
import re
import time

from claude_tool import agents, history, theme
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


def new_workspace_entry(name, path):
    """新建一条工作区记录的完整形状。

    default_config、merge_scanned、以及界面那两个"加工作区"的入口（新建文件夹 /
    选已有目录）都得用同一个形状，所以收成这一处。load_config 那段重建也是按
    这个形状补齐旧配置的（见它注释：新加的字段最容易在那儿被悄悄吃掉）。

    故意不含 permission：那一格是读的时候按 item 现算的（见
    permissions.workspace_permission），没写就等于用默认值。
    """
    return {"name": name, "path": path,
            "handoff_ignore_git": False,
            "agent": agents.DEFAULT_AGENT,
            # 这本文档绑的模型预设名（空 = 不绑，跟随全局 / 自带默认）。
            # 绑了的话，开会话时它的 env 走 --settings 传下去，不覆盖全局配置。
            "preset": ""}


def default_config():
    """全新安装时的配置：默认工作区目录建在工具目录下，它本身也算第一个工作区。"""
    return {
        "workplace": WORKPLACE_DIR,
        "roots": [WORKPLACE_DIR],
        "workspaces": [new_workspace_entry("默认", WORKPLACE_DIR)],
        "window": None,
        # 外观：theme.PALETTES 里的名字（paper / clay / frost / midnight /
        # abyss / plum）。换的时候由 launcher.apply_theme 落盘，下次开窗在读
        # 配置之后、建界面之前就套上（见 theme.apply）。
        "theme": "paper",
        # 左栏当前选中那一本（记路径不记名字：改名不影响选中）。
        # 老配置文件里没这个键，读的地方一律用 .get()，别用 []。
        "selected_workspace": None,
        # 底下这一排勾都记着上次的状态，但首次装上一定是关的：内嵌终端会改变新开
        # 的会话开在哪儿，自动继续会替用户拍板，自动查 claude 新版会联网，都不该
        # 悄悄替用户打开。（启动器查**自己**新版那条 0.4 起没开关了，每次启动都
        # 查，见下面 auto_self_update 那行。）
        "auto_continue": False,
        "embed": False,
        "auto_version_check": False,
        # 这个键 0.4 起没用了：启动器现在每次都自动查自己的新版，不再由用户开关。
        # 老配置文件里留着它，读的时候忽略，不删也不迁移——为这个跑一次迁移不值当。
        "auto_self_update": False,
        # 「AI 托管」上次选的档、上次丢给它的那个工作区、以及第 2 档用哪个模型
        # 当指挥。记着是为了下次打开对话框直接停在原处，不用重挑。档位只认 1/2/3。
        "autonomy": 1,
        "autonomy_workspace": "",
        "autonomy_commander": "",
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
                # 每个字段都得在这儿列一遍：这段是重建条目，没列到的键会被
                # 丢掉——新加的字段最容易在这儿被悄悄吃掉，存了等于没存。
                workspaces.append({
                    "name": str(item.get("name") or os.path.basename(path)),
                    "path": path,
                    "permission": workspace_permission(item),
                    "handoff_ignore_git": bool(item.get("handoff_ignore_git")),
                    "agent": item.get("agent") if item.get("agent") in agents.ids()
                    else agents.DEFAULT_AGENT,
                    # 绑的模型预设名。不查它存在不存在——预设可能刚被删、或者被
                    # 手改名了；真有对不上，开会话那步会当"没绑"照常开。
                    "preset": item.get("preset").strip()
                    if isinstance(item.get("preset"), str) else "",
                })
        config["workspaces"] = workspaces
    config["auto_continue"] = bool(data.get("auto_continue"))
    config["embed"] = bool(data.get("embed"))
    config["auto_version_check"] = bool(data.get("auto_version_check"))
    # 不认识的、老配置里没有的，一律回到默认那套。老配置里的 light / dark
    # 由 theme.canonical 翻成新名字，升级后不会退回默认色。
    config["theme"] = theme.canonical(data.get("theme")) or "paper"
    # 档位是手改得坏的：写个 4、写个字符串，都当没写，回落到第 1 档。
    tier = data.get("autonomy")
    config["autonomy"] = tier if tier in (1, 2, 3) else 1
    remembered = data.get("autonomy_workspace")
    config["autonomy_workspace"] = remembered.strip() if isinstance(remembered, str) else ""
    # 指挥模型记的是预设名。那个预设后来被删了也不清空——对话框打开时会发现名字
    # 对不上而退回第一个可选项，比在这儿偷偷抹掉、下次打开发现选择没了更清楚。
    deputy = data.get("autonomy_commander")
    config["autonomy_commander"] = deputy.strip() if isinstance(deputy, str) else ""
    return config


def save_config(config):
    os.makedirs(TOOL_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")


def projects_dir_for(agent_id):
    """这个 agent 的会话记录目录。认不出来的回落 claude。"""
    return agents.get(agent_id).projects_dir


def project_dir(path, agent_id=agents.DEFAULT_AGENT):
    """工作目录 -> 这个 agent 的会话记录目录（claude 那一档的命名规则）。

    移动工作区时用它算"新旧位置各自那份记录"（见 mover.project_paths），那边只
    处理 claude 的目录。列会话不走这儿——两档的规则不一样，见 history.project_dirs。

    Claude Code 的命名规则是把路径里所有非字母数字的字符换成短横：D:\\Desktop
    变成 D--Desktop，D:\\File\\claude\\kk 变成 D--File-claude-kk。猜错了也只是
    显示成"还没聊过"，不会出错。
    """
    return os.path.join(agents.get(agent_id).projects_dir,
                        re.sub(r"[^A-Za-z0-9]", "-", path))


def last_chat_time(path, agent_id=None):
    """这个工作区最近一次会话是什么时候。找不着就返回 None。

    按这本当前用的档位去它自己的会话库里找：以前写死 `~/.claude/projects`，
    切到 CodeBuddy 的工作区永远显示"没聊过"。
    """
    return history.latest_time(agent_id or agents.DEFAULT_AGENT, path)


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
            config["workspaces"].append(
                new_workspace_entry(item["name"], item["path"]))
            added.append(item["name"])
    return added
