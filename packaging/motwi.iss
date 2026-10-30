; Motwi's installer. Run through packaging/build.py, which passes /DAppVersion.
; Installs per user, so no admin prompt. The Run value is removed on uninstall either way,
; since Motwi's Settings can create it after install.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif

#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{7E2C7B1E-5A0F-4C57-9C3B-4D4B8E6F2A11}
AppName=Motwi
AppVersion={#AppVersion}
AppPublisher=H4ck3rPr0G4m3r
AppPublisherURL=https://github.com/H4ck3rPr0G4m3r/Motwi
DefaultDirName={autopf}\Motwi
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=Motwi-Setup-{#AppVersion}
SetupIconFile=motwi.ico
UninstallDisplayIcon={app}\Motwi.exe
UninstallDisplayName=Motwi
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "autostart"; Description: "Start Motwi with Windows"; Flags: unchecked
Name: "desktopicon"; Description: "Add a desktop shortcut"; Flags: unchecked

[InstallDelete]
; an upgrade must not leave the old version's libraries next to the new ones
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "out\pyinstaller\Motwi\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Motwi"; Filename: "{app}\Motwi.exe"; AppUserModelID: "H4ck3rPr0G4m3r.Motwi"
Name: "{autodesktop}\Motwi"; Filename: "{app}\Motwi.exe"; AppUserModelID: "H4ck3rPr0G4m3r.Motwi"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "Motwi"; ValueData: """{app}\Motwi.exe"" --background"; Tasks: autostart
Root: HKCU; Subkey: "{#RunKey}"; ValueType: none; ValueName: "Motwi"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Motwi.exe"; Description: "Start Motwi"; Flags: nowait postinstall skipifsilent

[Code]
const
  ClientKey = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  ClientKey32 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed: Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, ClientKey32, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, ClientKey, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, ClientKey, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Exe: String;
begin
  Result := '';
  // ask an installed Motwi to stop, so in-flight messages finish; CloseApplications is the
  // fallback if it's too old or too stuck to answer
  Exe := ExpandConstant('{app}\Motwi.exe');
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not WebView2Installed) and (not WizardSilent) then
    MsgBox('Motwi needs the Microsoft Edge WebView2 Runtime for its window, and it isn''t on this PC.' + #13#10 + #13#10 +
      'Get it from https://developer.microsoft.com/microsoft-edge/webview2/', mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Exe: String;
begin
  if CurUninstallStep <> usUninstall then
    exit;
  Exe := ExpandConstant('{app}\Motwi.exe');
  Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if (not UninstallSilent) and (MsgBox(
      'Also delete your Motwi data?' + #13#10 + #13#10 +
      'This removes your rules, settings and history, and the Twitch sign-ins and AI keys saved in Windows Credential Manager.' + #13#10 + #13#10 +
      'Choose No if you plan to install Motwi again.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    Exec(Exe, '--wipe', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;
