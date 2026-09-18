"""权限等级表，对应 claude 的 --permission-mode。

以前这儿写死 --dangerously-skip-permissions，等于永远挑最松的那档；现在每次
启动自己选，选过的按工作区记下来。
"""


# 本次会话的权限等级，对应 claude 的 --permission-mode。以前这里写死
# --dangerously-skip-permissions，等于永远挑最松的那档；现在每次启动自己选，
# 选过的按工作区记下来。元组是 (给 claude 的值, 界面上显示的字, 一句解释)。
PERMISSION_MODES = (
    ("default", "每次都问", "用任何工具之前都先问你一句，最稳当。"),
    ("acceptEdits", "改文件不问，跑命令才问", "改、写、移动文件直接做，跑命令还是要你点头。"),
    ("plan", "只规划不动手", "只读代码、给方案，一个文件都不会改。"),
    ("auto", "让模型替你批", "另开一个模型判断该不该放行；官方还标着研究预览，偶尔判错。"),
    ("bypassPermissions", "什么都不问", "权限检查整个跳过。只在你百分之百信得过的目录里用。"),
)
DEFAULT_PERMISSION = "acceptEdits"
PERMISSION_VALUES = tuple(mode for mode, _, _ in PERMISSION_MODES)


def permission_label(value):
    """给 claude 的值 -> 界面上显示的字。认不出来就原样返回。"""
    for mode, label, _ in PERMISSION_MODES:
        if mode == value:
            return label
    return value


def permission_option(value):
    """下拉里那一行：中文解释 + 括号里的真名。

    光写中文解释，熟练用户反而找不着——他脑子里记的是 acceptEdits、
    bypassPermissions 这些字符串，是照着文档和命令行来的。两个都给，各取所需。
    """
    return "{}（{}）".format(permission_label(value), value)


def permission_hint(value):
    """给 claude 的值 -> 那句解释。

    只写大白话：真名和命令行参数摆在别处（下拉里带括号的真名 + 下面那行
    --permission-mode），不用挤在这一句里，挤进来换档时还会撑高对话框。
    """
    for mode, _, hint in PERMISSION_MODES:
        if mode == value:
            return hint
    return ""


def workspace_permission(item):
    """这个工作区上次挑的权限等级。

    没挑过、或者存的值是旧的/手改坏了的，一律回落到默认那档——工作区条目在
    好几个地方现造（新建文件夹、选已有目录、重新扫描），不是每条都带这个字段。
    """
    mode = item.get("permission")
    return mode if mode in PERMISSION_VALUES else DEFAULT_PERMISSION
