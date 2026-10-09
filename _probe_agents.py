"""agent 档位表：claude / codebuddy 两条各自的 exe 名、家目录、权限档，以及找法。

纯逻辑，不建窗口、不落用户目录。沙箱 HOME 是防万一——agents 里那几条路径是
从 paths 的常量派出来的，跟别处一样得赶在 import claude_tool 之前把 HOME 指到
沙箱，不然冻在真实家目录上（见 _probe_run.py 文件头那段）。

    python _probe_agents.py
"""
import os
import shutil
import sys

from _probe_common import sandbox  # noqa: E402
PROFILE = sandbox("agents")

os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from claude_tool import agents, paths, permissions    # noqa: E402

BAD = []


def check(label, passed, detail=""):
    if not passed:
        BAD.append(label)
    print("{} {} {}".format("ok  " if passed else "FAIL", label, detail))


def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    os.makedirs(PROFILE, exist_ok=True)

    claude = agents.get("claude")
    codebuddy = agents.get("codebuddy")

    # ── 1. 表本身 ──
    print("--- 1. 档位表 ---")
    check("默认档位是 claude", agents.DEFAULT_AGENT == "claude")
    check("两条内置档位", set(agents.ids()) == {"claude", "codebuddy"}, agents.ids())
    check("认不出来的 id 回落 claude", agents.get("nope").id == "claude")
    check("None / 空串也回落 claude",
          agents.get(None).id == "claude" and agents.get("").id == "claude")
    check("labels 跟档位对得上",
          agents.labels() == {i: agents.get(i).label for i in agents.ids()},
          agents.labels())

    # ── 2. claude 那一档必须逐字等于 paths 现在的值（paths 之后由它转发）──
    print("--- 2. claude 沿用 paths 现在那几个常量 ---")
    check("config_dir 就是 paths.CLAUDE_DIR", claude.config_dir == paths.CLAUDE_DIR)
    check("settings_file 就是 paths.SETTINGS_FILE",
          claude.settings_file == paths.SETTINGS_FILE)
    check("preset_dir 就是 paths.PRESET_DIR",
          claude.preset_dir == paths.PRESET_DIR)
    check("projects_dir 落在 config_dir 底下",
          claude.projects_dir == os.path.join(paths.CLAUDE_DIR, "projects"))
    check("claude 的 exe 名", claude.exe_names[0] == "claude")
    check("claude 没有家目录覆盖变量", claude.config_dir_env is None)
    check("claude 不用 node", claude.needs_node is False)
    check("claude 的权限取值就是 permissions.PERMISSION_VALUES",
          claude.permission_modes == tuple(permissions.PERMISSION_VALUES))

    # ── 3. codebuddy 那一档 ──
    print("--- 3. codebuddy 档位 ---")
    check("codebuddy 的 exe 名", codebuddy.exe_names[0] == "codebuddy")
    check("codebuddy 指向 CODEBUDDY_CONFIG_DIR",
          codebuddy.config_dir_env == "CODEBUDDY_CONFIG_DIR")
    check("codebuddy 的家目录是 ~/.codebuddy（CLI 自己的默认，不是桌面端的 .workbuddy）",
          codebuddy.config_dir == os.path.join(os.path.expanduser("~"), ".codebuddy"),
          codebuddy.config_dir)
    check("codebuddy 的 settings 在它自己家里",
          codebuddy.settings_file == os.path.join(codebuddy.config_dir,
                                                  "settings.json"))
    check("codebuddy 要 node（没 shim 的 node 脚本）", codebuddy.needs_node is True)
    check("codebuddy 没有 npm 包（不提供一键装）", codebuddy.npm_package is None)
    check("两个档位的权限取值都覆盖 default/acceptEdits/plan/bypassPermissions",
          all({"default", "acceptEdits", "plan", "bypassPermissions"}
              <= set(a.permission_modes) for a in agents.AGENTS.values()))
    check("codebuddy 比 claude 多收一个 dontAsk",
          "dontAsk" in codebuddy.permission_modes
          and "dontAsk" not in claude.permission_modes)
    check("两档的预设目录不重样", claude.preset_dir != codebuddy.preset_dir)

    # ── 4. 找法 ──
    print("--- 4. find() ---")
    nothing = lambda name: None          # noqa: E731

    # PATH 里就有
    got = claude.find(which=lambda n: "C:/x/claude" if n == "claude" else None)
    check("PATH 里有就直接用", got == ["C:/x/claude"], got)

    # PATH 里没有，但某个目录里躺着（_probe_install 撞的那种）
    dir_a = os.path.join(PROFILE, "a")
    os.makedirs(dir_a, exist_ok=True)
    fake_claude = os.path.join(dir_a, "claude.exe")
    with open(fake_claude, "wb") as f:
        f.write(b"")
    got = claude.find(which=nothing, extra=[dir_a])
    check("PATH 没有时去 extra 目录里捞", got == [fake_claude], got)

    # codebuddy：脚本没扩展名 → 得凑上 node
    dir_b = os.path.join(PROFILE, "b")
    os.makedirs(dir_b, exist_ok=True)
    cb_script = os.path.join(dir_b, "codebuddy")
    node_exe = os.path.join(dir_b, "node.exe")
    for p in (cb_script, node_exe):
        with open(p, "wb") as f:
            f.write(b"")
    which_node = lambda n: node_exe if n == "node" else None      # noqa: E731
    got = codebuddy.find(which=which_node, extra=[dir_b])
    check("codebuddy：凑成 [node, 脚本] 两个 token", got == [node_exe, cb_script],
          got)

    # node 不在 → 不算找到（不能拿半个命令去开窗）
    got = codebuddy.find(which=nothing, extra=[dir_b])
    check("codebuddy：没 node 就不算找到", got is None, got)

    # 有 .cmd shim 就不用塞 node
    dir_c = os.path.join(PROFILE, "c")
    os.makedirs(dir_c, exist_ok=True)
    cb_shim = os.path.join(dir_c, "codebuddy.cmd")
    with open(cb_shim, "wb") as f:
        f.write(b"")
    got = codebuddy.find(which=nothing, extra=[dir_c])
    check("codebuddy：有 .cmd shim 就直接用它，不塞 node", got == [cb_shim], got)

    # exe_names_for
    check("exe_names_for 把几个名字都拼上",
          codebuddy.exe_names_for("D:/d") == tuple(
              os.path.join("D:/d", n) for n in codebuddy.exe_names),
          codebuddy.exe_names_for("D:/d"))

    # ── 5. 版本参数 ──
    print("--- 5. version_argv ---")
    check("claude 问版本是 --version", claude.version_argv() == ["--version"])
    check("codebuddy 问版本也是 --version",
          codebuddy.version_argv() == ["--version"])

    print()
    print("结果:", "全过" if not BAD else "没过：" + str(BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
