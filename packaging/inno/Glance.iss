#ifndef AppDist
  #error AppDist must point to the standalone Nuitka distribution directory.
#endif
#ifndef OutputDir
  #define OutputDir "..\..\build\package"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.2"
#endif

[Setup]
AppId={{B37C2AF8-93BC-4E1A-BA1A-000000000001}
AppName=Glance
AppVersion={#AppVersion}
AppPublisher=Glance contributors
DefaultDirName={autopf}\Glance
MinVersion=10.0.22000
DefaultGroupName=Glance
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir={#OutputDir}
OutputBaseFilename=GlanceSetup.tmp
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Glance.exe
SetupIconFile={#AppDist}\Glance.ico
CloseApplications=yes
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
SetupLogging=yes
InfoBeforeFile=..\installer-info.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#AppDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Glance"; Filename: "{app}\Glance.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Glance"; Filename: "{app}\Glance.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Glance.exe"; Description: "Launch Glance"; Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\provider\GlanceAdmin.ps1"" -Operation disable-provider -TargetSid ""S-1-0-0"""; Flags: runhidden waituntilterminated; RunOnceId: "DisableGlanceProvider"
