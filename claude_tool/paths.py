"""所有目录、文件、命名规则常量都在这儿。

想知道"启动器往硬盘上写了什么"，只看这一个文件就够了：自己的东西全在
~/.claude_tool/ 下面，不跟 Claude Code 的配置混在一起。
"""
import os


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
# 老版本的遗留位置，只用来各迁移一次，迁完就不再看它们
LEGACY_CONFIG = os.path.join(CLAUDE_DIR, "launcher.json")
LEGACY_PREFIX = "settings_"
LEGACY_PRESET_DIR = os.path.join(TOOL_DIR, "models")

PRESET_SUFFIX = ".json"
ILLEGAL_CHARS = r'[<>:"/\\|?*\s]'
