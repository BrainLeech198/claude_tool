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
    check("三条内置档位",
          set(agents.ids()) == {"claude", "codebuddy", "codebuddy-intl"},
          agents.ids())
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
    check("codebuddy 的 npm 包是腾讯那个",
          codebuddy.npm_package == "@tencent-ai/codebuddy-code",
          codebuddy.npm_package)
    check("claude 有原生脚本 / winget / brew 三样装法",
          claude.native_install.get("win32") == "https://claude.ai/install.ps1"
          and claude.native_install.get("darwin") == "https://claude.ai/install.sh"
          and claude.winget_id == "Anthropic.ClaudeCode"
          and claude.brew_cask == "claude-code",
          (claude.native_install, claude.winget_id, claude.brew_cask))
    check("codebuddy 不借 claude 的原生脚本 / winget / brew",
          not codebuddy.native_install and not codebuddy.winget_id
          and not codebuddy.brew_cask,
          (codebuddy.native_install, codebuddy.winget_id, codebuddy.brew_cask))
    check("codebuddy 的 search_dirs 也认便携 Node 目录",
          os.path.normcase(paths.NODE_DIR) in
          {os.path.normcase(d) for d in codebuddy.search_dirs()},
          codebuddy.search_dirs())
    check("两个档位的权限取值都覆盖 default/acceptEdits/plan/bypassPermissions",
          all({"default", "acceptEdits", "plan", "bypassPermissions"}
              <= set(a.permission_modes) for a in agents.AGENTS.values()))
    check("codebuddy 比 claude 多收一个 dontAsk",
          "dontAsk" in codebuddy.permission_modes
          and "dontAsk" not in claude.permission_modes)
    check("两档的预设目录不重样", claude.preset_dir != codebuddy.preset_dir)

    # ── 3b. codebuddy 国际版那一档 ──
    print("--- 3b. codebuddy-intl 档位 ---")
    intl = agents.CODEBUDDY_INTL
    check("国际档 id / label 分得清国内",
          intl.id == "codebuddy-intl" and intl.label != codebuddy.label,
          intl.label)
    check("国际档的家目录独立于国内档",
          intl.config_dir == os.path.join(os.path.expanduser("~"),
                                          ".codebuddy-intl"),
          intl.config_dir)
    check("国际档的 settings 在它自己家里",
          intl.settings_file == os.path.join(intl.config_dir, "settings.json"))
    check("国际档同样走 CODEBUDDY_CONFIG_DIR",
          intl.config_dir_env == "CODEBUDDY_CONFIG_DIR")
    check("国际档钉死 external 环境",
          intl.launch_env.get("CODEBUDDY_INTERNET_ENVIRONMENT") == "external",
          intl.launch_env)
    check("国际档预设目录又是单独一份",
          len({a.preset_dir for a in agents.AGENTS.values()}) == 3)
    check("国际档的 search_dirs 认的是 WorkBuddyAI 那个安装名",
          all(isinstance(d, str) for d in intl.search_dirs())
          and intl.search_dirs() != codebuddy.search_dirs(),
          intl.search_dirs()[:1])
    check("国际档也把便携 Node 目录垫在后面",
          os.path.normcase(paths.NODE_DIR) in
          {os.path.normcase(d) for d in intl.search_dirs()},
          intl.search_dirs()[-1:])
    env = intl.spawn_environ({"HOME": "/sandbox", "PATH": "/bin"})
    check("spawn_environ 叠上配置家目录和环境",
          env["CODEBUDDY_CONFIG_DIR"] == intl.config_dir
          and env["CODEBUDDY_INTERNET_ENVIRONMENT"] == "external"
          and env["PATH"] == "/bin", env.get("PATH"))
    overrides = intl.launch_overrides({"PATH": "/bin"})
    check("launch_overrides 只给要改的键（给 cmd /k 用）",
          set(overrides) == {"CODEBUDDY_CONFIG_DIR",
                             "CODEBUDDY_INTERNET_ENVIRONMENT"}
          and "HOME" not in overrides, sorted(overrides))
    check("claude 档什么覆盖都没有",
          agents.CLAUDE.launch_overrides({"PATH": "/bin"}) == {})

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

    # ── 6. paths.for_agent + config 工作区 agent 字段 ──
    print("--- 6. paths.for_agent / config.agent ---")
    check("paths.CLAUDE_DIR 仍指向 claude 档位",
          paths.CLAUDE_DIR == claude.config_dir)
    check("for_agent 带出的字段齐",
          all(hasattr(paths.for_agent("claude"), f) for f in
              ("config_dir", "settings_file", "preset_dir", "projects_dir",
               "exe_names")))
    check("for_agent('codebuddy') 家目录是 ~/.codebuddy",
          paths.for_agent("codebuddy").config_dir.endswith(".codebuddy"),
          paths.for_agent("codebuddy").config_dir)
    check("for_agent 认不出来回落 claude",
          paths.for_agent("nope").config_dir == claude.config_dir)

    from claude_tool.config import default_config
    check("默认工作区带 agent 字段",
          default_config()["workspaces"][0].get("agent") == "claude")

    print()
    print("结果:", "全过" if not BAD else "没过：" + str(BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
