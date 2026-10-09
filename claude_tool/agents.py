"""认识启动器能拉起来的每一个命令行 agent，以及它们各自的家在哪。

claude 和 codebuddy 是同一套东西的两个牌子：命令行旗标几乎一样
（`--permission-mode` / `--continue` / `--settings` / `--append-system-prompt`），
差别只在可执行文件、配置家目录、装法。这里把"不一样"的那部分收成一张表，
别处一律来这儿问，不再各自写死 "claude" 这个名字。

这是**叶子模块**：只认 os/shutil 和 paths/permissions，不 import 上层
（install、走 host 的那些）。install 那边反过来会 import 它，绕成环就赖在这儿。
"""
import os
import shutil

from claude_tool.paths import CLAUDE_DIR, LOCAL_BIN, NODE_DIR, PRESET_DIR, TOOL_DIR
from claude_tool.permissions import PERMISSION_VALUES


def workbuddy_cli_dirs():
    """WorkBuddy（codebuddy 那个桌面程序）可能把 CLI 摊在哪些目录。

    它是 Electron 应用，CLI 落在 `<装目录>/resources/app.asar.unpacked/cli/bin`
    底下：一个**没有扩展名**的 node 脚本，既不在 PATH 上、注册表里也没有它的
    安装位置（App Paths 里查不到），只能按常见位置猜——

      - 个人安装（Electron 默认）：`%LOCALAPPDATA%\\Programs\\WorkBuddy`
      - 全机安装：每个盘符的 `\\Program Files\\WorkBuddy`、`\\Program Files (x86)\\…`

    盘符得挨个试：这台机器就装在 `D:\\Program Files\\WorkBuddy`，`%ProgramFiles%`
    那个变量指的是 C 盘，照它找不到。

    做成**函数**而不是模块级元组，是因为它要挨个盘符 stat——断开的网络盘上
    isdir 会卡住，不能一 import 就付这个账，等真要找的时候再算。
    """
    suffix = os.path.join("WorkBuddy", "resources", "app.asar.unpacked",
                          "cli", "bin")
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Programs"))
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for program in ("Program Files", "Program Files (x86)"):
            root = "{}:\\{}".format(letter, program)
            if os.path.isdir(root):
                roots.append(root)
    return tuple(os.path.join(root, suffix) for root in roots)


class Agent:
    """一个命令行 agent 的档位。

    id           写进配置、探针里认的那个短名（"claude" / "codebuddy"）。
    label        界面上给人看的名字。
    exe_names    可执行文件可能的文件名，挨个去 PATH 和候选目录里找。
    config_dir   这个 agent 的配置家目录（settings、会话记录都在这底下）。
    settings_file 它自己那份 settings.json 的完整路径。
    preset_dir   启动器存"模型预设"的目录。**是启动器自己的东西**，按 agent 分开，
                 免得两口子往同一个目录里堆同名文件。
    projects_dir 会话记录目录。
    config_dir_env 能覆盖 config_dir 的环境变量名；没有这种变量的填 None。
    needs_node   是不是得靠系统的 node 来跑（没打包成 exe 的 node 脚本要）。
    npm_package  npm 上的包名，用来拼安装命令；没有就 None。
    install_docs 官方装法说明的地址。
    permission_modes 这个 agent 认的权限档位。
    search_dirs  PATH 之外还去哪儿找可执行文件。可以给元组，也可以给一个返回
                 元组的**函数**（要挨个盘符去 stat 的那种，见 workbuddy_cli_dirs）。
    version_args 问它版本时给的那几个参数。
    """

    def __init__(self, id, label, exe_names, config_dir, settings_file,
                 preset_dir, projects_dir, config_dir_env=None, needs_node=False,
                 npm_package=None, install_docs="", permission_modes=PERMISSION_VALUES,
                 search_dirs=(), version_args=("--version",)):
        self.id = id
        self.label = label
        self.exe_names = tuple(exe_names)
        self.config_dir = config_dir
        self.settings_file = settings_file
        self.preset_dir = preset_dir
        self.projects_dir = projects_dir
        self.config_dir_env = config_dir_env
        self.needs_node = needs_node
        self.npm_package = npm_package
        self.install_docs = install_docs
        self.permission_modes = tuple(permission_modes)
        self._dirs = search_dirs
        self.version_args = tuple(version_args)

    def search_dirs(self):
        """PATH 之外还去哪些目录找可执行文件（元组）。"""
        return tuple(self._dirs() if callable(self._dirs) else self._dirs)

    def exe_names_for(self, directory):
        """在指定目录里，这个 agent 可能落的几个完整路径。"""
        return tuple(os.path.join(directory, name) for name in self.exe_names)

    def find(self, which=shutil.which, extra=()):
        """找这个 agent 的命令行前缀，返回 token 列表；找不到返回 None。

        返回值是**列表**不是字符串：codebuddy 是个 node 脚本、没有 Windows
        shim，前缀得是 `[node, 脚本]` 两个 token；claude 落在 PATH 上是
        `[claude.cmd]` 一个。上层把前缀接到 argv 最前面就行，两种形状通吃。

        `which` 能塞假的进来（跟 install.routes 那边一个路数）：探针要撞
        "PATH 里没有、但某个目录里躺着"这一种，不能去改这台机器真正的 PATH。
        extra 是额外先找的目录（比如用户手填的一个位置）。
        """
        for name in self.exe_names:
            found = which(name)
            if found:
                return self._prefix(found, which)
        for directory in tuple(extra) + self.search_dirs():
            for name in self.exe_names:
                candidate = os.path.join(directory, name)
                if os.path.exists(candidate):
                    return self._prefix(candidate, which)
        return None

    def _prefix(self, script, which):
        """把一个找到的文件凑成命令行前缀，该配 node 的配上。"""
        # 已经是 Windows 能直接执行的东西（shim/exe），就不用 node 去跑它——
        # 拿 node 去跑一个 .cmd 是跑不起来的。
        if not self.needs_node or script.lower().endswith(
                (".cmd", ".exe", ".bat", ".com")):
            return [script]
        # 没 shim 的 node 脚本：得有个系统 node 才带得动。node 不在就当"没有
        # 这个 agent"，不能拿半个命令去开窗。
        node = which("node")
        return [node, script] if node else None

    def version_argv(self):
        """问这个 agent 版本号的参数（不含它自己的命令行前缀）。"""
        return list(self.version_args)


CLAUDE = Agent(
    id="claude",
    label="Claude Code",
    exe_names=("claude", "claude.exe", "claude.cmd"),
    config_dir=CLAUDE_DIR,
    settings_file=os.path.join(CLAUDE_DIR, "settings.json"),
    preset_dir=PRESET_DIR,
    projects_dir=os.path.join(CLAUDE_DIR, "projects"),
    config_dir_env=None,
    needs_node=False,
    npm_package="@anthropic-ai/claude-code",
    install_docs="https://code.claude.com/docs/en/setup",
    permission_modes=PERMISSION_VALUES,
    # 装完当场就能认：官方原生脚本的 ~/.local/bin，和便携版 Node 那条路
    # （Windows 上 shim 直接摆在 NODE_DIR，别的平台在它底下的 bin/）。
    search_dirs=(LOCAL_BIN, os.path.join(NODE_DIR, "bin"), NODE_DIR),
)

CODEBUDDY = Agent(
    id="codebuddy",
    label="CodeBuddy Code",
    exe_names=("codebuddy", "codebuddy.cmd", "codebuddy.exe"),
    # CLI 自己的默认家目录是 ~/.codebuddy（不是桌面端那个 ~/.workbuddy）——
    # 它已经带着凭据和会话记录，重定向过去等于把这些孤立掉，所以就用它默认的。
    config_dir=os.path.join(os.path.expanduser("~"), ".codebuddy"),
    settings_file=os.path.join(os.path.expanduser("~"), ".codebuddy",
                               "settings.json"),
    preset_dir=os.path.join(TOOL_DIR, "codebuddy_settings"),
    projects_dir=os.path.join(os.path.expanduser("~"), ".codebuddy", "projects"),
    config_dir_env="CODEBUDDY_CONFIG_DIR",
    needs_node=True,
    npm_package=None,
    install_docs="https://www.codebuddy.ai/",
    # 比 claude 多的那个 dontAsk 一并收下；两边共用的规矩是：传进来的权限值不
    # 在 permission_modes 里就退回默认（见 permissions.workspace_permission）。
    permission_modes=("default", "acceptEdits", "plan", "dontAsk", "auto",
                      "bypassPermissions"),
    search_dirs=workbuddy_cli_dirs,
)

AGENTS = {CLAUDE.id: CLAUDE, CODEBUDDY.id: CODEBUDDY}

DEFAULT_AGENT = "claude"


def get(agent_id):
    """按 id 拿档位。认不出来一律回落 claude——**不抛异常**：

    配置里那个 agent 字段可能是从旧版本迁移过来没有、或者被手改成了一个不认得的
    名字。为这个把启动器整个起不来，代价太大；退回 claude 顶多是把一本工作区当成
    claude 工作区，用户自己改回来就是了。
    """
    return AGENTS.get(agent_id) or AGENTS[DEFAULT_AGENT]


def ids():
    """所有内置档位的 id，顺序跟 AGENTS 里声明的一致。"""
    return tuple(AGENTS)


def labels():
    """id -> 界面显示名。给下拉、设置窗列表用。"""
    return {agent_id: agent.label for agent_id, agent in AGENTS.items()}
