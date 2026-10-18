; Installateur de Rangeo (Inno Setup 6) — construit par installer.bat après build.bat.
; Installation pour l'utilisateur courant (pas besoin des droits administrateur), dans le même
; dossier que l'installation intégrée à Rangeo : %LOCALAPPDATA%\Programs\Rangeo.

#define AppName "Rangeo"
#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif

[Setup]
AppId={{6B0E4C7A-3F2D-4B8E-9C61-5A7D2E1F0B93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Rangeo
DefaultDirName={localappdata}\Programs\Rangeo
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Rangeo-{#AppVersion}-Installation
SetupIconFile=..\rangeo\resources\rangeo.ico
UninstallDisplayIcon={app}\Rangeo.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.DesktopIcon=Créer un raccourci sur le Bureau
en.DesktopIcon=Create a desktop shortcut
fr.LaunchApp=Lancer Rangeo
en.LaunchApp=Launch Rangeo

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"

[Files]
Source: "..\dist\Rangeo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Rangeo"; Filename: "{app}\Rangeo.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Rangeo"; Filename: "{app}\Rangeo.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Rangeo.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; rangements planifiés créés par Rangeo dans le Planificateur de tâches (dossier \Rangeo\)
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-ScheduledTask -TaskPath '\Rangeo\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "RangeoTasks"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  { une version installée par le bouton « Installer » de Rangeo a sa propre entrée : une seule suffit }
  if CurStep = ssPostInstall then
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Rangeo');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { menu clic droit de l'Explorateur, démarrage avec Windows, ancienne entrée « Installer » de l'appli }
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\Directory\shell\Rangeo');
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\Drive\shell\Rangeo');
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\Directory\Background\shell\Rangeo');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Rangeo');
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Rangeo');
  end;
end;
