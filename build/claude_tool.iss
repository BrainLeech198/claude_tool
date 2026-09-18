; Claude 启动器的安装脚本（Inno Setup 6.3 以上）
;
; 这个文件是 UTF-8 **带 BOM** 的。Inno 只有在开头有 BOM 时才按 UTF-8 读 .iss，
; 否则按系统 ANSI（这台机器是 cp936）解，里面的中文会变乱码。改这个文件时别把
; BOM 弄丢——Inno 自带的 IDE 和 VS Code 都会保留它。
;
; 编译要分两步，先 PyInstaller 再 ISCC，合起来是 build/打包.bat：
;   python -m PyInstaller build/claude_tool.spec --noconfirm --clean ^
;       --workpath build/_work --distpath dist
;   ISCC build/claude_tool.iss

#define AppName "Claude 启动器"
#define AppExe "claude_tool.exe"
#define AppVersion "0.1.0"

[Setup]
; AppId 是唯一标识，升级安装靠它认出"这还是同一个软件"。**定下来就别改**——
; 改了之后装新版会变成并存两份，而不是覆盖升级。
AppId={{9C4A7E12-5D38-4F6B-A2C1-7E90DB31F5A8}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=claude_tool
; 安装目录用 ASCII（claude_tool），显示名才用中文。因为 hook 命令行里要塞这个
; 路径，全程 ASCII 能少一整类编码麻烦；而开始菜单和快捷方式上的中文只是显示，
; 不参与任何命令行拼接。
DefaultDirName={autopf}\claude_tool
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\Output
OutputBaseFilename=ClaudeLauncher-{#AppVersion}-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 默认免管理员，装到用户目录（%LOCALAPPDATA%\Programs）；想装成整机共用的可以用
; /ALLUSERS 走命令行，或者在界面上切。{autopf} 会跟着这个选择变。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\claude_tool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// 注意：[Code] 段里 ';' 不是注释（那是 [Setup]/[Icons] 那些段的注释符），
// Pascal 只认 '//' 和 '{ }'。写成 ';' 会报 "'BEGIN' expected"。
//
// 卸载**不删** ~/.claude_tool/——那里面是用户全部的模型预设（含 API key）和工作区
// 列表，不是我们安装进去的东西，Inno 默认也不会去碰。就留一句提示告诉他东西还在，
// 免得他以为卸载等于清空。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // UninstallSilent 那句是必要的：这个 MsgBox 是脚本自己弹的，/SILENT 和
  // /SUPPRESSMSGBOXES 都拦不住它，静默卸载会卡在这儿等一个没人看的框。
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
    MsgBox('启动器自己的数据——模型预设（含 API key）和工作区列表——都还留在'
           + #13#10 + '{%USERPROFILE}\.claude_tool 下面，卸载程序没有动它们。'
           + #13#10 + #13#10
           + '要彻底清干净的话，卸载完手动把那个文件夹删掉即可。',
           mbInformation, MB_OK);
end;
