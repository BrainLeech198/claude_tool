@echo off
rem Run straight from source (development). After installing, use the Start Menu
rem shortcut instead - that one goes through the packaged exe and does not need
rem Python on the machine.
rem
rem Uses "python" rather than "pythonw": errors have to be visible on this path.
rem The packaged exe is the windowless one, and it talks to Claude Code's Stop
rem hook over stdin/stdout via the --hook branch.
rem
rem ASCII-only and CRLF on purpose - see build/pack.bat for why.
setlocal
set PYTHONIOENCODING=utf-8
python "%~dp0claude_tool\__main__.py" %*
