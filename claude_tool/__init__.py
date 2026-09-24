"""Claude Code 启动器。

把一个单文件脚本拆成包：各模块只依赖它下面那几层，反过来不依赖。
拆法见仓库根的 设计说明.md。
"""

# 启动器自己的版本号，**唯一一处写死的地方**。改版本改这一行。
#
# 挪到这儿是因为"感知自己有没有新版"要拿它跟官网上那份清单比，而这份号以前只
# 写在 build/claude_tool.iss 里给 Inno 用，Python 这边看不见。现在两头都来这儿
# 读：打包那两步看 build/write_version.py（它把这个号生成成 build/_version.iss，
# Inno 的 #include 吃那个），build/update_releases.py 直接 import 这个模块。
__version__ = "0.4.0"
