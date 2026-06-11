; ============================================================
;  MayaFileManager Installer — Inno Setup 6 スクリプト
;  https://jrsoftware.org/isinfo.php
;
;  ビルド:
;    iscc build_tools\installer.iss
;  または build_exe.bat から自動起動
; ============================================================

#define AppName      "Maya File Manager"
; CI からは iscc /DAppVersion=1.2.3 で上書きされる
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppPublisher "PointLights for entertainment"
#define AppExeName   "MayaFileManager.exe"
#define AppURL       "https://pointlights.jp"

[Setup]
; 相対パス (dist\, resources\) をリポジトリルート基準で解決する
; （本スクリプトは build_tools\ 配下にあるため必須）
SourceDir=..
AppId={{E4A2F3B1-7C9D-4E5F-A8B0-123456789ABC}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\MayaFileManager
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=
OutputDir=dist\installer
OutputBaseFilename=MayaFileManager_v{#AppVersion}_Setup
SetupIconFile=resources\icons\app.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english";  MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";     Description: "デスクトップにショートカットを作成";  GroupDescription: "追加タスク:"; Flags: unchecked
Name: "quicklaunchicon"; Description: "クイック起動バーにアイコンを追加";    GroupDescription: "追加タスク:"; Flags: unchecked; OnlyBelowVersion: 6.1

[Files]
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; onedir mode の場合は以下を有効化して上の行を削除
; Source: "dist\MayaFileManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"
Name: "{group}\{#AppName} をアンインストール"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} を起動"; Flags: nowait postinstall skipifsilent

; 注意: ユーザー設定 (%USERPROFILE%\.maya_file_manager のブックマーク・履歴) は
; アンインストールしても削除しない（再インストール時に引き継ぐため）
