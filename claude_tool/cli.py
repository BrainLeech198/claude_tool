"""命令行：不开窗口的那一套用法。

界面是给"看着点"的场景准备的；命令行是给"写进脚本、绑快捷键、远端敲"的场景
准备的。两边共用同一套底层（agents / config / presets / agent / host），所以行为
不会分家。

这条路唯一的原则是**一个 tkinter 都不 import**：没界面的机器上（服务器、SSH、
CI）也得能跑，而且启动得快。要新加子命令，先确认它用到的东西都还在 tkinter 外
头——`python -c "import claude_tool.cli; import sys; assert 'tkinter' not in
sys.modules"` 一句话就能验（_probe_cli 里也有一条）。

入口在 __main__.py：认得出子命令就走这儿，否则照旧开窗口。
"""
import argparse
import json
import os
import re
import sys

from claude_tool import __version__, agents
from claude_tool.agent import agent_args, find_agent
from claude_tool.config import (
    default_config,
    load_config,
    new_workspace_entry,
    path_key,
    save_config,
)
from claude_tool.handoff import ensure_hook_settings
from claude_tool.host import spawn_terminal
from claude_tool.paths import CONFIG_FILE, ILLEGAL_CHARS, WORKPLACE_DIR
from claude_tool.permissions import (
    PERMISSION_VALUES,
    permission_option,
    workspace_permission,
)
from claude_tool.presets import (
    active_preset,
    apply_preset,
    cached_test_result,
    describe_preset,
    discover_presets,
    preset_path,
    read_env,
    read_model,
)
from claude_tool.shared_memory import ensure_pointers, preamble


# 退出码：0 正常，1 办事失败，2 用法不对（argparse 自己也用 2）。
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class CliError(Exception):
    """办事办砸了。消息是给人看的，直接打到 stderr。"""


def _say(text=""):
    print(text, flush=True)


def _fail(text):
    raise CliError(text)


# ── 配置 ──────────────────────────────────────────────────────────────────


def _config():
    """读配置；一台机器上第一次用时给一份全新的并落盘。

    跟界面首次启动一个行为（ui/launcher.py 那儿也是 load_config() 拿不到就
    default_config() 再存），不然命令行第一次跑 list 会看到一片空，而界面第一
    次开会看到"默认"那一本——两边对不上。
    """
    config = load_config()
    if config is None:
        config = default_config()
        save_config(config)
    return config


def _save(config):
    try:
        save_config(config)
    except OSError as e:
        _fail("配置写不成：{}".format(e))


def _match(config, selector):
    """按"第几个 / 名字 / 路径"找出工作区那条，找不到返回 None。

    第几个就是界面上左栏那个序号（Ctrl+1~9 那个），从 1 起算——命令行里敲
    `go 2` 跟上头敲 Ctrl+2 得是同一本，不然两边的序号对不上。
    """
    workspaces = config["workspaces"]
    if selector.isdigit():
        index = int(selector) - 1
        return workspaces[index] if 0 <= index < len(workspaces) else None
    key = path_key(selector)
    for item in workspaces:
        if item["name"] == selector or path_key(item["path"]) == key:
            return item
    return None


def _label(item):
    """列表里那一行：序号、名字、路径，加上档位和绑定（有就跟）。"""
    bits = [item["name"], item["path"]]
    agent = agents.get(item.get("agent"))
    bits.append(agent.label)
    bound = item.get("preset")
    if bound:
        bits.append("模型：{}".format(bound))
    return "  ".join(bits)


def _pick_workspace(config, selector):
    """定下这次要动哪一本。不给 selector 就用配置里选中那本。"""
    if selector:
        item = _match(config, selector)
        if item is None:
            _fail("没有叫「{}」的工作区。用 list 看看有哪些。".format(selector))
        return item
    selected = config.get("selected_workspace")
    if selected:
        item = _match(config, selected)
        if item is not None:
            return item
    if not config["workspaces"]:
        _fail("还没有工作区。先 new 一个，或者 add 一个已有的目录。")
    return config["workspaces"][0]


# ── 子命令 ────────────────────────────────────────────────────────────────


def cmd_list(args):
    config = _config()
    workspaces = config["workspaces"]
    if args.json:
        _say(json.dumps({
            "count": len(workspaces),
            "workspaces": [{
                "name": item["name"],
                "path": item["path"],
                "agent": agents.get(item.get("agent")).id,
                "preset": item.get("preset") or "",
                "permission": workspace_permission(item),
                "selected": item["path"] == config.get("selected_workspace"),
            } for item in workspaces],
        }, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not workspaces:
        _say("还没有工作区。new 一个，或者 add 一个已有的目录。")
        return EXIT_OK
    for index, item in enumerate(workspaces, 1):
        mark = "*" if item["path"] == config.get("selected_workspace") else " "
        _say("{} {:>2}  {}".format(mark, index, _label(item)))
    return EXIT_OK


def cmd_new(args):
    config = _config()
    name = args.name
    if not name or re.search(ILLEGAL_CHARS, name):
        _fail("名字不能空，也不能含 < > : \" / \\ | ? * 和空格：{!r}".format(name))
    base = os.path.normpath(args.base or config["workplace"] or WORKPLACE_DIR)
    target = os.path.join(base, name)
    if os.path.exists(target) and not os.path.isdir(target):
        _fail("那个位置有个同名文件，建不了目录：{}".format(target))
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        _fail("建不了目录：{}".format(e))
    known = {path_key(w["path"]) for w in config["workspaces"]}
    if path_key(target) not in known:
        config["workspaces"].append(
            new_workspace_entry(args.title or name, target))
    config["workplace"] = base
    roots = [r for r in config["roots"] if path_key(r) != path_key(base)]
    roots.insert(0, base)
    config["roots"] = roots
    _save(config)
    _say("建好了：{}".format(target))
    return EXIT_OK


def cmd_add(args):
    config = _config()
    path = os.path.normpath(args.path)
    if not os.path.isdir(path):
        _fail("找不到目录：{}".format(path))
    if path_key(path) in {path_key(w["path"]) for w in config["workspaces"]}:
        _fail("这个目录已经在列表里了。")
    config["workspaces"].append(
        new_workspace_entry(args.name or os.path.basename(path), path))
    _save(config)
    _say("加进来了：{}".format(path))
    return EXIT_OK


def cmd_remove(args):
    config = _config()
    item = _pick_workspace(config, args.workspace)
    if not args.yes:
        _say("要把「{}」从列表里拿掉？目录本身不删。加 --yes 确认。".format(
            item["name"]))
        return EXIT_USAGE
    config["workspaces"] = [w for w in config["workspaces"] if w is not item]
    if config.get("selected_workspace") == item["path"]:
        config["selected_workspace"] = (config["workspaces"][0]["path"]
                                        if config["workspaces"] else None)
    _save(config)
    _say("移掉了：{}".format(item["name"]))
    return EXIT_OK


def cmd_go(args):
    config = _config()
    item = _pick_workspace(config, args.workspace)
    path = item["path"]
    if not os.path.isdir(path):
        _fail("目录不在：{}".format(path))

    profile = agents.get(args.agent or item.get("agent"))
    permission = args.permission or workspace_permission(item)
    if permission not in profile.permission_modes:
        _fail("{} 不认权限档位 {}。能用的：{}".format(
            profile.label, permission, " / ".join(profile.permission_modes)))
    if find_agent(profile) is None:
        _fail("这台机器上找不到 {} 的命令行。装了再 go，或者 --agent 换一档。"
              .format(profile.label))

    # 绑定预设的 env 走 --settings 传下去（命令行这层压过用户 settings.json），
    # 全局那份一个字节都不动——跟界面 launch_workspace 是同一套。
    settings = None
    bound = item.get("preset")
    if bound:
        source = preset_path(bound, profile.id)
        if os.path.isfile(source):
            env = read_env(source)
            if env:
                try:
                    settings = ensure_hook_settings(
                        env=env, auto_continue=args.auto_continue,
                        hold=False, commander="")
                except OSError as e:
                    _fail("写不了这次的会话配置：{}".format(e))
        else:
            _say("（绑的预设「{}」不在盘上，这次走全局生效的那份）".format(bound))
    elif args.auto_continue:
        try:
            settings = ensure_hook_settings(auto_continue=True)
        except OSError as e:
            _fail("写不了 hook 配置：{}".format(e))

    preamble_text = None
    try:
        ensure_pointers(path)
        preamble_text = preamble(path)
    except Exception:
        pass        # 记忆互通是锦上添花，为它开不了会不值得

    argv = agent_args(profile, cont=args.cont, prompt=args.prompt,
                      settings=settings, permission=permission,
                      append_system_prompt=preamble_text)
    if args.dry_run:
        _say(" ".join(argv))
        return EXIT_OK

    try:
        process = spawn_terminal(path, cont=args.cont, prompt=args.prompt,
                                 settings=settings, permission=permission,
                                 profile=profile,
                                 append_system_prompt=preamble_text)
    except Exception as e:
        _fail("起不来：{}".format(e))
    _say("开了「{}」（{}，权限 {}）：{}".format(
        item["name"], profile.label, permission_option(permission), path))
    _say("pid {}".format(process.pid))
    return EXIT_OK


def cmd_model(args):
    profile = agents.get(args.agent)
    presets = discover_presets(profile.id)
    if args.json:
        _say(json.dumps({
            "agent": profile.id,
            "settings_file": profile.settings_file,
            "presets": [{
                "name": name,
                "model": read_model(path),
                "active": name == active_preset(presets, profile.id),
                "test": cached_test_result(path),
            } for name, path in sorted(presets.items())],
        }, ensure_ascii=False, indent=2))
        return EXIT_OK
    _say("{}（{}）".format(profile.label, profile.settings_file))
    if not presets:
        _say("  还没有预设。设置窗里加，或者 export/import 搬一份过来。")
        return EXIT_OK
    current = active_preset(presets, profile.id)
    for name, path in sorted(presets.items()):
        bits = []
        if name == current:
            bits.append("生效中")
        test = cached_test_result(path)
        if test:
            bits.append("上次 {}：{}".format("测通" if test.get("ok") else "失败",
                                            test.get("note", "")))
        tail = "（{}）".format("，".join(bits)) if bits else ""
        _say("  {:<24} {}{}".format(name, describe_preset(path), tail))
    return EXIT_OK


def cmd_use(args):
    profile = agents.get(args.agent)
    presets = discover_presets(profile.id)
    if args.preset not in presets:
        _fail("{} 没有「{}」这个预设。用 model 看看有哪些。".format(
            profile.label, args.preset))
    try:
        target = apply_preset(args.preset, profile.id)
    except Exception as e:
        _fail("写不进 {}：{}".format(profile.settings_file, e))
    _say("{} 现在用「{}」（{}）。重启 {} 后生效。".format(
        profile.label, args.preset, read_model(preset_path(args.preset,
                                                          profile.id)),
        profile.label))
    _say("写到了 {}".format(target))
    return EXIT_OK


def cmd_doctor(args):
    config = _config()
    problems = []

    _say("配置  {}".format(CONFIG_FILE))
    _say("版本  claude_tool {}".format(__version__))
    for agent_id in agents.ids():
        profile = agents.get(agent_id)
        prefix = find_agent(profile)
        where = " · ".join(prefix) if prefix else "没找到"
        _say("  {:<16} {}".format(profile.label, where))
        if prefix is None:
            problems.append("找不到 {} 的命令行（{}）".format(
                profile.label, " / ".join(profile.exe_names)))
        if not os.path.isfile(profile.settings_file):
            _say("    自己的 settings.json 还没有：{}".format(profile.settings_file))
        presets = discover_presets(profile.id)
        if presets:
            current = active_preset(presets, profile.id)
            _say("    预设 {} 份{}".format(
                len(presets), "，生效中：{}".format(current) if current else ""))
        else:
            _say("    还没有预设")

    missing = [w["name"] for w in config["workspaces"]
               if not os.path.isdir(w["path"])]
    if missing:
        problems.append("这些工作区的目录不在了：{}".format("、".join(missing)))
    _say("工作区  {} 本（默认目录 {}）".format(
        len(config["workspaces"]), config["workplace"]))
    if not os.path.isdir(config["workplace"]):
        problems.append("默认工作区目录不在了：{}".format(config["workplace"]))

    _say()
    if problems:
        _say("有 {} 处要收拾：".format(len(problems)))
        for line in problems:
            _say("  - {}".format(line))
        return EXIT_FAIL
    _say("没查出问题。")
    return EXIT_OK


def cmd_version(args):
    _say("claude_tool {}".format(__version__))
    return EXIT_OK


# ── 参数表 ────────────────────────────────────────────────────────────────


def build_parser():
    parser = argparse.ArgumentParser(
        prog="claude_tool",
        description="Claude Code 启动器的命令行。不带子命令就开窗口。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例子：\n"
               "  claude_tool list\n"
               "  claude_tool new 项目部\n"
               "  claude_tool go 项目部 --agent codebuddy\n"
               "  claude_tool go 2 --continue\n"
               "  claude_tool model --agent codebuddy\n"
               "  claude_tool use deepseek\n"
               "  claude_tool doctor\n")
    parser.add_argument("--version", action="store_true",
                        help="打印版本号就退出")
    sub = parser.add_subparsers(dest="command")

    def add(name, help_text, handler):
        child = sub.add_parser(name, help=help_text)
        child.set_defaults(handler=handler)
        return child

    p = add("list", "列出工作区（* 是当前选中那本）", cmd_list)
    p.add_argument("--json", action="store_true", help="输出 JSON")

    p = add("new", "在默认工作区目录下建一个新文件夹当工作区", cmd_new)
    p.add_argument("name", help="文件夹名（不能含空格和 < > : \" / \\ | ? *）")
    p.add_argument("--title", help="列表里显示的名字，默认用文件夹名")
    p.add_argument("--base", help="建在哪个目录下，默认用配置里的默认工作区")

    p = add("add", "把一个已有的目录加进工作区列表", cmd_add)
    p.add_argument("path", help="目录路径")
    p.add_argument("--name", help="列表里显示的名字，默认用文件夹名")

    p = add("remove", "从列表里拿掉一个工作区（不删目录）", cmd_remove)
    p.add_argument("workspace", nargs="?", help="第几个 / 名字 / 路径")
    p.add_argument("--yes", action="store_true", help="确认拿掉")

    p = add("go", "在一个工作区里开会话", cmd_go)
    p.add_argument("workspace", nargs="?",
                   help="第几个 / 名字 / 路径；不给就用当前选中那本")
    p.add_argument("--agent", choices=list(agents.ids()),
                   help="用哪一档，默认这本工作区自己那个")
    p.add_argument("--permission", choices=list(PERMISSION_VALUES),
                   help="权限档位，默认这本上次挑的")
    p.add_argument("-c", "--continue", dest="cont", action="store_true",
                   help="接着这个目录里最近一次会话聊")
    p.add_argument("-p", "--prompt", help="开场白，起来就先按这句干")
    p.add_argument("--auto-continue", action="store_true",
                   help="挂上自动继续的 Stop hook")
    p.add_argument("--dry-run", action="store_true",
                   help="只把要跑的那条命令打出来，不真开")

    p = add("model", "列出某一档的模型预设", cmd_model)
    p.add_argument("--agent", choices=list(agents.ids()),
                   default=agents.DEFAULT_AGENT, help="管哪一档，默认 claude")
    p.add_argument("--json", action="store_true", help="输出 JSON")

    p = add("use", "把某个预设设成当前生效的（写那个 agent 的 settings.json）",
            cmd_use)
    p.add_argument("preset", help="预设名，用 model 看有哪些")
    p.add_argument("--agent", choices=list(agents.ids()),
                   default=agents.DEFAULT_AGENT, help="改哪一档，默认 claude")

    add("doctor", "查一遍环境：agent 找得到吗、配置和工作区有没有毛病", cmd_doctor)
    add("version", "打印版本号", cmd_version)
    return parser


def main(argv=None):
    """跑命令行。返回进程退出码。

    argv 不给就吃 sys.argv[1:]。第一个参数不像子命令（也不是 --version）时返回
    None，让调用方照旧开窗口——`claude_tool`、`claude_tool --whatever` 都还走
    老路，不能因为加了个 CLI 就把启动方式改了。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return None
    if argv[0] in ("-h", "--help"):
        build_parser().print_help()
        return EXIT_OK
    if argv[0] == "--version":
        _say("claude_tool {}".format(__version__))
        return EXIT_OK
    # 认不得的旗标：返回 None 让调用方照旧开窗口。老用法（`claude_tool`、
    # `claude_tool --某个旗标`）不能因为加了 CLI 就变了。
    if argv[0].startswith("-"):
        return None

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.handler(args)
    except CliError as e:
        print("出错：{}".format(e), file=sys.stderr, flush=True)
        return EXIT_FAIL
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        return 130
