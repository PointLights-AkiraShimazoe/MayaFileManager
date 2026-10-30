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
; インストーラの画面の絵もアプリのアイコンから作る（generate_icon.py が出力）。
; 指定しないと Inno Setup の既定の絵になり、新しいアイコンが出ない（v0.9.13）
WizardImageFile=resources\icons\wizard_large_164x314.bmp,resources\icons\wizard_large_246x459.bmp,resources\icons\wizard_large_328x604.bmp
WizardSmallImageFile=resources\icons\wizard_small_55x55.bmp,resources\icons\wizard_small_83x80.bmp,resources\icons\wizard_small_110x106.bmp,resources\icons\wizard_small_138x140.bmp
; 上書きインストールでは EXE の場所が同じなので、Windows が覚えている古い
; アイコンがショートカットやタスクバーに残る。終了時にアイコンの覚えを捨てさせる
ChangesAssociations=yes
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
; Windows のアイコンの覚え（キャッシュ）を作り直させる（上書きインストールで古いアイコンが残る対策）
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden nowait skipifdoesntexist
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} を起動"; Flags: nowait postinstall skipifsilent

; 注意: ユーザー設定 (%USERPROFILE%\.maya_file_manager のブックマーク・履歴) は
; アンインストールしても削除しない（再インストール時に引き継ぐため）
