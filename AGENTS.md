# AGENTS.md

给 AI 编码助手的仓库规则。动手前先读这一页；这里的约束优先于临时起意。

## 绝对不要进 git 的东西

- **探针与截图**：`_probe_*.py` / `_probe_*.png` / `_probe_*.js` / `_probe_*.sh`、`_shot*`、`_zoom*`、`_dlg*`。它们只在本机跑，`.gitignore` 已经挡住。
- **打包产物与大文件**：`Output/`、`dist/`、`build/_work/`、`build/_venv/`、`build/_version.iss`、`*.exe`、`*.msi`、`*.zip`、`*.tar.gz` 等。安装包只放 GitHub / Gitee 的 release 附件，不进版本库。
- **临时目录**：`temp/`；AI 工作记忆 `.workbuddy/`（项目数据，别删，也别提交）。
- **不要用 `git add -f` 绕过 `.gitignore`**。确实需要入库的文件，先改 `.gitignore` 并在提交信息里说明理由。

## 提交

- 提交信息用中文，格式 `范围：一句话`（例：`设置：自动查启动器新版加开关`）；正文说清动机和影响。
- **不要任何 AI 署名**：不加 `Co-Authored-By`、`Generated with ...` 或类似尾注；作者和提交者都必须是 `Mr_brainleech <brainleech198@foxmail.com>`。
- 提交前跑 `git status`，确认没有混进探针、截图、安装包。

## 发布

- tag 用纯版本号（`1.0.1`，不带 `v`）。
- GitHub release 正文用英文，Gitee 用中文；两边结构统一为「一句话摘要 + `What's new` / `Fixes`」（中文为「新增 / 修复」）。
- 安装包作为 release 附件上传；附件名必须与 `docs/releases.js` 里的 `file` 完全一致，上传后验证下载直链返回 200。
- `docs/releases.js` 只记录真实存在附件的版本；没有附件的版本不要写进去，否则官网必然 404。
- 发布后手动重建 Pages（`gh api -X POST repos/BrainLeech198/claude_tool/pages/builds`），再确认线上清单已更新。

## 验证与数据

- 改界面、启动、会话、安装这些逻辑后，先在本机跑相关的 `_probe_*.py`，通过再提交。探针不入库，但它们是唯一的回归手段，**别删本地文件**。
- 跑探针用沙箱 `USERPROFILE`，不要碰真实的 `~/.claude_tool`。
- **别弹窗打扰用户**：用户常在全屏游戏。任何会开窗的东西（探针、测试窗、安装器）一律放后台跑（`_probe_run.py` + 后台任务），窗口停屏外并挂 `WS_EX_NOACTIVATE`，绝不抢焦点；结论只在文字里说，不用询问弹窗。（2026-10-30 用户再次强调「不要弹出来影响我」。）
- `temp/pre-*.bundle` 是改写历史前的完整备份，除非确认不再需要，不要删。
