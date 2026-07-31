#ifndef AppVersion
  #error AppVersion must be supplied by build_windows.ps1
#endif

#ifndef SourceDir
  #error SourceDir must be supplied by build_windows.ps1
#endif

#ifndef OutputDir
  #error OutputDir must be supplied by build_windows.ps1
#endif

#ifndef SetupIcon
  #error SetupIcon must be supplied by build_windows.ps1
#endif

[Setup]
AppId={{A78FDC60-4496-4DDE-B893-26BA881A0402}
AppName=Econ Paper Analyzer
AppVersion={#AppVersion}
AppPublisher=Econ Paper Analyzer
DefaultDirName={localappdata}\Programs\Econ Paper Analyzer
DefaultGroupName=Econ Paper Analyzer
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=econ-paper-analyzer-windows-x64-v{#AppVersion}-setup
SetupIconFile={#SetupIcon}
UninstallDisplayIcon={app}\econ-paper-analyzer.exe
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Econ Paper Analyzer"; Filename: "{app}\econ-paper-analyzer.exe"
Name: "{autodesktop}\Econ Paper Analyzer"; Filename: "{app}\econ-paper-analyzer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\econ-paper-analyzer.exe"; Description: "Launch Econ Paper Analyzer"; Flags: nowait postinstall skipifsilent
