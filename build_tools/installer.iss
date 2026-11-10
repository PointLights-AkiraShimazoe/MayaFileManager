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
; 起動中の Manager は «こちらで» 閉じてから入れる（v0.9.19、ユーザー報告 2026-11-10）。
;   「インストールしようとしたらフリーズしました。アプリケーションを自動で落とすとして
;     進めるとフリーズしました。落としたら大丈夫でした。」
; Inno Setup の既定（CloseApplications=yes）は Windows の Restart Manager に閉じさせるが、
; Manager（Qt・コンパクト中は本体を画面外へ退けている）がその要求に応えきれず、
; インストーラが待ち続けて固まった。Restart Manager は使わず、[Code] の
; PrepareToInstall で «普通に閉じる → 待つ → 終わらなければ強制終了 → それでも残れば
; 先へ進ませない» を行う。
CloseApplications=no
RestartApplications=no

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

[Code]
// 起動中の Maya File Manager を閉じる（v0.9.19）。上の CloseApplications=no を参照。
function MfmIsRunning(): Boolean;
var
  TmpFile: String;
  Output: AnsiString;
  ResultCode: Integer;
begin
  Result := False;
  TmpFile := ExpandConstant('{tmp}\mfm_tasklist.txt');
  // cmd は引用符が 3 つ以上あると外側を剥がすので /S /C "..." の形で渡す
  if Exec(ExpandConstant('{cmd}'), '/S /C "tasklist /FI "IMAGENAME eq {#AppExeName}" /NH > "' + TmpFile + '""',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if LoadStringFromFile(TmpFile, Output) then
      Result := Pos(Lowercase('{#AppExeName}'), Lowercase(String(Output))) > 0;
  end;
end;

function MfmWaitGone(Seconds: Integer): Boolean;
var
  I: Integer;
begin
  Result := not MfmIsRunning();
  I := 0;
  while (not Result) and (I < Seconds * 2) do
  begin
    Sleep(500);
    I := I + 1;
    Result := not MfmIsRunning();
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if not MfmIsRunning() then
    Exit;
  // 1) 普通に閉じる（ウィンドウを閉じる要求。設定はここで保存される）
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#AppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if MfmWaitGone(8) then
    Exit;
  // 2) 応答が無ければ強制終了（子プロセスごと）
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if MfmWaitGone(5) then
    Exit;
  // 3) それでも残っていれば先へ進ませない（固まったまま待たせない）
  if ActiveLanguage = 'japanese' then
    Result := 'Maya File Manager を終了できませんでした。タスクマネージャーで MayaFileManager.exe を終了してから、もう一度インストールしてください。'
  else
    Result := 'Could not close Maya File Manager. End MayaFileManager.exe in Task Manager, then run the installer again.';
end;
