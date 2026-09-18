@echo off
rem 从源码直接跑（开发用）。装好之后请用开始菜单里那个快捷方式，
rem 那个走打包出来的 exe，不依赖这台机器上有没有 Python。
rem
rem 用 python 而不是 pythonw：这条路上要看得见报错。打包出来的 exe 才是
rem 无控制台的，靠 --handoff-hook 那条分支走标准输入输出。
setlocal
set PYTHONIOENCODING=utf-8
python "%~dp0claude_tool\__main__.py" %*
