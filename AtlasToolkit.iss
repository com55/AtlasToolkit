; AtlasToolkit — Inno Setup script (per-user install, no admin required).
; Version is injected by CI:  ISCC.exe /DMyAppVersion=1.2.3 AtlasToolkit.iss
; Packages the Nuitka standalone output in dist\main.dist into a single Setup.exe.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "AtlasToolkit"
#define MyAppExeName "AtlasToolkit.exe"
#define MyAppPublisher "com55"
#define MyAppURL "https://github.com/com55/AtlasToolkit"

[Setup]
; AppId MUST stay constant across releases so upgrades replace in place (never change it).
AppId={{AADE8604-EC9B-491C-92FA-D0628C934556}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
; Per-user install — no admin / no UAC, so silent self-update works unattended.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=installer
OutputBaseFilename=AtlasToolkit-Setup-x64
SetupIconFile=ui\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; During a silent self-update, close the running app via Restart Manager; the
; updater cmd handles relaunch, so don't let Inno restart it (avoids double-launch).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\main.dist\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Interactive install only — the silent self-update relaunch is owned by the updater cmd.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
