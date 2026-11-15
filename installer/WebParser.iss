#ifndef BuildVersion
  #define BuildVersion "0.0.0"
#endif
#ifndef BuildDir
  #define BuildDir "..\dist\WebParser"
#endif

[Setup]
AppId=WebParser.SidSpears69
AppName=Web Parser
AppVersion={#BuildVersion}
AppPublisher=Web Parser
DefaultDirName={localappdata}\Programs\WebParser
DefaultGroupName=Web Parser
OutputDir=..\dist\installer
OutputBaseFilename=WebParserSetup-{#BuildVersion}
UninstallDisplayIcon={app}\WebParser.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0.17763
CloseApplications=yes
RestartApplications=no
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "{#BuildDir}\WebParser.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildDir}\version.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildDir}\ms-playwright\*"; DestDir: "{app}\ms-playwright"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Web Parser"; Filename: "{app}\WebParser.exe"
Name: "{autodesktop}\Web Parser"; Filename: "{app}\WebParser.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Run]
Filename: "{app}\WebParser.exe"; Description: "Запустить Web Parser"; Flags: nowait postinstall skipifsilent
