@echo off
rem One-shot packaging: PyInstaller -> dist\claude_tool\, then Inno -> installer.
rem
rem Build-time deps: PyInstaller, plus Pillow (PyInstaller uses it to embed the
rem icon). Both are tooling only - the packaged app itself stays stdlib-only.
rem
rem Outputs: dist\claude_tool\                      portable, double-clickable
rem          Output\ClaudeLauncher-<ver>-Setup.exe  the one to hand out
rem
rem WHY THIS FILE IS ASCII-ONLY AND CRLF:
rem   cmd.exe parses .bat byte-by-byte in the OEM codepage. Non-ASCII bytes here
rem   get mis-decoded and the parser starts trying to *execute* fragments of the
rem   line ("'xxx' is not recognized as an internal or external command"). And do
rem   NOT add "chcp 65001": switching codepage mid-file makes cmd lose its read
rem   position, which breaks it the same way.
rem   The Chinese notes about packaging live in the design doc instead.
setlocal
for %%i in ("%~dp0..") do set "ROOT=%%~fi"
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

cd /d "%ROOT%" || exit /b 1

echo === 1/4  version ===
rem Generates build\_version.iss, which claude_tool.iss #includes. The version
rem number itself lives in claude_tool\__init__.py (the launcher compares it
rem against the online release list to notice its own updates).
python build\write_version.py || exit /b 1

echo.
echo === 2/4  PyInstaller ===
python -m PyInstaller build\claude_tool.spec --noconfirm --clean --workpath build\_work --distpath dist || exit /b 1

echo.
echo === 3/4  Inno Setup ===
rem No parenthesized if-block here: the ")" inside "Program Files (x86)" would
rem close the block early ("\Inno was unexpected at this time"). Hence goto.
if exist "%ISCC%" goto :have_iscc
echo ISCC.exe not found: %ISCC%
echo PyInstaller step is done - dist\claude_tool\ is runnable as-is.
exit /b 1
:have_iscc
"%ISCC%" build\claude_tool.iss || exit /b 1

echo.
echo === 4/4  docs release list ===
rem Records this version in docs\releases.js so the download page picks it up.
rem The actual upload to Gitee releases is still manual - see that script's header.
python build\update_releases.py || exit /b 1

echo.
echo Done. Output: %ROOT%\Output
