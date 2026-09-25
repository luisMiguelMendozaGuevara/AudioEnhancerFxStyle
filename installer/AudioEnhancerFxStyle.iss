; Inno Setup: empaqueta el build onedir de PyInstaller en un instalador.
;
; Compilar (desde la raiz del repo, con el exe ya construido):
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\AudioEnhancerFxStyle.iss
;
; Requiere: dist\AudioEnhancerFxStyle\  (salida de AudioEnhancerFxStyle.spec).
; El instalador es POR USUARIO (PrivilegesRequired=lowest): no pide UAC y
; encaja con la config en %APPDATA%\AudioEnhancerFxStyle.

; La version se toma de audio_enhancer/constants.py (APP_VERSION) por el
; workflow (ISCC /DAppVersion=...); este default es solo para builds locales.
#ifndef AppVersion
  #define AppVersion "1.5.4"
#endif

#define AppName "Audio Enhancer FxStyle"
#define AppExe "AudioEnhancerFxStyle.exe"
#define AppPublisher "Luis Miguel Mendoza Guevara"
#define AppURL "https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle"

[Setup]
AppId={{B7E4C2A1-9F3D-4E6B-8A21-5C7D9E0F1A2B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\AudioEnhancerFxStyle
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=AudioEnhancerFxStyle-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\AudioEnhancerFxStyle\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
