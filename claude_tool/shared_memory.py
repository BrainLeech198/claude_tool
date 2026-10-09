"""跨工具共享记忆：项目根 AGENTS.md + 各工具的静态互链。

claude 和 codebuddy 各有自己的专属记忆目录（~/.claude/、~/.codebuddy/），
但项目级的长期约定应该写在一份两边都看得到的地方——AGENTS.md（业界惯例）。
这一层管三件事：

1.  找到共享记忆（shared_file）。
2.  生成启动注入文本（preamble）：告诉 AI 先读 AGENTS.md、新记忆写进它。
3.  建静态互链（ensure_pointers）：CLAUDE.md / CODEBUDDY.md 里写一句"去
    AGENTS.md 看"，让工具自己的记忆入口也能找到共享那份。

**注入文本不含双引号**：Windows 那条路把整条命令行塞给 cmd，值里的双引号会
把引号配对搅乱（见 agent.agent_command 的说明）。
"""
import os

SHARED_NAME = "AGENTS.md"

# 各工具自己的记忆入口文件名 -> 都指向 SHARED_NAME。
POINTERS = {
    "claude": "CLAUDE.md",
    "codebuddy": "CODEBUDDY.md",
}

# 注入文本定死不含双引号。意思是：先读 AGENTS.md，新记忆写进它、别写进你
# 自己工具的专属记忆目录。
_PREAMBLE = (
    "本仓库有一份跨工具共享记忆 ./AGENTS.md。动手之前先读它，把它当作本项目"
    "已有的长期约定。本次产生的新长期记忆写进这份共享记忆，不要写进你这个工具"
    "自己的专属记忆目录。"
)

# 指针文件的定死内容。
_POINTER_TEXT = "见同目录下 AGENTS.md —— 本项目所有 AI 工具共用的记忆都在那儿。"


def shared_file(project_path):
    """项目根的 AGENTS.md 路径；没有（或是目录）返回 None。"""
    path = os.path.join(project_path, SHARED_NAME)
    return path if os.path.isfile(path) else None


def preamble(project_path):
    """启动注入文本；没有共享记忆返回 None（老项目行为一字不变）。"""
    return _PREAMBLE if shared_file(project_path) else None


def ensure_pointers(project_path):
    """为缺失的指针文件建一句极短指针。已有的一律不覆盖；任何失败都吞掉。"""
    if not shared_file(project_path):
        return
    for name in POINTERS.values():
        path = os.path.join(project_path, name)
        if os.path.exists(path):
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_POINTER_TEXT + "\n")
        except OSError:
            pass
