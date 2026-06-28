; AtlasToolkit — Inno Setup script (per-user install, no admin required).
; Version is injected by CI:  ISCC.exe /DMyAppVersion=1.2.3 AtlasToolkit.iss
; Packages the Nuitka standalone output in dist\main.dist into a single Setup.exe.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "AtlasToolkit"
#define MyAppExeName "AtlasToolkit.exe"
#define MyAppProgId "AtlasToolkit.atlas"
#define MyAppMutex "AtlasToolkitSingleInstanceMutex"
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
AllowNoIcons=yes
; Force the fixed install location that _is_installed_build() / silent self-update
; assume — without this the user could install elsewhere and never get auto-updates.
DisableDirPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=AtlasToolkit-Setup-x64
SetupIconFile=ui\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Detect a running instance (app holds this named mutex) and block a second
; installer from running concurrently.
AppMutex={#MyAppMutex}
SetupMutex=AtlasToolkitSetupMutex
; CloseApplications=yes lets silent self-update shut down the running app via
; Restart Manager. RestartApplications=no keeps interactive installs on [Run];
; self-update relaunch (with preserved argv) is handled by the update runner script.
CloseApplications=yes
RestartApplications=no
; Notify the shell when the .atlas association changes (refreshes icons / Open With).
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "associate"; Description: "Associate .atlas files with {#MyAppName}"; GroupDescription: "File associations:"

[Files]
Source: "dist\main.dist\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Registry]
; Per-user .atlas file association (HKA -> HKCU under PrivilegesRequired=lowest).
; Only written when the "associate" task is selected; cleaned up on uninstall.
Root: HKA; Subkey: "Software\Classes\.atlas\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppProgId}"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.atlas"; ValueType: string; ValueName: ""; ValueData: "{#MyAppProgId}"; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyAppProgId}"; ValueType: string; ValueName: ""; ValueData: "Spine Atlas File"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyAppProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyAppProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
; Interactive install only — silent self-update relaunch is handled by the update runner script.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; On uninstall, drop the update cache (downloaded installers / logs / scripts);
; user config (config.json) in the same data dir is intentionally left intact.
Filename: "{cmd}"; Parameters: "/c rmdir /s /q ""{localappdata}\{#MyAppName}\update"""; Flags: runhidden; RunOnceId: "DelUpdateCache"

[Code]
{ Clean upgrade without blunt deletion: run the PREVIOUS version's uninstaller
  before installing the new files. It removes only what the old version installed
  (per its uninstall log) — so stale files from a dropped dependency are cleaned —
  while leaving app-created data (config.json, update cache) untouched. }

function GetUninstallString(): String;
var
  Key: String;
  S: String;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{AADE8604-EC9B-491C-92FA-D0628C934556}_is1';
  S := '';
  if not RegQueryStringValue(HKCU, Key, 'UninstallString', S) then
    RegQueryStringValue(HKLM, Key, 'UninstallString', S);
  Result := S;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  UnInstStr, OldExe: String;
  ResultCode, Waited: Integer;
begin
  if CurStep <> ssInstall then
    Exit;
  UnInstStr := GetUninstallString();
  if UnInstStr = '' then
    Exit;
  UnInstStr := RemoveQuotes(UnInstStr);
  OldExe := ExtractFilePath(UnInstStr) + '{#MyAppExeName}';
  if Exec(UnInstStr, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    { The uninstaller relaunches a temp copy and returns early; wait until the old
      exe is actually gone so it can't delete our freshly-copied files (cap ~20s). }
    Waited := 0;
    while FileExists(OldExe) and (Waited < 100) do
    begin
      Sleep(200);
      Waited := Waited + 1;
    end;
  end;
end;
