"""记忆互通：AGENTS.md 共享记忆 + 启动注入 + 静态互链。

沙箱 USERPROFILE，绝不碰真目录。

    python _probe_shared_memory.py
"""
import os
import shutil
import sys

from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("shared_memory")

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool import shared_memory  # noqa: E402

BAD = []


def check(label, passed, detail=""):
    if not passed:
        BAD.append(label)
    print("{} {} {}".format("ok  " if passed else "FAIL", label, detail))


def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(PROFILE, exist_ok=True)

    # ── 1. 没有 AGENTS.md → preamble 是 None ──
    print("--- 1. 没有共享记忆 ---")
    proj = os.path.join(PROFILE, "old_project")
    os.makedirs(proj, exist_ok=True)
    check("没有 AGENTS.md 时 shared_file 是 None",
          shared_memory.shared_file(proj) is None)
    check("没有 AGENTS.md 时 preamble 是 None",
          shared_memory.preamble(proj) is None)

    # ── 2. 有 AGENTS.md → preamble 非空，且不含双引号 ──
    print("--- 2. 有共享记忆 ---")
    proj2 = os.path.join(PROFILE, "new_project")
    os.makedirs(proj2, exist_ok=True)
    with open(os.path.join(proj2, "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write("# 项目约定\n")
    check("有 AGENTS.md 时 shared_file 返回路径",
          shared_memory.shared_file(proj2) == os.path.join(proj2, "AGENTS.md"))
    text = shared_memory.preamble(proj2)
    check("preamble 非空", bool(text), repr(text))
    check("preamble 不含双引号", '"' not in (text or ""), repr(text))
    check("preamble 里提到 AGENTS.md", "AGENTS.md" in (text or ""))
    check("preamble 说别写进专属记忆",
          "专属记忆" in (text or "") or "不要写进" in (text or ""))

    # ── 3. ensure_pointers 建互链 ──
    print("--- 3. ensure_pointers ---")
    shared_memory.ensure_pointers(proj2)
    for name in ("CLAUDE.md", "CODEBUDDY.md"):
        path = os.path.join(proj2, name)
        check("{} 被建出来".format(name), os.path.isfile(path))
        if os.path.isfile(path):
            content = open(path, encoding="utf-8").read()
            check("{} 内容指向 AGENTS.md".format(name),
                  "AGENTS.md" in content, repr(content[:40]))

    # ── 4. 已有的 CLAUDE.md 不覆盖 ──
    print("--- 4. 不覆盖已有文件 ---")
    proj3 = os.path.join(PROFILE, "has_claude_md")
    os.makedirs(proj3, exist_ok=True)
    with open(os.path.join(proj3, "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write("共享\n")
    with open(os.path.join(proj3, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write("我自己写的内容\n")
    shared_memory.ensure_pointers(proj3)
    content = open(os.path.join(proj3, "CLAUDE.md"), encoding="utf-8").read()
    check("已有 CLAUDE.md 不被覆盖", "我自己写的内容" in content, repr(content))

    # ── 5. AGENTS.md 是目录 → shared_file 返回 None ──
    print("--- 5. AGENTS.md 是目录 ---")
    proj4 = os.path.join(PROFILE, "dir_not_file")
    os.makedirs(os.path.join(proj4, "AGENTS.md"), exist_ok=True)
    check("AGENTS.md 是目录时 shared_file 返回 None",
          shared_memory.shared_file(proj4) is None)
    check("AGENTS.md 是目录时 preamble 返回 None",
          shared_memory.preamble(proj4) is None)

    print()
    print("结果:", "全过" if not BAD else "没过：" + str(BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
