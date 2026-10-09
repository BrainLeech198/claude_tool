"""改完 winhost.spawn_terminal()/spawn_console() 之后，验它们真把开场白整条送出去了。

不真起 claude（会把用户会话接着聊下去），而是把 Popen 拦下来看它收到什么，
再把那条命令行里的 claude 换成 dumper 实跑一遍。
"""
import os
import subprocess
import sys

sys.path.insert(0, r"D:/File/idea/python/claude_tool")
import claude_tool.winhost as W
from claude_tool.handoff import READ_HANDOFF_PROMPT

from _probe_common import sandbox  # noqa: E402
WORK = sandbox("cmdarg")
os.makedirs(WORK, exist_ok=True)
DUMP = WORK + "/dump.py"
OUT = WORK + "/argv.txt"
DUMPPER = sys.executable + " " + DUMP
DUMPPER_LIST = [sys.executable, DUMP]

with open(DUMP, "w", encoding="utf-8") as f:
    f.write("import sys, pathlib\n")
    f.write("pathlib.Path(r'%s').write_text(repr(sys.argv), encoding='utf-8')\n" % OUT)

captured = {}
real_popen = W.subprocess.Popen


def spy(cmd, **kwargs):
    captured["cmd"] = cmd
    return None


def delivery(label, line):
    """把这条命令行里的 exe 换成 dumper 跑一遍，看对面收到什么。

    exe 现在是 agent_exe 找出来的绝对路径（带 .EXE 那种），不能靠 "claude"
    这个名字认——路径里那个 "claude" 会被先换走。按 " --permission-mode" 断开：
    前面那段是 "cmd /c <exe>"（可能还带 title 前缀），换成 dumper。
    """
    if isinstance(line, list):
        # spawn_terminal 现在直接给 argv 列表，不用 cmd /k 了。
        # dumper 换掉前两项（node + codebuddy 脚本 / claude），后面的参数原样。
        try:
            idx = line.index("--permission-mode")
        except ValueError:
            print(label, "-> 找不到 --permission-mode，跳过")
            return
        probe = DUMPPER_LIST + line[idx:]
    else:
        probe = line.replace("conhost.exe cmd /k ", "cmd /c ", 1)
        probe = probe.replace("cmd /k ", "cmd /c ", 1)
        idx = probe.find(" --permission-mode")
        if idx < 0:
            print(label, "-> 找不到 --permission-mode，跳过")
            return
        probe = "cmd /c " + DUMPPER + probe[idx:]
    if os.path.exists(OUT):
        os.remove(OUT)
    subprocess.run(probe, cwd=WORK, timeout=30)
    if not os.path.exists(OUT):
        print(label, "-> 对面什么都没收到")
        return
    with open(OUT, encoding="utf-8") as f:
        got = f.read()
    print(label, "->", got)
    print("   开场白整条到达:", READ_HANDOFF_PROMPT in got)


W.subprocess.Popen = spy
W.spawn_terminal(WORK, cont=True, prompt=READ_HANDOFF_PROMPT, permission="default")
line1 = captured["cmd"]
W.spawn_console(WORK, cont=True, prompt=READ_HANDOFF_PROMPT, permission="default")
line2 = captured["cmd"]
W.subprocess.Popen = real_popen

print("spawn_terminal() 交给 Popen 的:")
print("   ", repr(line1))
print("   类型:", type(line1).__name__)
print()
print("spawn_console() 交给 Popen 的:")
print("   ", repr(line2))
print()
delivery("spawn_terminal 这条实跑", line1)
print()
delivery("spawn_console 这条实跑", line2)
