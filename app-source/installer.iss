#define AppName "Text2Speech"
#define AppVersion "1.1.0"
#define AppPublisher "Text2Speech"
#define AppExeName "KokoroVoiceoverStudio.exe"
#ifdef OfflineDistribution
#define InstallerOutputDir "dist\offline"
#define InstallerOutputName "KokoroVoiceoverStudio-Offline-1.1.0"
#define InstallerCompression "lzma2/fast"
#define InstallerDiskSpanning "yes"
#define InstallerDiskSliceSize "2000000000"
#else
#define InstallerOutputDir "dist\installer"
#define InstallerOutputName "Text2Speech-Setup-1.1.0"
#define InstallerCompression "lzma2/ultra64"
#define InstallerDiskSpanning "no"
#endif

[Setup]
AppId={{5399139D-3C2B-40FE-8C5D-2AA07DC83544}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Kokoro Voiceover Studio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
OutputDir={#InstallerOutputDir}
OutputBaseFilename={#InstallerOutputName}
Compression={#InstallerCompression}
DiskSpanning={#InstallerDiskSpanning}
#ifdef OfflineDistribution
DiskSliceSize={#InstallerDiskSliceSize}
#endif
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\KokoroVoiceoverStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef OfflineDistribution
Source: "release-assets\models\Qwen3.5-9B-Q4_K_M.gguf"; DestDir: "{localappdata}\Kokoro Voiceover Studio\models"; DestName: "Qwen3.5-9B-Q4_K_M.gguf"; Flags: ignoreversion nocompression uninsneveruninstall
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Check: not WizardSilent; Flags: nowait postinstall
