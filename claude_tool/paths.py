"""所有目录、文件、命名规则常量都在这儿。

想知道"启动器往硬盘上写了什么"，只看这一个文件就够了：自己的东西全在
~/.claude_tool/ 下面，不跟 Claude Code 的配置混在一起。
"""
import os
import sys


CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
# 这个文件是 Claude Code 自己的，永远不动它——换模型就是往里覆盖一份新内容。
SETTINGS_FILE = os.path.join(CLAUDE_DIR, "settings.json")

# 启动器自己的东西全在这一个目录下，不跟 Claude Code 的配置混在一起。
#   ~/.claude_tool/
#     launcher.json     工作区列表、窗口位置、默认目录
#     claude_settings/  模型预设，一个模型一个 json，由启动器接管
#     workplace/        默认工作区目录，新建的文件夹都建在这儿
TOOL_DIR = os.path.join(os.path.expanduser("~"), ".claude_tool")
CONFIG_FILE = os.path.join(TOOL_DIR, "launcher.json")
PRESET_DIR = os.path.join(TOOL_DIR, "claude_settings")
WORKPLACE_DIR = os.path.join(TOOL_DIR, "workplace")

# 启动器自己升级时下载的安装包落在哪儿。留在用户数据目录下面（而不是系统临时
# 目录）是有意的：装完那一步用户没点"是"、或者安装包跑不起来，文件还在原地，
# 他自己能找过去双击。名字跟安装包同名，一看就知道是哪一版（见 selfupdate.py）。
DOWNLOAD_DIR = os.path.join(TOOL_DIR, "downloads")

# 便携版 Node + npm 那条路（见 install.node_setup）把东西搁在这儿：官方那个
# Node 便携包解在这，npm 的 --prefix 也指着它，所以 claude 的 shim 就跟 node
# 挨着——npm 生成的 shim 是去自己旁边找 node 来跑自己的，两个分开放就跑不起来。
# 整个目录都是启动器自己拉下来的，卸载就是把它删掉，系统里不留别的痕迹。
NODE_DIR = os.path.join(TOOL_DIR, "node")

# Claude Code 官方那个原生安装脚本把 claude 装在这儿（Windows 上同一个目录，
# 只是带扩展名）。这是**别人的**东西，启动器只读不写；摆在这个文件里是因为
# find_claude 得认它——刚装完这会儿启动器自己的 PATH 还没刷新，不认这儿就
# 找不到刚装好的那个。
LOCAL_BIN = os.path.join(os.path.expanduser("~"), ".local", "bin")
LOCAL_NAMES = ("claude", "claude.exe", "claude.cmd")

# 老版本的遗留位置，只用来各迁移一次，迁完就不再看它们
LEGACY_CONFIG = os.path.join(CLAUDE_DIR, "launcher.json")
LEGACY_PREFIX = "settings_"
LEGACY_PRESET_DIR = os.path.join(TOOL_DIR, "models")

# 窗口图标。源码跑的时候在仓库的 build/ 底下，打包之后 PyInstaller 会把它摊在
# sys._MEIPASS（onedir 就是 exe 旁边那个 _internal/）里，两条路都得认，
# spec 里 datas 那一行就是管后面这条的。
ICON_FILE = (
    os.path.join(sys._MEIPASS, "icon.png")
    if getattr(sys, "frozen", False)
    else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "build", "icon.png")
)

PRESET_SUFFIX = ".json"
ILLEGAL_CHARS = r'[<>:"/\\|?*\s]'

# 预设名要落成 <名字>.json 这个文件，管的只是"文件名里不能出现什么"，所以空格是
# 合法的：Windows 那条路给 --settings 拼命令行时会整个包引号（见 claude_command），
# Linux 那边是一项一个元素的 argv，两边都不怕空格。
# 原来这里跟 ILLEGAL_CHARS 共用一条规则、把空格也挡了，可内置供应商表里就有 6 家
# 名字带空格（「智谱 GLM」这种），从下拉里挑一家就把一个存不下去的名字填进了表单。
ILLEGAL_FILE_CHARS = r'[<>:"/\\|?*]'


# 插件目录。跟 ICON_FILE 一样两条路：源码跑的时候在包自己的 plugins/ 底下，
# 打包之后 PyInstaller 把 datas 摊在 sys._MEIPASS（onedir 就是 exe 旁边那个
# _internal/）里。
#
# **不要用 hiddenimports 收插件**（spec 里那份是手工维护的清单）：加一个插件就得
# 改一次 spec，而插件本来就是"往目录里丢一个文件夹"的东西。当 datas 收源码、
# 运行时用 importlib 加载。
PLUGIN_DIR = (
    os.path.join(sys._MEIPASS, "plugins")
    if getattr(sys, "frozen", False)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
)

# 用户自己导入的插件落在这儿。**必须跟上面那个分开**：上面那个是"随启动器一起
# 发出去的内置插件"，打包之后在 `_internal/` 里——那地方跟着安装包走，升级一次
# 覆盖一次，用户的插件放进去就没了。用户导入的插件是**用户数据**，跟 launcher.json
# 一样该待在自己的数据目录里，升级、卸载重装都不受影响（卸干净才一起走）。
#
# 两处都扫，**同名时用户那份优先**（见 plugins/registry.discover）——这样用户能
# 用一个自己改过的版本盖掉内置的同名插件，而不用去动安装目录。
USER_PLUGIN_DIR = os.path.join(TOOL_DIR, "plugins")

# 插件在启动器这边的状态（信没信过、启没启用）。**跟 launcher.json 分开放**：
# 那份是用户的工作区配置，这份是"我信任过哪些插件的哪个版本"，混在一起以后两边
# 的迁移会互相拖累——用户想重置工作区不该顺带把信任记录也清了。
PLUGIN_STATE_FILE = os.path.join(TOOL_DIR, "plugins.json")
