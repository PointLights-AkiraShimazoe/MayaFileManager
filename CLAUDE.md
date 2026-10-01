# MayaFileManager — Claude セッション用メモ

Maya用ファイルマネージャ（PySide6/PySide2両対応、QColumnViewベースのカラムブラウザ）。
所有者: 島添 聡（PointLights for entertainment）。出力・ログ・コミットは日本語で。

## GitHub（重要: 確認方法）

- リポジトリ: https://github.com/PointLights-AkiraShimazoe/MayaFileManager （プライベート）
- **web_fetch / api.github.com では見えない（認証なし→空応答）。必ず Claude in Chrome
  （ユーザーのログイン済みブラウザ）で `navigate` → `get_page_text` を使うこと。**
- リリースフロー: `v*.*.*` タグを push → GitHub Actions「Build MayaFileManager.exe」が
  自動ビルドし Release に Setup.exe / _windows.zip を添付（所要 約2〜3分）。
- 確認先: /actions（ビルド状況）、/releases（成果物）。

## EXEビルドの注意（重要）

- **run_dev は Maya の Qt（6.8系）、EXE は pip の PySide6 を同梱**する。両者で
  QFileSystemModel の挙動（特にシンボリックリンク/ジャンクション解決）が異なる。
- build.yml では **PySide6==6.8.3 に固定**している。未固定にすると 6.11 系が入り、
  リンクのブラウジングが EXE 版でのみ壊れる（2026-07 に実際に発生）。
- 「run_dev では動くが EXE で動かない」報告が来たら、まず Qt バージョン差を疑う。
- **directoryLoaded の罠**: pip版Qt の QFileSystemModel は «既にロード済みの
  ディレクトリ» に対して directoryLoaded を再発火しない（Maya の Qt は発火する）。
  ナビ完了をシグナル待ちにすると EXE 版だけ「クリックしても何も起きない」になる。
  対策として _maybe_finalize_navigation（シグナル非依存の遅延完了判定）を実装済み。
  ナビ系の新規実装でも directoryLoaded 依存の完了待ちを作らないこと。

## i18n（UI二言語化）

- `core/i18n.py` の `tr("日本語", "English")` を使う（カタログ無しの併記方式）。
- 言語は設定キー `ui_language`（auto=Maya言語追従 / ja / en）。起動時に
  `i18n.init(sm)` で確定、切替は**再起動で反映**。ログ・コミットは日本語のまま。
- クリック動作から「プレビュー」は廃止（「開く」と同一動作だったため統合。
  旧設定 preview は open へ自動移行）。「none（何もしない）」を追加。

## Maya連携（スタンドアロン）

- 「起動」ボタンで起動した Maya は commandPort(python) を開く。ポートは
  レンジ 20261〜20269 から find_free_port() で空きを割り当て（複数Maya対応）。
  右クリックの開く/インポート/リファレンスはソケット経由で送信。
- 接続先の選択: メニューバー右上の「接続:」コンボ＋⟳ボタン。scan_open_ports()
  でレンジを走査し、各ポートへ IDENTIFY_CODE を照会して
  「Maya 2026 — scene.ma (:20261)」形式で列挙。スキャン・照会も必ずワーカー
  スレッド（_refresh_maya_connections → _BridgeNotifier.conn_list Signal）。
- **commandPort に -echoOutput を付けない**。付けるとスクリプトエディタの全出力
  （selectKey のMELダンプ等）がソケットへ垂れ流され、応答が汚染される
  （2026-09 実機で発生: 接続コンボに数千文字のゴミラベル → コーナーウィジェットの
  幅が暴発しレイアウト崩壊 → ブラウザが断続的に表示されない、まで連鎖した）。
- **commandPort(python) は «単一の式» の評価結果しか返さない**。import文を含む
  コードを送ると応答が None になり識別に失敗する（「Maya?」表示の原因になった）。
  IDENTIFY_CODE は __import__ を使った単一式を維持すること。
- 識別応答は必ずセンチネル（MFMID<ver><scene><pid>MFMID）を parse_identify() で
  厳格に抽出する。生応答をUIラベルへ直接使うのは禁止。コンボは maximumWidth で
  幅を固定（想定外の長文でもレイアウトを壊さない）。
- 「🗖」ボタン＝接続中Mayaを最前面へ（識別で得たPID → EnumWindows →
  SetForegroundWindow。Windows専用）。
- ツールメニュー「Maya連携を全Mayaにインストール...」= <Maya app dir>/scripts/
  userSetup.py へ commandPort 自動オープンのスニペットを書き込む
  （install_usersetup、マーカーで冪等）。ショートカット起動のMayaも接続可能になる。
  * app dir は maya_app_dir(): MAYA_APP_DIR → Windows「ドキュメント」既知フォルダ
    （SHGetFolderPathW CSIDL_PERSONAL、OneDriveリダイレクトを反映）/maya →
    ~/Documents/maya。~/Documents 固定は禁止（OneDrive環境でMayaが読まない場所へ
    書き、「起動以外のMayaが接続リストに出ない」原因になった。r44）。
  * 「起動」ボタン以外の既存Mayaは、commandPort が開いていなければ原理的に
    検出不能。インストール後に Maya を起動し直す必要がある。
- **D&D動作**（設定キー dnd_action、既定 import）: ブラウザからMayaのウィンドウへ
  D&Dすると設定アクションを実行。実装＝«選択済み項目» のプレスを消費し、
  リリース＝クリック再現／Move閾値＝_start_multi_drag（単一・複数とも同一経路、
  _pending_multi_drag の len>=1）。未選択項目のドラッグはネイティブに任せる。
  ドロップ後にカーソル直下プロセスが maya.exe か判定（_cursor_over_maya_window）
  → BrowserPanel._handle_maya_drop → MainWindow._on_maya_drop。
  D&Dのリファレンスはダイアログ無し（デフォルトNamespace使用）。
  * **禁止**: プレスを素通しして MouseMove だけを消費して QDrag.exec に入る方式
    （r21〜r24）。リリースがドラッグループに食われてビューが押下状態のまま残り、
    以後のクリックが効かない＝「かなりの頻度でフリーズ」の原因になった（2026-09）。
- フリーズ調査: _install_freeze_watchdog（常時有効）がUIスレッド1.5秒停止で
  メインスレッドのスタックをツールフォルダ直下の mfm_freeze.log に記録する
  （MFM_DEBUG時は mfm_debug.log にも）。フリーズ報告時はまずこれを見る。

## Blender連携（r65、Maya と対称）

- 構成: core/blender_version.py（検出: Program Files\Blender Foundation\Blender X.Y、
  Steam、環境変数 MFM_BLENDER_EXE ／ 起動: `blender --python resources/
  blender_bridge.py -- --mfm-port N`）、core/blender_bridge.py（マネージャー側。
  クライアントは MayaBridge と同一プロトコル、ポートレンジ **20271〜20279**）、
  resources/blender_bridge.py（Blender 内: TCP で1行 Python を受け、
  bpy.app.timers のメインスレッドで eval→exec、応答を返す。startup へコピー
  すると自動起動＝Maya の userSetup 相当。ツールメニューからインストール）。
- 操作の対応: 開く=wm.open_mainfile（未保存なら Blender 側 popup で確認）／
  インポート=Append（.blend は全オブジェクトをシーンへ複製、fbx/obj/abc/usd/
  glTF/stl/ply は各 import オペレータ）／リファレンス=**Link**（.blend のみ。
  他形式はインポートへ退避）。エラーは Blender 側 popup ＋ 応答 "Error:" を
  マネージャーがダイアログ表示。
- **ヘッダの配置（r67、ユーザーのスケッチ準拠）**: ui/dcc_header.py。
  メニューバーを含む «ヘッダ行» を setMenuWidget で置き換え、中央に DCC ブロック
  2段（上: [Mayaバージョン][M][スイッチ][b][Blenderバージョン] / 下:
  [接続中Maya][⟳][起動][🗖][接続中Blender]）、右端にクリック動作/D&D動作
  （アイコン＋コンボ2段）。**QMainWindow.menuBar() を一切使わない**（r68）:
  setMenuBar/setMenuWidget は «前のメニューバー» を deleteLater し、後から
  menuBar() を呼ぶと新しいバーを作ってヘッダごと削除する（実機で「UIが無い＋
  QComboBox already deleted」）。_build_menu は自前の QMenuBar
  （self._menubar）に構築し、_install_header（__init__ の最後）が1回だけ
  setMenuWidget する。ヘッダ行は _HeaderRow の手動レイアウト（r69）:
  メニュー左端・動作ブロック右端・DCC ブロックは «起動ボタンの中心が
  ウィンドウ中央» になる x に置く。M/b バッジは QFileIconProvider で
  maya.exe / blender.exe 埋め込みの正式アイコンを表示（描かない。未検出時は
  文字バッジ）。アイコンは ChatGPT デザイン
  （7個の一列シート）を core/hdr_icons.py に «24px アルファRLE» で埋め込み
  （サンドボックス停止で PNG が作れなかったため。PNG があれば優先）。M/b は
  ブランドロゴではなく独自の文字バッジ（ロゴの再現はしない）。
- **送り先の決定（ユーザー決定）**: ヘッダのスイッチ（設定 dcc_target）で
  Maya/Blender を切替。起動・🗖 は選択中の DCC、接続一覧は両方常時表示。
  .blend は常に Blender、.ma/.mb は常に Maya、共通形式（fbx/obj/abc/usd）は
  選択中の DCC（core.file_operations.dcc_for_path）。右クリックは該当する
  DCC のサブグループを両方出す（_dcc_callback(app, action, paths) で明示）。
  D&D は落下先プロセス（maya.exe / blender.exe）で決める。
- 接続一覧は DCC 別（items の第5要素で識別し、選択中と違えば捨てる）。
  ブリッジは `_bridge`（Maya）/`_bl_bridge`（Blender）、設定
  maya_command_port / blender_command_port。
- **ヘッダのコンボは _PopupCombo**（r71、ui/dcc_header.py）: 本体は固定幅
  （136/196/150px）のまま、showPopup で展開リストの最小幅を «項目の文字幅+40»、
  最小高を «項目数×行高» に広げる（固定幅のままだと接続一覧が途中で切れ、
  行も潰れて「全然見えない」と指摘された）。theme_engine の
  `QComboBox QAbstractItemView::item` で行高 22px と余白を保証。
- **Blender 検出（r71）**: core/blender_version.find_installed_blender_versions
  (extra_paths) は 設定 `blender_exe`（ツールメニュー「Blender の場所を
  指定...」で保存）→ MFM_BLENDER_EXE → 全 «固定» ドライブの Program Files\
  Blender Foundation と <drive>\Blender Foundation → Steam → Store エイリアス
  （%LOCALAPPDATA%\Microsoft\WindowsApps\blender.exe）→ レジストリ
  （Uninstall の DisplayName=Blender*、blendfile 関連付け、App Paths）→
  PATH の順。exe パスで重複排除。**Microsoft Store 版（r75、ユーザーの 5.2 が
  これだった）**: 実体は `C:\Program Files\WindowsApps\BlenderFoundation.Blender_
  <ver>_x64__<id>\Blender\blender.exe`。WindowsApps は ACL で列挙/stat が拒否
  され得るため、_windows_store_candidates（エイリアス %LOCALAPPDATA%\Microsoft\
  WindowsApps\blender*.exe と BlenderFoundation.* サブフォルダ、WindowsApps の
  glob、どれも無ければ Get-AppxPackage を PowerShell で 1 回）＋ _exe_present
  （PermissionError は WindowsApps 配下なら «存在» 扱い）。版はパッケージ名から
  （_version_from_store_path）。エイリアスしか見つからず版が「?」なら
  _resolve_store_packages（Get-AppxPackage → PackageFullName/InstallLocation）で
  差し替える。**WindowsApps 配下の exe に QFileIconProvider を使わない（r76）**:
  シェルのアイコン取得は描画時に遅延実行され、Store パッケージ解決で 1.5 秒
  停止 → access violation で起動直後に落ちた（mfm_freeze.log で確定）。
  app_icon_for は WindowsApps を文字バッジに退避し、通常の exe もその場で
  pixmap を確定させて遅延エンジンを描画中に走らせない。**起動もエイリアス経由
  （r79）**: WindowsApps 直下の exe を CreateProcess すると WinError 5（アクセス
  拒否）。launch_blender は _launchable_exe で %LOCALAPPDATA%\Microsoft\WindowsApps\
  BlenderFoundation.Blender_<id>\blender(-launcher).exe に差し替える。バッジは
  exe から取れない時 resources/icons/app_blender.png（ユーザー提供のロゴ画像、
  2026-09-17）を使う（app_maya.png も同名規則で置ける）。**Store 版と通常
  インストール版は同居可**（r80）: 検出は exe パスで重複排除し、同じ版でも
  `ver@exe` キーで両方保持。版コンボは WindowsApps 配下に「(Store)」を添え、
  ツールチップに exe パス。起動先の差し替え（_launchable_exe）は WindowsApps
  配下だけに効く。走査結果は起動時に mfm_startup.log の
  「blender detect:」行（detection_report）に残る。「Blender が起動リストに
  出ない」報告はまずこの行を見る。ネットワークドライブは触らない。

## DCC からの保存／書き出し（r70）

- 入口: カラムの «空白» の右クリック（→ そのカラムのフォルダ。
  _show_context_menu が lv_hit.rootIndex() で解決し _popup_folder_context_menu）
  と、単一ファイル（.ma/.mb など core/dcc_save の exts_for に含まれる形式）の
  右クリック。項目は「💾 Maya: シーンをここに保存...」「📤 Maya: 選択を
  書き出し...」の **2項目だけ**（ユーザー指示「4つに分けない」）。DCC は
  dcc_for_path で決める: .ma/.mb → Maya、.blend → Blender、フォルダ／共通形式
  → ヘッダのスイッチで選択中の DCC（BrowserPanel.set_dcc_target_provider で
  MainWindow が `lambda: self._dcc` を登録。_add_dcc_save_actions）。
  経路は既存の _dcc_callback(app, "save_scene"|"export_selection", [target])
  → MainWindow._on_maya_drop → _dcc_save_dialog。
- ui/save_dialog.py（SaveDialog）: ファイル名欄（選択ファイル名 or 接続コンボの
  ラベルから取ったシーン名を初期値、拡張子を除いて選択）＋拡張子プルダウン
  「Optional（任意）」＋ DCC の形式一覧（.ma/.mb/.fbx を先頭に、Maya:
  obj/abc/usd*、Blender: blend/fbx/obj/abc/usd*/gltf/glb/stl/ply）。形式を
  選ぶと名前の拡張子を差し替える（既知の拡張子だけ外す。chr_A.v012 は保持）。
  Optional は名前に打った拡張子で判定。オプションは同じダイアログの
  QGroupBox に形式別（core/dcc_save.EXT_OPTIONS の定義から生成）で、
  設定 `save_options` {dcc: {ext: {...}}} に記憶、直近形式は `save_last_ext`
  {dcc: {mode: ext}}。既存ファイルは上書き確認。
- 送信コードは core/dcc_save.maya_code / blender_code。**commandPort は単一式
  しか返さない**ので `(lambda _ns: (exec(src, _ns), _ns.get('_mfm_result'))[1])({})`
  の形で結果（"saved:path" / "Error: ..."）を返す。Maya 側エラーは confirmDialog、
  Blender 側は mfm_popup、加えて "Error:" 応答を _on_bridge_done が警告表示。
  Maya 内モードは同じコードを eval してプロセス内実行。.ma/.mb の «保存» は
  file(rename)→file(save)（以後そのファイルが現在のシーン）、書き出しは
  exportSelected。FBX は fbxmaya の MEL（FBXExport -s）、OBJ は objExport、
  ABC は AbcExport -root、USD は mayaUSDExport。Blender は各 export オペレータ
  （.blend の選択書き出しは bpy.data.libraries.write）。
- 空白右クリックには「貼り付け」（_paste_override_dir でそのカラムへ）・
  「エクスプローラーで表示」・「フォルダパスをコピー」も付けた。
- 回帰テスト: tests/offscreen/test_save_dialog.py（全形式×両モード×両 DCC の
  コードを ast で取り出して compile、ダイアログの拡張子連動／Optional／記憶、
  メニュー項目の DCC 振り分け、空白右クリック→フォルダメニュー）。

## パフォーマンス（拘束ブラウジングの生命線）

- **【最重要】UIスレッドで QFileSystemModel.filePath(index) を呼ぶの禁止**。
  Qtの filePath() は `node->isSymLink() && resolveSymlinks() && ...` の順に
  評価するため、setResolveSymlinks(False) でも isSymLink()（リパースポイント
  判定＝ファイルシステムアクセス）が先に走り、OneDrive/到達不能なネットワーク
  先で**約21秒ブロック**する（faulthandler でフリーズ最中の行を確定。2026-09）。
  代替は `_safe_file_path(model, index)`（fileName() を親方向へ連結。
  fileName() は resolveSymlinks() を先に判定するためI/O無し）。
  browser_panel.py 内の filePath 呼び出しは全て置換済み（r36）。新規コードでも
  filePath を使わないこと。
  * **罠**: ドライブ階層ノードの fileName() は環境（MayaのQt）により
    「ローカル ディスク (C:)」等の表示名を返す。_safe_file_path は
    _DRIVE_IN_LABEL_RE で「C:」へ正規化する（r39。これを怠ると全パスが壊れ、
    スピナーが消えない／Mayaへ不正パス送信、が同時に起きた）。
- **切断済みドライブマッピング（W:, X: 等）の行に触れるの禁止**。ルート直下
  （ドライブ階層）の行に isDir()/filePath()/data() 等で触れると QFileInfo が
  stat を試みて約21秒ブロックする（SLOW記録 'W:/' 'X:/' で確定）。
  filterAcceptsRow は source_parent 無効なら即 True、lessThan は fileName 比較
  のみ。さらにビューの rootIndex を無効（=マイコンピュータ）のまま表示しない
  （起動直後はホームを仮ルートにする）。無効ルートで表示すると Qt が全ドライブ
  を列挙し、列挙スレッドが切断ドライブ毎に21秒待って「起動時に数十秒
  Loading」になる（r37）。
- フリーズ診断は **faulthandler 方式**（_install_freeze_watchdog）が正。Python
  スレッドのサンプラは PySide の C++呼び出し中にGILを取れず「フリーズ終了直後の
  行」しか記録できない（r26〜r34 の誤特定の原因）。mfm_freeze.log の
  「Timeout (0:00:01.500000)!」ブロックがフリーズ最中の正確な行。
- **QFileSystemModel.setResolveSymlinks(False) を必ず維持する**。ONだと
  filePath()/列挙が symlink・ジャンクション行で canonicalFilePath（リンク先へ
  の実I/O）を行い、リンク先が到達不能なネットワークだと **SMBタイムアウト
  約21秒がUIスレッドで発生**する（filterAcceptsRow内のfilePathで21秒×ナビ毎、
  起動時は列挙スレッドで21秒×リンク数=63秒停滞。mfm_freeze.log のスタックと
  21秒ジャストの停止時間で特定。2026-09）。本ツールはリンクを実体へ解決しない
  設計なのでOFFで機能劣化なし（.lnk は resolve_windows_shortcut で別途処理）。

- **QFileSystemModel.setRootPath("") を起動時に呼ぶの禁止**。"" は全ドライブの
  情報収集を «単一の» 収集スレッド(QFileInfoGatherer)へ投入し、切断済み
  ドライブマッピングが1つあるだけで数十秒ブロック。ローカルの directoryLoaded
  までその後ろに並び「起動後1分ブラウズ不能」になった（2026-09 実機）。
  rootPath はナビゲーション時に対象ディレクトリへ設定する。
- **UIスレッドで os.path.isdir/exists をリンク越し・未確認パスに呼ぶの禁止**
  （path_guard の設計原則）。isdir はリンクを«辿る»ため、ネットワーク先の
  ジャンクションが経路にあると毎ナビゲーションでフリーズ（戻るボタンで実例）。
  リンク判定は islink/readlink/lstat（辿らない）で行う（_column_root_for 参照）。
- directoryLoaded のタイムラインは MFM_DEBUG=1 で mfm_debug.log に出る。
  さらに**常時有効**の起動タイムラインがツールフォルダ直下 mfm_startup.log に
  出る（起動ごとに上書き、120秒まで）。初回読み込みの調査はまずこれを見る。
- 初回読み込みの構造: Qtの列挙キャッシュは«プロセス内メモリのみ»なので
  再起動ごとに再列挙される（run_dev/EXE共通、本質的に同じ挙動）。
  _prime_path_loading は全階層を一括投入し目的地を優先（r31。従来の
  directoryLoaded連鎖による1段ずつの直列降下は深いパスで起動を遅くした）。
  DontUseCustomDirectoryIcons を設定済み（desktop.iniシェル照会を抑止）。
- **ファイルアイコンはシェル拡張を走らせない _SafeIconProvider（r77）**。Qt 標準の
  QFileIconProvider は Windows でファイル毎に SHGetFileInfo を «描画時に遅延»
  実行し、P4EXP / TortoiseSVN 等のシェル拡張がプロセス内で動く。mayapy では
  QSortFilterProxyModel.data() の中で 1.5 秒停止 → access violation で起動直後に
  落ちた（2026-09-16、mfm_freeze.log。r75/76 の Store 検出を疑ったが無関係）。
  対策: フォルダ＝標準アイコン、ファイル＝拡張子ごとに 1 回だけ «存在しない
  ダミー名» で問い合わせ（レジストリ既定アイコンのみ、ハンドラ不実行）、
  exe/lnk/url/ico 等は汎用ファイルアイコン。収集スレッドから呼ばれるので
  キャッシュはロック付き。r64（ネイティブダイアログ禁止）と同じ系統の問題。
- **filterAcceptsRow はホットパス**。invalidateFilter の度にモデル内の全読み込み
  済み行へ呼ばれるため、**I/Oは初回も含め完全禁止**。隠し属性は
  「未判定なら表示して _queue_hidden_probe（ワーカーでlstat）→隠しと判明した
  時だけ一括再評価」の非同期方式（r32）。キャッシュだけでは不十分
  （初回パスがネットワーク先で10〜21秒フリーズした実測。C:\GameStudio 配下は
  ジャンクション先がネットワークで1statに秒単位かかる）。
  set_force_visible は内容不変なら invalidateFilter をスキップ
  （ナビ毎のフル再評価を防ぐ）。フィルタ全て空なら早期リターン。
- 接続先の自動切替は «識別できたMaya» のポートを優先（別アプリのポートへ
  送ると「何も起きない」ように見える）。
- **識別できた接続は無応答でも一覧に残す（r78）**: Maya がシーン読込・レンダー・
  モーダルダイアログ中は IDENTIFY がタイムアウトする。従来は 1 回の無応答で
  一覧から落とし「Maya の数は同じなのに更新で増減する／送っても反応しない」に
  見えた（2026-09-16、mfm_maya.log）。_known_conns {port: (label, pid)} を保持し、
  無応答なら前回ラベル＋「…」で残す。ポートが閉じた（scan に無い）時だけ消す。
  スキャンは走行中なら重ねない（_scan_running）。送信の応答が None（ビジー）
  の時はステータスバーに「応答なし（DCC 側が処理中かダイアログ待ち）」を出す。
- 送信コマンド（開く/インポート/リファレンス）のMaya側エラーは Maya の
  confirmDialog で表示する（fire-and-forget のため握りつぶすと気づけない）。
- リファレンスのデフォルトNamespace = ファイル名を「.」区切りした先頭要素。
- **送信・応答待ちは必ずワーカースレッド**（_bridge_send_async）。UIスレッドで
  recv を待つと Maya ビジー時にクリックごとフリーズする（2026-09 に実際に発生）。
- 未保存確認は Maya 側の confirmDialog で行う（リモート照会は禁止）。

## 外部サービス連携（r47〜、core/integrations/）

- プロバイダ方式: git_provider（git CLI必須、TortoiseGitProc/GitHub Desktop任意）、
  svn_provider（TortoiseProc、svn CLIがあれば状態も）、p4_provider（p4 CLI必須、
  p4vc/p4v任意。ルートは P4CONFIG 探索→無ければ `p4 -ztag info` を1回だけ）、
  cloud_provider（OneDrive=環境変数、Dropbox=info.json、Google Drive=固定
  ドライブのボリューム名。状態はクラウドファイル属性の lstat のみ）。
- **絶対条件**: 検出・状態取得・外部コマンドは manager の単一ワーカー
  スレッド（キュー直列）。UIスレッドは辞書参照のみ。コマンドは必ず
  タイムアウト、連続失敗で自動無効化（MAX_FAILURES）。未検出プロバイダは
  一切呼ばれない。Google Drive 検出で **ネットワークドライブ(DRIVE_REMOTE)は
  絶対に触らない**（GetDriveType==FIXED のみ照会）。
- リンク対応: resolve_link_prefix（readlink のみ、辿らない）で実体パスに置換
  してから判定・状態取得し、結果は basename で表示パスへ対応付ける。
  右クリック操作へは実体パスを渡す。
- 更新方針: 表示時（createColumn）に1回 request_status、以後は右クリック
  「連携状態を更新」または操作実行3秒後の再取得のみ（定期更新はしない）。
- UI: StatusBadgeDelegate（各カラムの delegate。アイコン右下 9px の丸＋グリフ、
  色は _BADGE_STYLE が持つ «ロール名» を sys.status_* から引く＝テーマ追従。
  r82 以前は hex 直書きだった）、ツールチップは proxy.data(ToolTipRole)。
  表示可否は **_badge_kind(provider, state, is_dir) が唯一の判定**（描画と
  ツールチップで共用。分岐を別々に書かない）。
- **Perforce は P4V 準拠**（r53、ユーザー指示「P4Vと違うと混乱する」）:
  resources/icons/badge_p4_*.png（ChatGPT でデザイン、24x24、12px で描画）
  edit=赤チェック / add=赤＋ / delete=赤× / outdated=黄三角 / locked=南京錠
  （otherLock）/ other_open=人＋チェック（otherOpen）。**最新・デポに無い
  ファイル・フォルダには何も付けない**（fstat は直下ファイルのみでフォルダの
  状態は取れないため、付けると嘘になる）。Git/SVN/クラウドは各サービスの
  慣習通りフォルダにも丸バッジ。
- **操作結果は黙殺しない（r58）**: Provider.run_cli_async → 終了時に
  provider.report(ok, msg) → manager.action_finished Signal → browser_panel の
  モジュール関数 _on_integration_action_finished（プロセスで1回だけ接続。
  失敗＝ダイアログに CLI メッセージそのまま／成功＝ステータスバー、直後に
  状態再取得）。p4 は rc=0 でもエラー文を出すため cli_output_is_error
  （_ERR_MARKERS。ファイル名に含まれ得る一般語は入れない）で判定。
- **事前チェック（r58）**: actions() のタプル第3要素 dict
  `{"enabled": False, "tooltip"}`（灰色＋理由）/ `{"confirm": 文}`（Yes/No）。
  p4 は直近 fstat の otherOpenN/otherLockN（user@ws）を _file_info に保持し、
  チェックアウトは「他者ロック→不可」「他者チェックアウト中→同時編集の確認」、
  revert は常に確認。判定はキャッシュ参照のみ（I/Oなし）。
- **アイコン画像は ChatGPT でデザインする**（ユーザー指示）。取得手順は
  Claude in Chrome（ログイン済み）で生成 → JS canvas でセル切り出し →
  数値RLE文字列で受け渡し（base64 は javascript_tool がブロック）→ サンドボックス
  の PIL で PNG 復元 → resources/icons/。
  右クリックは該当ワークスペースのプロバイダだけ「⎇ Git」等のサブメニュー。
  全体スイッチ: 編集メニュー／設定キー integrations_enabled（再起動で反映）。
- 既知の制限: クラウドの「同期中」は属性から判定不能のため非表示。OneDrive/
  Google Drive の「Webで表示」はサービスのルートを開く（パス→URLの対応が
  APIなしでは取れない。Dropbox はフォルダ直リンク可）。

## 対応バージョン方針（2026-09 決定）

- 目標: Maya 2023〜2027。まず **Maya 2027 で正常動作**させ、その後 2023/2024
  （PySide2/Qt5.15）はサンドボックスに PySide2 5.15 を入れてオフスクリーン
  テスト一式を Qt5 で回して差異を潰す（ユーザー決定: 方針2）。
- スタンドアロン（EXE）は Maya バージョン非依存（起動と commandPort 連携のみ）。
  難所は **Maya内モード**の Qt5（2023/2024）。Qt6限定API（event.position 等）は
  フォールバック済みだが Qt5 実動作は未検証。

## コード構造図（tool-diagram ルール、2026-09-15〜）

- Archify（tt-a1i/archify）でアーキテクチャ図＋ワークフロー図。ソースは
  docs/diagrams/architecture.architecture.json / workflow*.workflow.json、成果物は
  docs/architecture.html / docs/workflow*.html。私用（内部名をそのまま載せる）。
  **ワークフローは 1 枚 12 ノード・6 列の制約があり全体を 1 枚に収められない**ため
  4 枚に分割（ユーザー指摘「全体は見れないのか」2026-09-16）: workflow_startup
  （起動→ナビ→カラム生成）/ workflow（クリック→DCC 送信）/ workflow_fileops
  （ファイル操作と Undo）/ workflow_integrations（連携）。入口は docs/index.html。
  構造変更時は該当する枚だけ更新し、5 枚とも deliver → inject を通す。
  **ワークフローはユーザー視点で書く**（ユーザー指示 2026-09-16）: レーンは
  ユーザー / MayaFileManager / Maya・Blender / ファイル・外部サービス、ラベルは
  「送り先を選ぶ」「対象を確定」のような操作・挙動の日本語。関数名は
  paths.json の symbol（FILES サイドバー）側に置く。アーキテクチャ図だけ開発用。
- モジュール/クラス/主要関数の追加・削除・改名、責務移動、処理順・外部依存の
  変更をしたら **同じターンで JSON を直して deliver --quality showcase** を通す。
- **Archify はサンドボックス（/sessions/<id>/archify に git clone）で回す。
  ユーザーPCへは何もインストールしない**（ユーザー指示。bat 配布は撤回済み）。
  サンドボックス停止中は生成を保留して、復旧後に同じターンで deliver する。
  deliver 受領は validation.checksPassed 9/9・errors 0・warnings 0 を確認。
- **階層ビュー（2026-09-16〜）**: docs/diagrams/paths.json（root=D:\Claude\PLs-Tools\
  MayaFileManager、copy_mode=full、editor=vscode、files=パッケージ内全ファイル
  （tests/docs/__pycache__ 除く）、nodes=両図の全 id → file/line/symbol）。
  deliver の **後に必ず** `node inject-paths.mjs docs/<t>.html docs/diagrams/paths.json`
  （スクリプトは tool-diagram スキル末尾の全文を scratchpad に書き出す。deliver は
  HTML を上書きするので再 deliver ごとに再 inject）。ファイルの追加・移動・改名や
  主要シンボルの行ずれがあれば paths.json も同じターンで更新。

## 右クリックの DCC 項目は選択中 DCC 1つ分だけ（r80、2026-09-17）

- 交換形式（.fbx/.obj/.abc/.usd… = INTERCHANGE_EXTENSIONS）は **ヘッダで選択中の DCC の
  インポート／リファレンス項目だけ** 出す。.ma/.mb は常に Maya、.blend は常に Blender
  （混在選択もネイティブ形式側に寄せる）。以前は .fbx で Maya と Blender の両方が並んで
  いた（ユーザー指摘）。実装は _popup_context_menu の show_maya / show_blender、
  回帰テストは tests/offscreen/test_dcc_menu_by_target.py。save/export 側
  （_add_dcc_save_actions）は元から dcc_for_path で同じ規則。
- ローカル VM（device_bash）で PySide6 + apt-get download した libEGL 等（$HOME/qtlibs/libs）
  を LD_LIBRARY_PATH に通せば tests/run_offscreen.py が回る（19/19 PASS 確認済み）。
- inject-paths.mjs は docs/diagrams/inject-paths.mjs に常設。行ずれ修正時は
  paths.json を直して 5 枚に再 inject するだけでよい（構造不変なら deliver 不要）。

## 展開中フォルダの目印（r81、2026-09-17）

- フォルダを開いて右に子カラムが出た後、同じカラムでファイルを選ぶと選択色がファイルへ
  移り「右のカラムがどのフォルダ以下か」分からなくなる（ユーザー指摘）。**右隣に中身を
  出しているフォルダ（＝いずれかのカラムの rootIndex に一致）には、選択とは別の淡い目印**
  （palette.highlight を α46 で面塗り＋左端 3px を α150 の帯）を描く。選択中は描かない。
- 実装: `_paint_expanded_mark()`（StatusBadgeDelegate / ThumbnailDelegate の両方から呼ぶ）、
  `CappedColumnView._track_column / _is_expanded_index / _repaint_columns`（createColumn で
  カラムを追跡し、生成／破棄のたびに全カラム再描画）。テーマトークンには依存しない。
- 回帰テスト: tests/offscreen/test_expanded_folder_mark.py（ピクセル比較）。

## クラウドストレージ（オンラインのみ）の扱い（r83、2026-09-17）

- OneDrive / Dropbox / Google Drive の «オンラインのみ» ファイルは、**内容に
  触れた瞬間に実体がダウンロード（ハイドレート）される**。フォルダを開いただけで
  大量ダウンロードが走り、体感で固まる（ユーザー報告）。
- 判定は `core/cloud_state.py` の `is_online_only()`（`os.lstat` の
  `st_file_attributes` のみ＝ハイドレートしない）。**属性定数の単一の真実は
  ここ**。`core/integrations/cloud_provider.py` もこれを import する。
- **内容を読む処理はオンラインのみを必ず除外する**:
  * サムネイル生成（`ThumbnailWorker._generate` / `ThumbnailManager.prefetch`）
    → 種別アイコンで代替。Maya のサイドカーがオンラインのみの場合も読まない
  * Quick Look → 選択追従（`show_for(allow_download=False)`）では読まず
    「オンラインのみ」カードを出す。**Space の明示操作のときだけ** 読む
- **先読みはサムネイル表示のカラムがあるときだけ**（`_prefetch_thumbs_async` の
  `force` / `_any_thumb_column`）。リスト表示のデリゲートはサムネを使わないので
  従来の「移動のたびに 64 件先読み」は完全な無駄だった。
- 名前の一覧は QFileSystemModel の収集スレッドが出すので、上記さえ守れば
  「名前は即出る・ダウンロードは実行時だけ」になる。

## 遅延カラム再構築は «ユーザーの選択を壊さない»（r83）

- `_force_column_rebuild` は navigate 後 90/320/1200/2600ms に遅延実行される
  **見た目の自己修復**。クラウドのように列挙が遅いフォルダでは、ユーザーが
  Ctrl/Shift で選んでいる最中に発火し、current 無効化→再設定で選択が全消去
  されていた（＝「クラウドストレージで複数選択ができない」の真因）。
- ガード: **選択が2件以上** または **ナビゲーション後にユーザーが項目を
  クリック済み**（`CappedColumnView._mfm_user_selected`、`_navigate_now` で解除）
  なら再構築しない。自己修復より選択の維持を優先する。
- 回帰テスト: tests/offscreen/test_cloud_and_selection.py

## テーマトークンの import は «必ず遅延»（r82 の実害）

- UI モジュールのトップレベルで `from core.theme_engine import qss_vars` を
  書くと、Maya 内ホットリロードや部分再読込で partially initialized module に
  当たり `ImportError: cannot import name 'qss_vars'` で起動不能になる（実際に
  発生）。各 UI モジュールは関数 `_tv()` の中で import すること。

## サムネイル表示（ThumbnailDelegate）の描画契約（r84、2026-09-17）

- **「絵が取れなくても、アイコンとファイル名は必ず並ぶ」**（ユーザー指示）。
  描画の優先順位: ① 生成済みサムネイル → ② モデルの装飾アイコン
  （`Qt.DecorationRole`＝_SafeIconProvider の種別アイコン）→ 名前は常に描く。
- **表示モードで配置を変える**（`_icon_mode()` が判定）:
  * IconMode（グリッド）… 画像を上・名前を下（中央寄せ、2行まで折り返し）
  * ListMode … 画像を左・名前を右
  r84 以前は **常にリスト配置で描いていた** ため、IconMode では名前が項目矩形の
  外（x+thumb_size+8）に出て消え、サムネ未生成だと «グリッドが真っ白» になった。
- `sizeHint` も IconMode では名前の行を含めて返す（グリッドと整合させる）。
- **フォルダはサムネイル生成の対象外**（"?" の汎用チップになる）。モデルの
  フォルダアイコンを使う。生成失敗時のプレースホルダも "generic" ではなく
  **その拡張子の種別**（MA / FBX / IMG …）にする。
- サムネイル到着時の再描画は `_thumb_view` だけでなく
  **サムネ表示中のカラムも** update する（`_on_thumbnail_ready`）。
- 先読みは `CappedColumnView.set_thumb_prefetch_callback()` 経由で BrowserPanel
  に依頼する（`_set_column_view_mode` は CappedColumnView 側のメソッドなので、
  `self._prefetch_thumbs_async` は存在しない。r84 で修正）。
- 回帰テスト: tests/offscreen/test_thumb_view_mode.py（項目矩形をピクセル検査し、
  «背景以外が描かれている» ことを全項目について確認する）

## DCC 送信は «1操作＝1回»（r86、2026-09-18）

- 症状: Maya へのリファレンスが同一ファイルで **2回** 実行される（ユーザー報告）。
- 対策: `MainWindow._dcc_once(kind, path, app)` を **DCC 送信の直前** に必ず通す。
  同じ (種別, パス, 送り先) が **1.2 秒以内** に再度来たら捨てて、ログに
  `dcc-send: 二重発火を抑止 …` を残す。`_dcc_open` / `_dcc_import` /
  `_dcc_reference` の3経路に入れてあるので、クリック動作・右クリック・D&D の
  どこから来ても止まる。`_on_maya_drop` 側では同一パスの重複も除去する。
- **発火元の特定は保留**（Windows 実機でしか再現しないため）。二重に呼んでいる
  経路が分かったら本丸を直すこと。ログの抑止行がその手がかりになる。
- 意図した「2回リファレンス」は 1.2 秒空ければ通る。

## 保存ダイアログの拡張子プルダウン（r86）

- **既定は「Optional（任意）」**（index 0、ユーザー指示）。形式はファイル名の
  拡張子から決まる（`effective_ext()`）。
- 名前が空で «.fbx» だけの状態、および拡張子なしの名前でも **直近に使った形式**
  へフォールバックする（既定を Optional にしたので、これが無いと
  「前回 FBX で書き出したのに .ma になる」事故になる）。
- `splitext(".fbx")` は先頭ドットを隠しファイル名と見なし拡張子を返さないので、
  その場合を明示的に拾っている。

## コンボボックスの ▼ は «生成した PNG»（r86）

- QSS は subcontrol に文字を置けず、border で三角を作る CSS の小技も Qt では
  **小さな四角にしか描かれない**（実機確認）。`theme_engine._arrow_png()` が
  テーマ色の ▼ PNG を `~/.maya_file_manager/ui/arrow_down_<色>.png` に生成し、
  `QComboBox::down-arrow { image: url(...) }` で渡す。色ごとにキャッシュ。

## カラム間の D&D は «自前処理»（r87、2026-09-18）

- 症状: カラムからカラムへドラッグしても移動されない。
- 原因: 落とし先の処理を Qt 標準（QFileSystemModel の dropMimeData）に任せて
  いたが、**プロキシ越しのカラム表示では届かず無反応**になる。さらに標準任せ
  では «同名がある時に選ばせる» ことができない（黙って連番になる）。
- 対策: `CappedColumnView.eventFilter` で DragEnter / DragMove / Drop を受け、
  `_handle_view_drag()` で処理する。ビューポートに後から載せたフィルタは
  QAbstractItemView より先に呼ばれるので、標準処理より先に消費できる。
  * 落とし先 = カーソル下がフォルダならそれ、そうでなければ **そのカラムの
    フォルダ**（`_drop_dir_for`）
  * **既定は移動／Ctrl でコピー**（Explorer 準拠）。ドラッグ開始時の
    `drag.exec()` の既定アクションも Move にしてある
  * ビュー組み替え中のクラッシュを避けるため、実処理は `QTimer.singleShot(0)`
    でイベントを抜けてから `BrowserPanel._on_files_dropped()` で行う
- **自分自身／自分の中へ／同じフォルダへ** のドロップは捨てる（`_on_files_dropped`）。
- Undo は MoveOp（コピー時は CopyOp）。`_run_file_op` の進捗・キャンセル付き。

## 同名衝突は «上書き / 名前を変えて / スキップ»（r87）

- `ui/conflict_dialog.py`（ConflictDialog）。サイズと更新日時を並べて判断させ、
  「残りすべてに適用」で以降を自動処理。×／中止＝**操作全体を中止**。
- ダイアログはワーカースレッドから出せないので、**実行前にまとめて聞いてから**
  決定表を `conflict_cb` として `move_items` に渡す。
- `core/file_operations.move_items(..., conflict_cb=, pairs=)`:
  CONFLICT_RENAME（既定・従来どおり連番）/ CONFLICT_OVERWRITE / CONFLICT_SKIP。
  **フォルダ同士の上書きは «統合»**（`_merge_move`。Explorer と同じ。同名
  ファイルだけ置き換え、既存の他の中身は残す）。移動したものは pairs に
  (元, 先) で積み、Undo に使う。
- 回帰テスト: tests/offscreen/test_drop_move.py

## マネージャーからの操作は «DCC 側に» ログを残す（r88、2026-09-23）

- ユーザー指示: Maya はスクリプトエディタ、Blender も同程度の場所に出す。
- `core/dcc_log.py` の `wrap_maya()` / `wrap_blender()` が、送る «式» を
  「開始行 → 元の式を評価（戻り値はそのまま返す）→ 完了 / 失敗 / キャンセル /
  確認待ち」の式に包む。`_maya_send_or_prompt(..., log=(操作名, パス))` /
  `_blender_send_or_prompt(..., log=...)` に log を渡すと包まれる。
  判定: 例外＝失敗（再送出）、戻り値が `Error:` / `Failed:`（大小無視）＝失敗、
  `Cancelled`＝キャンセル、`confirm`＝確認待ち、それ以外＝完了。
- Maya: `maya.api.OpenMaya.MGlobal.displayInfo / displayWarning / displayError`。
  開く・インポート・リファレンスの Maya 側コードは、失敗を confirmDialog に
  出すだけでなく `_mfm_result = 'Failed: …'`（開くのキャンセルは 'Cancelled'）を
  返すよう式形式に変えた（ログで «完了» と誤表示しないため）。
  Maya 内起動（プロセス内実行）は `_maya_local_log()` で同じ行を出す。
- Blender: ブリッジの `mfm_log()` → **Info エディタ＋ステータスバー**＋システム
  コンソール。Python から呼んだオペレータの report は «呼び出し側の持ち物» で
  Info に届かない（bpy_operator_function.cc）ため、**モーダル**の
  `MFM_OT_report` にして WM に報告を引き取らせている（wm_event_system.cc の
  "Take ownership of reports"）。失敗も WARNING で出す（ERROR だと WM が
  報告ポップアップを出し、mfm_popup と二重になる）。
- **古いブリッジ（mfm_log 無し）では print にフォールバック**して落ちない。
  インストール済みの Blender 自動起動ブリッジは、マネージャー起動時に中身が
  違えば差し替える（`blender_bridge.refresh_installed_startup()`）。
- 回帰テスト: tests/offscreen/test_dcc_log.py（偽 maya モジュールで式を実評価）

## DCC 切替は «ON/OFF スイッチに見せない»（r88）

- ユーザー指摘: Maya=OFF、Blender=ON に見える。単なる二者択一なので、
  `DccSwitch` のトラック色・ノブ色を **左右どちらでも同じ**（トラック=
  surface_container_high、ノブ=primary）にし、反対側の止まり位置に小さな点を
  打って 2 択であることを示す。選択中の側は左右の M / B バッジの強調でも分かる。
- `theme_engine.switch_colors()` から `track_on` を廃止。
- 回帰テスト: tests/offscreen/test_dcc_switch_look.py（左右でピクセル比較）

## 読み込み時の «プロジェクトをセットしますか？»（r89、2026-09-23）

- Maya へ 開く／インポート／リファレンス（D&D・右クリック・クリック動作・
  ブックマーク、全経路）する時、ファイルの場所から上へ登って **workspace.mel**
  を探す（`core/maya_project.find_project_root`、ドライブ直下まで・最大40階層）。
- 見つかったフォルダが **Maya の現在のプロジェクトと違えば**、Maya 側の
  confirmDialog で「セットする／セットしない」を聞く。セットは
  `mel.eval('setProject "…"')`（最近のプロジェクト一覧も更新される）、失敗時は
  `cmds.workspace(…, openWorkspace=True)`。セットしたらスクリプトエディタに記録。
- **無い場合・既にそのプロジェクトの場合は何もしない**（ユーザー指示）。
- 「セットしない」を選んだプロジェクトは、その Maya セッション中は再確認しない
  （`__main__._mfm_setproj_declined`。複数ファイルを連続リファレンスした時に
  毎回聞かれないように）。
- 探索はマネージャー側、比較と確認は Maya 側（現在のプロジェクトは Maya しか
  知らず、マネージャーから問い合わせると Maya ビジー時に UI が止まるため）。
  `setproject_code(path, indent)` が Maya 側で実行する文を返す。**開く** は
  未保存確認で «開く» に進んだ時だけ聞き、開く前にセットする。
- Maya 内起動（プロセス内）は `_maya_local_setproject()` で同じ文を exec する。
- 注意: 送信コードは `"…" % 値` の書式でリテラルを組んでいるが、
  setproject_code の文字列は **書式の外に `+` で連結** している（中に `%` を
  含むため、書式に巻き込むと壊れる）。
- 回帰テスト: tests/offscreen/test_maya_setproject.py（実際に組み立てた送信コードを
  偽 maya モジュールで評価）

## DCC ウィンドウへの D&D は «Manager が引き受ける»（r90、2026-09-23）

- 症状: install.py を Maya のビューポートへ D&D → Maya は何も起きず、
  Manager が固まり、Maya を落としても戻らない。mfm_freeze.log で
  `_start_multi_drag` の `drag.exec(...)` に 250 秒以上止まっていた。
- 原因: Windows の D&D（DoDragDrop）は **落とし先の Drop 処理が終わるまで
  ドラッグ元を止める**。Maya は落とされた .py を «Drop 処理の中で» 同期実行
  する（onMayaDroppedPythonFile）。インストーラがダイアログを出すと Drop が
  戻らず、ダイアログは前面に出ないこともあり、双方が止まる。
  さらに設定の «D&D 動作» もドロップ後に Manager から送るので、DCC 自身の
  ドロップ処理と **二重実行** になっていた（.ma を落とすと Maya が取り込み、
  Manager も «開く» を送る）。
- 対策: ドラッグの mime を `_DccAwareMime`（Qt の遅延レンダリング
  `retrieveData`）にし、**落とし先がファイル一覧を取りに来た瞬間に** 判定する。
  カーソル下が Maya/Blender で `MainWindow._can_take_over_drop()` が True なら
  DCC には **空の一覧** を渡してネイティブ処理をさせず、ドロップ後に Manager が
  ブリッジ経由（非同期）で実行する。自アプリ内・Explorer 等へは実ファイル。
- 引き受ける条件: ブリッジ接続中（未接続なら DCC に任せる）かつ全ファイルが
  扱える形式（Maya の .py/.mel は常に可、他は «D&D 動作» が なし 以外で
  取込可能形式）。判定はドラッグ 1 回につき DCC ごとに 1 度だけ（COM 呼び出しの
  中なので重い処理禁止。接続確認は 0.2 秒上限）。Maya 内起動では常に False。
- `_notify_drag_finished(paths, mime)` は **引き受けた落下だけ** 実行する
  （DCC に実ファイルを渡した＝ネイティブ処理なら送らない＝二重実行しない）。
- Maya へ落とした .py は `_maya_run_script()`: Maya 標準の
  `maya.app.general.executeDroppedPythonFile(path, obj)`（onMayaDroppedPythonFile
  を呼ぶ）で実行、無い版は runpy で同等処理。.mel は source。送信後に Maya を
  前面へ出す（インストーラのダイアログが裏に隠れないように）。
- 平坦ビューのドラッグも同じ mime（`_DragListView._mime_factory`）。
- **ブリッジ未接続の DCC** への落下は従来どおり OS 任せなので、落とし先が
  Drop 内で止まれば同じ固まりは起こり得る（OS の仕様。回避策は接続しておくこと）。
- 回帰テスト: tests/offscreen/test_dcc_drop_takeover.py

## DCC コマンドは «対象形式にだけ» 反応する（r91、2026-09-23）

- ユーザー指示: .py を D&D したら «開く» を送ろうとした。Maya・Blender 問わず
  **絶対にそのコマンドが対象のものにだけ反応**すること。
- 単一の表: `core/dcc_caps.py` の CAPS（DCC × コマンド → 拡張子）。
  Maya: 開く=.ma/.mb / インポート=.ma .mb .fbx .obj .abc .usd系 /
  リファレンス=.ma .mb .fbx .abc / スクリプト実行=.py .mel。
  Blender: 開く=.blend / インポート=.blend .fbx .obj .abc .gltf .glb .stl .ply .usd系 /
  リンク=.blend。フォルダは常に対象外。
- 関所: `MainWindow._dcc_accepts(command, path, app)` を **送信直前に必ず通す**
  （_dcc_open / _dcc_import / _dcc_reference / _maya_run_script）。対象外は送らず
  ステータスに「スキップ: … は … の「…」の対象外です」。クリック動作・右クリック・
  D&D・ブックマークの全経路がここを通る。「開く」に複数渡された時は対象の最初の1件。
- 右クリックメニューも同じ表から項目を出す（.py に «Maya で開く» は出ない、
  .usd に «リファレンス» は出ない、Blender の «リンク» は .blend のみ）。
- D&D の引き受け（r90）は «ファイルごと» に判定し、`_can_take_over_drop` は
  **引き受けるファイルの一覧**を返す（旧: bool）。引き受けた分だけ DCC から隠し、
  残り（Manager の対象外）は DCC 自身のドロップ処理に渡す。
- インポート/リファレンスの Maya 側コードは形式ごとのプラグイン
  （fbxmaya / objExport / AbcImport / mayaUsdPlugin、`MAYA_PLUGIN_FOR_EXT`）を
  必ず先にロードする（未ロードだと «未対応形式» で失敗し «対象» と言えないため）。
- 回帰テスト: tests/offscreen/test_dcc_caps_gate.py（全形式×全コマンド×両 DCC で
  «対象外は一度も送らない» を総当たり）

## D&D の対象は «ドラッグ前に選んだものだけ»（r92、2026-09-23）

- 症状: カラムでファイルを1つクリックしてからドラッグすると、他のフォルダや
  ファイルまで一緒に D&D される。
- 原因: **QColumnView は全カラムで «同じ選択モデル» を共有する**。今いる階層の
  パンくず（祖先フォルダ）も選択状態なので、`selectionModel().selectedIndexes()`
  をそのまま使うと左カラムの祖先まで対象に入る。
  さらに Qt の `startDrag`（ネイティブ経路）も同じ選択モデルを見るため、
  «未選択フォルダをそのままドラッグ» でも同じことが起きていた。
- 対策:
  1. `_same_column_selection(sm, idx)` … 押した項目と **同じ親（＝同じカラム）**
     の選択だけを返す（押した項目は必ず含む）。pending drag はこれを使う。
  2. **未選択フォルダも pending 経路へ**（ネイティブの startDrag を使わせない）。
     押下でそのフォルダを単一選択し、リリースで従来どおり選択確定＋ナビゲート。
  3. `_start_multi_drag` の最終防衛: 重複除去＋`_drop_ancestor_dirs`
     （他の対象の祖先フォルダを落とす）。カラム／平坦ビューの全経路が通る。
- 回帰テスト: tests/offscreen/test_drag_selection.py（QDrag を差し替えて
  «実際に何がドラッグされたか» を検査。単一／複数／未選択フォルダ）

## ドラッグ直前に «対象より上の全部» が選択される（r94、2026-09-23）

- 症状: カラムで1つ選んでドラッグしようとすると、対象より上にリストされている
  項目が全部ハイライトされる。ドラッグが始まったのかも見た目で分からない。
- 原因: **`QAbstractItemView::mouseMoveEvent` は «左ボタンが押されたまま動いた»
  だけで `DragSelectingState` に入る**。その矩形選択の起点は
  `QAbstractItemViewPrivate::pressedPosition` で、**押下イベントを受け取って
  いなければ既定値（＝ビュー左上）のまま**。r92 で押下は eventFilter が消費
  するようにしたが、**Move 閾値未満の MouseMove を素通し**していたため、
  ビュー左上→カーソルの矩形＝«対象より上の全て» が選択されていた。
  （選択モデル共有の問題＝r92 とは別物。D&D のペイロード自体は正しかった）
- 対策（ui/browser_panel.py eventFilter / MouseMove）:
  1. `_pending_multi_drag` がある間は **閾値未満の MouseMove も必ず消費**する
     （`return True`）。
  2. 押下を消費した直後（右クリック・Ctrl/Shift 選択・空白クリック＝
     `_swallow_release`）に左ボタンを押したまま動いた場合も同様に消費する。
  3. `_drag_pixmap()` でドラッグ中のゴースト画像（アイコン＋名前、複数なら
     «+N»）を付ける。ドラッグが始まったことが見た目で分かるようにするため。
- 教訓: **押下を自前で消費したら、対応する Move/Release も全部消費する**。
  片方だけ塞ぐと Qt 側が «押下位置 0,0» のまま状態遷移して別の壊れ方をする。
- 回帰テスト: tests/offscreen/test_drag_rubberband.py（閾値未満の MouseMove が
  消費されること／その間に選択が変わらないこと）

## プリセット毎に «最後に見ていたディレクトリ» を記憶する（r95、2026-09-24）

- 要望: プリセットを切り替えたら、前にそのプリセットだった時に最後に表示して
  いたディレクトリへ戻ってほしい（エリア毎に独立）。
- 実装:
  * `QuickNavBar.preset_changed = Signal(str, str)`（変更前, 変更後）を新設。
    `_on_preset_changed`（コンボ操作）と `set_active_preset`（🔗リンク連動・
    プログラム切替）の両方から emit する。`set_active_preset(name, notify=False)`
    は **状態復元専用**（復元した path を切替復元で上書きしないため）。
  * `BrowserArea._on_preset_switched(prev, new)` … prev に今のディレクトリを
    記録 → new に記録があればそこへ `navigate_to`。記録が無ければ現在地のまま。
  * 保存先は **エリアの状態**（`get_state()/apply_state()` の `preset_paths`）。
    `browser_areas_state` に入るので再起動後も残る。get_state 時は現在の
    プリセットの分も確定させてから保存する。
  * 🔗リンクONで連動したエリアも、**それぞれ自分の記憶**で移動する
    （連動元のディレクトリを他エリアへ押し付けない）。
- 回帰テスト: tests/offscreen/test_preset_dirs.py（エリア独立／往復復元／
  状態保存ラウンドトリップ／リンクON連動）

## ドラッグが «始まらない» / 矩形選択が走る（r96、2026-09-24）

- 症状: r94 後も D&D が効かない。実機ログは `drag-start` の次に
  `move-without-pending` が出るだけで、**`drag-exec前` が出ていない**。
  ＝ `_start_multi_drag` が `drag.exec()` に到達する前に例外で落ちていた。
- 原因1（致命）: `_start_multi_drag` 全体が `except Exception: pass` で
  握り潰されていた。mime 生成や QDrag 生成で失敗しても «何も起きない» だけで
  ログに残らず、原因が特定できない状態が続いた。
- 原因2: r94 で塞いだのは «pending がある時» と `_swallow_release` の時だけ。
  自前ドラッグが終わった直後・失敗した直後の MouseMove（左ボタン保持）は
  素通しのままで、そこから Qt の矩形選択（押下位置 0,0 起点）が走っていた。
- 対策:
  1. `except` を **traceback ごとログ**（`drag-start FAILED:`）。握り潰し禁止。
  2. 段階ログ `drag-mime:` を追加し、`make_drag_mime` が失敗しても
     **素の `QMimeData`＋URL に退避して必ずドラッグを開始**する。
  3. `drag.exec` は **PySide2 に `exec` が無い版がある**ため
     `getattr(drag, "exec", None) or getattr(drag, "exec_", None)` で呼ぶ
     （ui/flat_column.py は元から両対応だった＝この経路だけ取り残されていた）。
  4. **左ボタン保持中の MouseMove は常に消費**する（このビューの選択は全て
     自前で行うので、Qt の矩形選択は一切不要）。
- 教訓: **D&D のような «出口が1つしかない» 経路で except pass を書かない**。
  失敗が «無反応» に化けて、原因調査が何往復も無駄になる。

## 圧縮ファイルの中身をブラウズする（r97、2026-09-24）

- 方針: **できること／できないことは Windows 標準のエクスプローラーと同じ**。
  * できる: 一覧・階層のたどり込み／中のファイルを開く（テンポラリへ展開して
    既定アプリ）／展開（取り出し）／中のファイルを外へ D&D
  * できない: 書庫の中身の書き換え（リネーム・削除・上書き・書庫への追加）／
    パスワード付き書庫の展開（一覧だけは出る）／.rar・.7z（Windows 標準が
    扱えないため対象外）
- 対応形式: `.zip` と tar 系（`.tar/.tar.gz/.tgz/.tar.bz2/.tbz2/.tar.xz/.txz`）。
  tar 系はエクスプローラーでは開けないが読み取り専用で害が無いため追加対応。
- 構成:
  * `core/archive_browse.py` … 読み取り層。`is_archive` / `list_entries`
    （実体の無い中間ディレクトリを補完する `_with_implied_dirs` 付き）/
    `children(entries, inner_dir)` / `extract_members` / `extract_to_temp`。
    **Zip Slip 対策 `_safe_join` は必ず通す**（`../` で書庫外へ書き出せてしまう）。
    テンポラリは書庫のパス＋サイズ＋更新日時のハッシュで分けて使い回す。
  * `ui/archive_column.py` … `ArchiveColumn`（ヘッダ＋内部 QColumnView）。
    モデルは `_ArchiveModel(QStandardItemModel)` で、`mimeData()` を上書きして
    **D&D 時にテンポラリへ展開した実ファイルの URL を返す**
    （書庫の中身そのものは掴めないため。エクスプローラーも同じ挙動）。
    各カラムは `DragOnly` / `setAcceptDrops(False)`＝書き込み不可。
  * `ui/browser_panel.py` … `_on_archive_request(path)` で `_view_stack` へ
    «次のカラム» として出す。平坦カラム・ナビゲーションとは排他。
- 見た目: 通常カラムと区別するため専用トークン `plane_archive` /
  `plane_archive_header` / `on_plane_archive`（琥珀寄り、ダーク/ライト両方）。
- 回帰テスト: tests/offscreen/test_archive_column.py（階層表示／専用の面色／
  D&D の展開／選択部分だけの展開／tar.gz／Zip Slip 拒否／非書庫で閉じる）

## リネーム: 選択追従と «開いているフォルダ» のロック（r98、2026-09-24）

### リネーム後は新しい名前の項目を選択する
- `_select_when_visible(path)` … QFileSystemModel の反映は非同期なので、
  項目が現れるまで最大 ~2.5 秒リトライして選択＋scrollTo する。
  **インライン編集中（`_inline_editor` あり）は選択を奪わない**
  （Tab 送りの連続リネームが壊れるため）。カラムに無ければ平坦／サムネビューも探す。
- インライン確定・ダイアログの **両方を `_on_fs_file_renamed` に集約**した
  （従来ダイアログ経路はパス欄追従もしていなかった）。
- 既存バグも同時修正: `_rename_when_visible` が BrowserPanel に無い
  `self._prune_selection_to_single` を呼んでいて、新規作成項目の選択＋
  scrollTo が毎回例外で無効化されていた（`self._column_view.` 経由へ）。

### 「Manager で開いていると Rename / 移動できない」（WinError 5）
- 原因: **QFileSystemModel（QFileInfoGatherer）は fetch したディレクトリを
  監視スレッドで掴み続ける**。Miller カラムはフォルダを展開して回るので、
  «今カラムに出ているフォルダ» 自身のハンドルが開きっぱなしになり、Windows では
  そのフォルダのリネーム／移動が `[WinError 5] アクセスが拒否されました` になる。
  （QFileDialog で同じ問題が出ないのは、親ディレクトリしか watch しないため）
- 対策: `core/file_operations.rename_path(src, dst, release_cb)` … WinError 5/32
  の時だけ **段階的に手放して再試行**する。
  * level 1 … `setRootPath(親)` ＋サムネイル生成の停止
  * level 2 … `_recreate_fs_model()` で **QFileSystemModel ごと作り直す**
    （Qt に «このディレクトリの監視だけ外す» API が無いため、確実に手放すには
    モデルを捨てるしかない）
  * 最後に Windows シェルのリネーム（`SHFileOperationW` / FO_RENAME）へ退避
  * 権限以外のエラー（同名・不正文字）は再試行せず即返す
- 移動（`_transfer_with_progress`）も、対象にフォルダが含まれる時は
  事前に level 1 の手放しを行う。
- エラー表示は `_rename_error_text()` で «OneDrive 同期中／アプリが開いている／
  エクスプローラーが開いている» の心当たりまで出す（WinError の数字だけ出さない）。

### 現在地が消えたら «実在する一番近い親» へ
- `nearest_existing_dir(path)` ＋ `_fallback_to_existing_ancestor()`。
- 呼ばれる所: 移動／コピー完了後、4 秒ごとの監視タイマー `_gone_watch`
  （外部操作で消えた場合も拾う）。
- **表示中フォルダ自身（または祖先）のリネーム時は、親へ逃げずに新しいパスへ
  追従する**（`_on_fs_file_renamed` でプレフィックス置換）。
- 回帰テスト: tests/offscreen/test_rename_select.py /
  tests/offscreen/test_rename_move_open_folder.py

## サムネイルが 1 枚も出ない（r99、2026-09-24）

原因は 2 つ。どちらも «出ない» 症状になるので両方直した。

### 1. QPixmap をワーカースレッドで作っていた
- `ThumbnailWorker` は QThreadPool 上で動くのに `QPixmap` を生成して
  シグナルで渡していた。Qt の規約違反
  （"It is not safe to use pixmaps outside the GUI thread"）で、
  環境によっては黙って空の pixmap になる。
- 対策: **ワーカーは `QImage` を返す**（`ready = Signal(str, QImage)`）。
  `QPixmap.fromImage()` は GUI スレッドの `ThumbnailManager._on_ready` で行う。
  cv2 経路の `QImage(numpy.data, …)` は **必ず `.copy()`**（バッファが消える）。

### 2. OneDrive を «全部オンラインのみ» と誤判定していた
- r83 の `is_online_only()` は **ファイル属性だけ**で判定していた。
  OneDrive の «ファイル オンデマンド» が有効なフォルダでは、
  **ダウンロード済みのファイルにも RECALL_ON_DATA_ACCESS が残る**ため、
  OneDrive 配下のサムネイルが全滅していた（ユーザー報告 2026-09-24）。
- 対策: `core/cloud_state.local_bytes()` を追加。`GetCompressedFileSizeW` は
  **ファイルを開かない＝ハイドレートを起こさない**ので、これで実体サイズを見る。
  * OFFLINE 属性 → オンラインのみ（サイズによらず）
  * RECALL 系のみ → 実体 0 バイトの時だけオンラインのみ
  * 取得不能 → 安全側（触らない）
- 教訓: **クラウド判定を属性だけで決めない**。属性は «プレースホルダである»
  ことしか示さず、«中身が無い» ことは示さない。

- 切り分け用に `MFM_DEBUG=1` で `mfm_debug.log` に `thumb: skip(cloud …)` /
  `thumb: load failed …` / `thumb: error …` が出る。
- 回帰テスト: tests/offscreen/test_thumbnail_pipeline.py（ワーカーの戻り値型／
  スレッド経路で実際に絵が届く／日本語パス／クラウド判定の 3 パターン）

## サムネイル表示への «切り替え» が効かない（r100、2026-09-24）

- 症状: カラムヘッダの ▦（リスト⇄サムネイル）を押しても切り替わらない。
- 原因: r85 で追加した表示サイズのスライダー `_ColumnSizePopup` を
  **`Qt.Popup` で出していた**。Qt::Popup は表示された瞬間に **マウスを grab** し、
  «ポップアップの外側のクリック» はポップアップを閉じるだけで消費される。
  このスライダーは «▦ にマウスオーバーすると出る» 仕様なので、
  ボタンを押すには必ずホバーを経由する＝**クリックが常に奪われる**。
- 対策: `Qt.Tool | FramelessWindowHint | WindowStaysOnTopHint |
  WindowDoesNotAcceptFocus` ＋ `WA_ShowWithoutActivating`。
  grab せずに出て、スライダーの操作性はそのまま。
  （判定は `windowFlags() & Qt.Popup` ではなく **`windowType()`** で見ること。
  Qt.Tool は内部的に Popup|Dialog のビットを含むため）
- `_set_column_view_mode` の `except Exception: pass` もログ出しに変更（r96 の教訓）。
- 回帰テスト: tests/offscreen/test_view_toggle_click.py
  （**実際のホバー→クリック**で切り替わること。従来テストは
  `_set_column_view_mode()` を直接呼んでいたのでこの不具合を素通りしていた）
- 教訓: **UI 操作のテストは «実際の入力経路» で行う**。内部メソッドを直接
  呼ぶテストは «配線が切れている» 種類の不具合を検出できない。

### テストの不安定さ（同時に対処）
- カラム生成は非同期なので、固定待ち時間 + `find_item` は «混んでいる時だけ
  落ちる» テストになっていた。`_common.find_item_wait(b, name)` を追加し、
  項目が現れるまで待つようにして全テストを置き換えた。

## バッチリネームを右クリックから（r101、2026-09-25）

- 右クリックメニューに「✏✏ バッチリネーム...」を追加（「名前変更」の直下）。
- **対象は右クリックした時点の選択をそのまま渡す**。
  `BrowserPanel.batch_rename_requested = Signal(list)` →
  `BrowserArea` が中継 → `MainWindow._open_batch_rename(paths)`。
- `_open_batch_rename` は **paths を渡された時は選択を拾い直さない**。
  従来はツールメニュー専用で必ず `_get_selected_paths()` を引き直しており、
  右クリックした対象と食い違う余地があった。paths が空の時だけ
  «現在の選択 → 無ければ現在地の全ファイル» の従来動作に落ちる
  （ツールメニュー経由は `triggered(bool)` が渡るが、falsy なので同じ扱い）。
- 回帰テスト: tests/offscreen/test_batch_rename_menu.py
  （メニュー項目の存在／選択がそのまま渡ること／単一選択／ダイアログが拾い直さないこと）

## サムネイルが無い項目が豆粒／サイズスライダーは «全体» へ（r102、2026-09-25）

### フォルダ・README.md 等がセルの中で小さい
- 原因: `QIcon.pixmap(w, h)` は **«要求サイズ以下の最大» しか返さず、足りなくても
  拡大しない**。Windows のシェルアイコンは 16/32/48 しか持たないことが多く、
  96px のセルに 16px や 48px のまま描かれていた（画像ファイルは生成した
  サムネイルなので正しい大きさ＝並べると違いが目立つ）。
- 対策: `ThumbnailDelegate._fallback_pixmap` で `availableSizes()` の最大を
  取り出してから `_fit()` でセルの一辺へ合わせる（**拡大もする**）。
  アスペクト比は維持（`KeepAspectRatio`）。

### 表示サイズのスライダーは «全体» に効く
- ユーザー指示により、スライダーは «全体的な表示サイズ» を変えるものに変更。
  `CappedColumnView.set_item_size_all(px, mode)` … **同じモードの全カラム**へ
  一括適用し、設定にも 1 回だけ保存（新しく開くカラムにも効く）。
- カラム以外へは `set_item_size_callback` → `BrowserPanel._apply_item_size_to_views`
  で配る（独立サムネビュー・平坦カラム）。
- 出し方は従来どおり **▦ ボタンへのマウスオーバー**（r100 で grab 問題を解消済み）。
  ラベルを「▦ 全体」にして «そのカラムだけではない» ことを示す。
- 回帰テスト: tests/offscreen/test_icon_fill_cell.py（代替アイコンがセルいっぱい／
  16px アイコンの拡大／アスペクト比／全カラム一括＋保存＋サムネビューへの伝播）、
  test_view_toggle_click.py に «ホバーでスライダーが実際に出る» 検証を追加。

## アイコンだけ小さい／スライダーが出ない（r103、2026-09-25）

### アイコンだけ «見た目» が小さい（高 DPI）
- r102 で «拡大したピクスマップ» を作るところまでは直したが、実機ではまだ小さい。
- 原因: **描画を «ピクセル数» で指定していた**。
  `painter.drawPixmap(x, y, pm)` は pm を **デバイスピクセル ÷ devicePixelRatio**
  の大きさで描く。シェルのアイコンは DPR が 1 でないことがあり、
  こちらが `side` ピクセルに拡大しても実際には `side / DPR` の大きさで描かれる。
  生成したサムネイル（QImage 由来 = DPR 1）だけが正しい大きさになり、
  «画像は大きいのにアイコンだけ豆粒» に見えていた。
- 対策: **描画先の矩形を渡す** `painter.drawPixmap(QRect, pm)`。
  論理サイズ（`pm.size() / devicePixelRatio`）でアスペクト比を保ったまま
  `side` に合わせるので、アイコンの元サイズが 16 でも 48 でも 256 でも同じ
  大きさで並ぶ。**アイコンごとの設定値は不要**。
- 教訓: **QPainter へサイズを «数値» で渡さない**。高 DPI では
  `pixmap.width()` はデバイスピクセルで、座標系（論理ピクセル）と一致しない。

### 表示サイズのスライダーが出ない（ツールチップしか出ない）
- 原因: r100 で Qt.Popup をやめた際に **別ウィンドウ（Qt.Tool）**にしたが、
  トップレベルウィンドウは表示順・アクティブウィンドウ判定・ツールチップとの
  重なりといったウィンドウマネージャ側の都合を受け、実機では出ていなかった。
  さらに ▦ の長いツールチップが **スライダーと同じ位置（ボタンの真下）**に
  出て隠していた。
- 対策: **カラムビューの子ウィジェット**にする（トップレベルをやめる）。
  必ず描画され、grab もしない。位置は親の座標系でボタン直下、はみ出す分は
  内側へ寄せる。▦ のツールチップは短くした。
- 回帰テスト: test_view_toggle_click.py に «子ウィジェットであること／ホバーで
  実際に表示されること／親からはみ出していないこと／動かすとサイズが変わること»、
  test_icon_fill_cell.py に «実際に描かれた絵の高さ» のピクセル検証を追加。

## 表示サイズはリスト／グリッドで別々に覚える（r104、2026-09-25）

- 保存自体は r85 から «モードごとの別キー»（`column_icon_size_list` /
  `column_icon_size_thumb`）で行っており、値の往復は正しかった。
- 壊れていたのは **スライダーを出したまま ▦ でモードを切り替えた時**。
  `_ColumnSizePopup` はレンジ（リスト 16-128／グリッド 48-256）と値を
  `show_for()` の時点で固定するため、モードが変わってもそのまま残る。
  その状態でスライダーを動かすと、**リストの値（例 40px）がグリッドの
  セル寸法として適用・保存**され、«切り替えるたびにグリッドが小さい»
  になっていた（ユーザー報告 2026-09-25）。
- 対策:
  1. `show_for()` で `self._mode` を記録する。
  2. `_set_column_view_mode()` は、開いているスライダーが同じカラムのものなら
     `show_for()` を呼び直して **新しいモードのレンジ／保存値へ読み直す**。
  3. `_on_value()` は `self._mode` と現在のモードがズレていたら適用せず
     読み直すだけにする（取りこぼし防止の二重化）。
- 平坦カラムは常にリスト表示なので、`_apply_item_size_to_views` は
  **リストの寸法の時だけ** そこへ反映する（グリッドの寸法を流し込まない）。
- 回帰テスト: tests/offscreen/test_item_size_per_mode.py
  （モード往復／新しいカラムへの適用／**スライダーを出したままのモード切替**）
- 教訓: **モードで意味が変わる UI は、モード変更時に必ず «読み直す»**。
  開きっぱなしのコントロールは前のモードの文脈を持ったまま残る。

## カラム幅を掴む帯は «必ず縦スクロールバーより左»（r105、2026-09-30）

- 症状: 右端のカラムで幅を変えようとすると、掴む場所が縦スクロールバーに
  隠れて掴みにくい。掴み損ねると誤って幅が変わり、戻すのが難しい。
- 原因: `_ColumnResizeHandle` を **カラム右辺をまたぐ帯**（`x = 右辺 - 幅/2`）
  として配置していた。右半分が隣カラムの左端に乗る設計だが、
  **一番右のカラムには隣が無い**ので、残った左半分がそのまま
  縦スクロールバーと重なっていた。
- 対策: `x = カラム右端 - スクロールバー確保幅 - 帯幅` に変更し、
  帯全体がスクロールバーの左に収まるようにした。
  * 幅は `_scrollbar_extent(view)` で **表示の有無によらず確保幅**で計算する。
    可視状態で計算すると «スクロールバーが出た瞬間に掴む位置がずれる»。
  * 罫線は帯の右端へ寄せ、«この帯の左側が掴める» と分かる見た目にした。
- 回帰テスト: tests/offscreen/test_resize_handle_pos.py
  （全カラムで帯の右端 ≤ スクロールバー左端／カラム外へ出ない／
  スクロールバーの表示有無で位置が動かない／幅変更が生きている）

## プリセット設定で Enter を押すと «新規» が立ち上がる（r106、2026-10-01）

- 症状: プリセット名を直して Enter → «新しいプリセット» ダイアログが開く。
  名前欄に限らず **ダイアログ内のどこで Enter を押しても** 起きる。
- 原因: **QDialog 内の QPushButton は既定で autoDefault=True**。Enter は
  «フォーカス連鎖で最初の autoDefault ボタン» を押すので、最初に作られた
  「✚ 新規」が発火していた。r63 で «保存して閉じる»／«キャンセル» だけ
  `setAutoDefault(False)` にしたが、**残りのボタンは手つかず**だった。
- 対策（ui/quick_nav_editor.py・ui/preset_editor.py）:
  1. `_disable_auto_default()` … `findChildren(QPushButton)` で
     **全ボタンの autoDefault / default を切る**。ボタンを足すたびに
     個別対応が要らないよう、UI 構築後に一括で行う。
  2. ダイアログの `keyPressEvent` で Return/Enter を accept して握り潰す。
     各入力欄の `returnPressed`（改名など）はそのまま効く
     （QLineEdit は returnPressed を出した後に ignore するため）。
- 教訓: **QDialog にボタンを置いたら autoDefault を切る**。
  意図した既定ボタンがある時だけ `setDefault(True)` を明示する
  （save_dialog / conflict_dialog / settings_dialog は意図的に OK を既定に
  しているのでそのまま）。
- 回帰テスト: tests/offscreen/test_dialog_enter_keys.py
  （autoDefault が残っていない／名前欄の Enter は改名だけ／他のウィジェットでも
  新規が出ない／ダイアログが閉じない／✚ 新規ボタン自体は動く）

## 「Perforce で最新取得中にフリーズ」の正体（r107、2026-10-01）

- 結論: **p4 とは無関係ではなく、Manager 側の不具合**。しかも r98 で入れた
  «現在地の消失監視» が原因。mfm_freeze.log で確定:

      === UIフリーズ検出 (16.7秒停止) メインスレッド ===
        ... browser_panel.py, line 5780, in _fallback_to_existing_ancestor
            if not cur or os.path.isdir(cur):

- 原因: 4 秒ごとのタイマーが **UI スレッドで `os.path.isdir()`** を呼んでいた。
  ネットワーク共有や p4 sync 中のワークスペースでは stat が十数秒返らず、
  その間 UI が完全に止まる（停止時間が 6.7→11.7→16.7 秒と伸びるのは、
  同じ stat を待ち続けているため）。
- 対策:
  * 判定を **ワーカースレッド**へ（`_check_current_gone_async`）。
    結果は Signal `_gone_result(調べたパス, 移動先)` で UI スレッドへ戻す。
  * 判定中に別の場所へ移動していたら結果を捨てる（古い結果で飛ばさない）。
  * 同時実行は 1 本だけ（`_gone_probe_busy`）、間隔は 4 秒 → 8 秒。
  * 移動／コピー完了後の呼び出しも非同期経路に統一。
- `_fallback_to_existing_ancestor()`（同期版）は残すが **タイマーからは呼ばない**。
- 教訓: **UI スレッドで `os.path.isdir` / `os.stat` / `listdir` を呼ばない**。
  ローカルでは一瞬でも、ネットワーク先・VCS 同期中は平気で十数秒かかる。
  定期実行するものは特に危険（止まるたびに積み上がる）。
- 回帰テスト: tests/offscreen/test_gone_watch_async.py
  （stat が 2 秒返らない状況で UI が止まらない／別スレッドで判定している／
  消失時は従来どおり一番近い親へ移動／古い結果を無視）

### 残りのフリーズ源の実測（r108 で整理）
最初の集計で最多だった `eventFilter`（704 件）は **旧パス
`C:\Users\owner\Projects\...` 時代の記録**で、現行ビルドのものではなかった。
**集計は必ず現行パスで絞る**こと:

    awk '/UIフリーズ検出/{keep=0} /D:\\Claude/{keep=1} keep&&/browser_panel/{print}' \
      mfm_freeze.log | grep -o "line [0-9]*, in [a-zA-Z_]*" | sort | uniq -c | sort -rn

現行ビルドの内訳は以下だけだった:
  * `_fallback_to_existing_ancestor` 44 件 … **r107 で解消**（本物のフリーズ）
  * `_start_multi_drag` / `eventFilter` 60 件 … **ドラッグ中の誤検出**（下記）
  * `paint` / `_timed` / `_on_probe_result` / `_clear_column_selection` 各数件

## フリーズ監視の誤検出を止める（r108、2026-10-01）

- Windows の D&D（OLE `DoDragDrop`）は **ドラッグ中ずっとこちらのスレッドを
  握ったまま戻らない**。フリーズ監視はこれを «停止» として記録するため、
  現行ビルドの記録の約半分がドラッグの誤検出で埋まっていた。
  これでは本物のフリーズが埋もれて調査にならない。
- 対策: «意図して UI スレッドを占有している区間» を宣言する仕組みを追加。
  * `mfm_blocking_begin(why) / mfm_blocking_end() / mfm_blocking_reason()`
    （入れ子対応。余分な end でも壊れない）
  * `_start_multi_drag` の `drag.exec()` を try/finally で囲む
  * `ui/main_window.py` の監視スレッドは `mfm_blocking_reason()` が立っている
    間は記録しない
- 併せて `_clear_column_selection` の `filePath()` を «追跡中フォルダがある時
  だけ» に限定（UI スレッドの I/O を 1 つ削減）。
- 回帰テスト: tests/offscreen/test_freeze_watchdog.py
  （宣言の入れ子・解除／ドラッグ中に宣言が立つ／ドラッグ後に解除される）
- 教訓: **監視ログは «本物だけ» が残るようにしておく**。誤検出を放置すると
  集計が意味を失い、次の調査で «最多の行» を追いかけて空振りする
  （実際、最多だった eventFilter 704 件は旧パス時代の記録だった）。

## モジュール分割（r110、2026-10-01）

6746 行あった `ui/browser_panel.py` を役割ごとに分けた。**コードは 1 行も
書き換えず、ブロック単位で移動**しただけ（挙動を変えないため）。

    ui/browser_util.py        338行  ログ・パス解決・アイコン提供・占有区間
    ui/browser_models.py      389行  FileFilterProxyModel
    ui/browser_delegates.py   478行  ThumbnailDelegate / StatusBadgeDelegate
    ui/browser_column_view.py 2623行 CappedColumnView と周辺ウィジェット
    ui/browser_panel.py       3127行 BrowserPanel 本体＋**全名前の再エクスポート**

- 依存は一方向（util → models/delegates → column_view → panel）。
  **逆向きの import は循環するので禁止**。
- `ui/browser_panel.py` は旧名を全て再エクスポートするので、
  **既存の `from ui.browser_panel import X` は変更不要**。
- ただし **差し替え（monkeypatch）は «コードが居るモジュール» に対して
  行う必要がある**。`bp.QDrag = Fake` は効かなくなり、
  `ui.browser_column_view.QDrag` を差し替える（テスト 2 本を修正済み）。
  再エクスポートは «読む» 側の互換は保つが «書き換える» 側は保たない。

## 握り潰しを «安全なまま» 可視化する（r112、2026-10-01）

- 本ツールには `except Exception: pass` が 249 箇所ある。UI 描画や破棄済み
  ウィジェットへの操作など、**落とす方が有害**な箇所が多く、設計自体は妥当。
  問題は **実装ミスまで隠す**こと。2026-09〜10 だけで «D&D が動かない»
  «連携バッジが更新されない» «ツールチップが出ない» の 3 件がここに隠れていた
  （いずれも NameError / AttributeError）。
- 対策: `core/diag.py` の `swallow(exc, where)` を通す。
  * 想定内（OSError・RuntimeError 等）→ 従来どおり黙殺
  * **実装ミスを示す型**（NameError / AttributeError / TypeError /
    ImportError / IndexError / KeyError / UnboundLocalError）→
    `~/mfm_debug.log` へ `BUG?:` 行を 1 回だけ記録
  * `swallow()` は **絶対に例外を投げない**（握り潰しの代わりだから）
  * 同じ場所は 1 回きり。ログが溢れない
- 適用: `except Exception:` ＋ `pass` だけの **204 箇所**を機械的に置換した。
  型を絞った except（`except OSError:` 等）は意図が明確なので触っていない。
- **新しく握り潰しを書く時も `_swallow(_e, "場所")` を使う**。素の `pass` は、
  そこで起きた実装ミスを永久に見えなくする。
- 回帰テスト: tests/offscreen/test_swallow_diag.py

## 開発・デバッグの約束事

- 起動: `run_dev.bat`（インストール済み最新Maya 2027→2023 の mayapy を自動選択。
  `set MFM_MAYA_VER=2024` で固定可。無ければ python）。デバッグは
  `$env:MFM_DEBUG=1` で `%USERPROFILE%\mfm_debug.log` に出力。
- Maya内モード: `run_in_maya.py` をスクリプトエディタで exec（`MFM_RELOAD=True`
  でモジュール再読込）。起動時に mfm_startup.log へ `env[...]` 行
  （Python/Qt/PySide/Maya バージョン・実行ファイル）が出る。バージョン差の
  切り分けはまずこの行を見る。
- **OS ネイティブのファイル/フォルダダイアログを使わない**（r64）。Windows の
  ネイティブダイアログはシェル拡張（TortoiseSVN/Git、Perforce P4EXP 等）を
  プロセス内へ読み込み、mayapy/EXE では QFileDialog.getExistingDirectory の
  中でプロセスごと落ちた（mfm_freeze.log で _browse 内 4 秒停止→消滅を確認）。
  スタンドアロンは main.py で AA_DontUseNativeDialogs、加えて各呼び出しでも
  `QFileDialog.DontUseNativeDialog` を明示（Maya 内モードでも効く）。
  faulthandler.enable も同ログに設定済み（クラッシュ時にスタックが残る）。
- **.bat ファイルは ASCII のみ**（日本語コメント禁止）。cmd.exe はバッチを
  コンソールのコードページ（日本語Windows=CP932）で読むため、UTF-8 の日本語が
  化けて壊れたコマンドとして実行される（2026-09 実機で発生）。
- **ビルドマーカー**: `ui/browser_panel.py` の BrowserPanel init ログに `rNN` マーカー、
  ファイル末尾に EOF センチネルログがある。ログに **initマーカーとEOF行の両方**が
  揃って初めて「最新かつ末尾欠損なし」と判断できる。コード変更時は両方を更新すること。
- **ファイル同期の罠**: Claude の Edit がユーザーPCの実ファイルに反映されるまで遅延があり、
  末尾切り詰めの中間状態でも起動できてしまう（コメント境界で切れると構文エラーにならない）。
  「こちらで動くのに実機で動かない」時は、まずログのマーカー/EOF行で実機コードの鮮度を疑う。
  bashマウント（/sessions/.../mnt/）も同様に遅延・切り詰めが起きる。
- **検証手順**: bashサンドボックスに PySide6 + /tmp/qtlibs（apt-get download で
  libegl1等を展開。apt は非 root なので `-o Dir::State=/tmp/apt/state
  -o Dir::Cache=/tmp/apt/cache -o Dir::Etc::sourceparts=/dev/null` で update
  してから download。2026-09-16 に復旧を確認、17/17 PASS）を用意し、/tmp/pkg にコードを複製してオフスクリーン
  （QT_QPA_PLATFORM=offscreen）で QTest によるクリック再現＋ピクセル検証を行う。
  マウントが切り詰められている場合は head + 既知の末尾を継ぎ足して再構成する。
- **回帰スイート（リポジトリ内、r49〜）**: `python tests/run_offscreen.py`。
  tests/offscreen/test_*.py（ナビ/選択/ファイルクリック、パス/リンク、右クリック、
  スピナー/リサイズ/自己修復、プリセット/連携、全ダイアログのスモーク、
  平坦深さスイッチ、Perforce バッジ方針＋fstat 解析、水平スクロール整合性）。
  **コードを変えたら必ず全件PASSを確認してから出す**。/tmp のテストは
  ワイプで消えるので、テストは必ずリポジトリ側に置く。
  * **mayapy で回す時の差異（r62 で吸収済み）**: Maya 同梱 PySide6 には
    QtTest が無い → `_common.QTest` シム（QMouseEvent/QKeyEvent 直送）を使う
    （テストは `from _common import QTest`。PySide6.QtTest を直接 import しない）。
    フォントディレクトリが無くグリフ幅が異常 → 幅判定は QFontDatabase.families()
    が空なら skip。言語は `_common` で ui_language=ja に固定。p4/svn の有無は
    環境依存なので「未検出なら False」の形で断定しない。
  * **【事故】テストが本番設定を破壊した（2026-09-11）**: テストの
    SettingsManager が ~/.maya_file_manager を共有しており、
    test_presets_and_integrations の save_quick_nav_presets がユーザーの
    プリセットを上書き、test_smoke_dialogs の click_all が実ブックマークに
    フォルダ追加＋並び替え確定、履歴に一時パスを追加した。対策（r63）:
    `_common.py` が MFM_SETTINGS_ROOT=一時フォルダを設定し、SettingsManager.
    _resolve_root がそれを最優先で使う。加えて settings.json は保存時に
    1日1回 backups/settings_YYYYMMDD.json へ退避（14世代）。**テストから
    本番設定へ書く経路を二度と作らない**（新しいテストは必ず _common 経由）。
  * **ダイアログの親はトップレベルウィンドウ**（r63）。プリセット行など
    独自 QSS を持つウィジェットを親にすると、その QSS がダイアログへカスケード
    して 24px 固定ボタン（▲▼）の内容領域が潰れる（実機で再発した）。
  * **サンドボックス停止時の代替経路（r60）**: ユーザーが `run_tests.bat` を
    ダブルクリック（mayapy でオフスクリーン実行）→ 結果が <repo>/mfm_tests.log
    に書かれる → Claude は Read ツールでそれを読んで判定する。2026-09-08 の
    Windows 更新で Cowork のサンドボックスがユーザーファイルへ到達できなく
    なる既知障害があり（bash が "Plan9 share not mounted" で失敗）、その間は
    この経路で検証する。未検証のまま「実機で確認して」と丸投げしない。
- **シグナル接続の罠**: `clicked/triggered(bool)` をデフォルト引数付きメソッドに
  直結すると checked(bool) が先頭引数へ流れ込む（`QLineEdit(False)` で落ちた
  実例）。必ず `lambda _c=False: self.method()` で遮断する。tests の
  横断検査（ast で必須0＋デフォルト有りの直結を検出）を review 時に回す。
- **テーマの小型ボタン**: グローバルQSSの QToolButton padding は 1px 3px。
  4px 8px にすると 24px 固定の ▲▼✕ の内容領域が潰れて文字が消える（r30の副作用）。

## アーキテクチャ要点（選択まわりの落とし穴）

- QColumnView は**各カラムに独立した選択モデルを複製**し、カラム再構築時に本体
  （self.selectionModel()）から種を撒く。選択操作は必ず
  `_all_selection_models()` 全体へ同期する（片側だけだと再構築で復活/消失する）。
- パンくず（上位階層の選択表示）は `_restore_tracked_selection()` が current の
  祖先チェーンを全モデルへ焼き込むことで再構築に耐えている。
- 複数選択中の「冗長な子カラム」抑制は **current をクリック項目の親へ退避**
  （_park_current_at_parent）。幅0に畳む方式は内部幅テーブルを汚染して
  カラム消失を起こすため**禁止**。
- `_multi_select` 内の処理順は重要: 選択→sync→park→スナップショット復元→flat要求。
  順序を崩すと「Ctrlで解除できない」等が再発する（コメント参照）。
- 修飾クリックのプレスを consume したら **リリースも consume**（_swallow_release）。
  素通しするとネイティブclickedが発火して選択が壊れる。
- ヘッダの平坦ボタンは **toggled シグナル**（clickedは実機で不達の事例あり）。
- 平坦ビューの自動表示は**複数選択(2件以上)のみ**。単一は平坦ボタンで明示的に。
- **ファイルのクリックでは本体 current を動かさない**（r46）。QColumnView に
  ファイルを current として渡すとプレビュー列が生成されカラム全体が左へ
  スクロールする（使いづらいとの指摘）。ファイルはプレスを消費して選択だけ
  単一化し（_prune_selection_to_single）、_pending_multi_drag(file_click) 経路で
  リリース時にクリック相当（clicked.emit）を再現。release-click で
  top.setCurrentIndex するのはフォルダのみ。判定は _is_dir_index（I/Oなし）。
  * **ダブルクリックは eventFilter で自前発火**（r66）。プレスを自前消費すると
    QAbstractItemView::pressedIndex が更新されず、Qt の mouseDoubleClickEvent
    は「pressedIndex == index」の時しか doubleClicked/activated を出さない
    （それ以外は通常プレス扱い）。結果「1回目のダブルクリックが効かず2回目で
    開く」になっていた。MouseButtonDblClick を捕まえて activated.emit し、
    Qt 側の処理は消費（リリースも _swallow_release）。
  * **r57 で経路非依存の保険を追加**: CappedColumnView.scrollTo をオーバーライドし、
    index がファイルなら横スクロールを行わず所属列の縦スクロールだけにする
    （Qt は currentChanged 等から仮想呼び出しするため、どの経路で current が
    ファイルになっても列は滑らない）。さらにファイルクリック後 1.2 秒の
    hbar.valueChanged を呼び出し元スタック付きで <repo>/mfm_ui.log に常時記録
    （note_file_click）。「ファイルクリックで滑る」報告はまずこのログを見る。

## デザイン方針（r30〜）

- **Mercury 準拠（r82、2026-09-17 にユーザー指示で AuthKit から変更）**
  （https://styles.refero.design/style/3172cd4d-118a-4a16-a259-6b634d32322e）。
  単一の真実は config/design_tokens.json（ref層に Mercury の9色）。
  QSSは core/theme_engine.py が生成。**色をQSSやUIコードへ直書きしない**。
  ウィジェット個別の setStyleSheet は必ず `theme_engine.qss_vars()` を
  `"%(role)s"` で差し込む（ベタ書きは test_theme_mercury.py が落とす）。
- 要点: ダークは面が #171721（Onyx）→ #1e1e2a（Graphite・カード）→ #272735
  （Obsidian）の3段、文字は #ededf3（Ivory）→ #c3c3cc（Ash）→ #70707d（Slate）/
  **有彩色は Cobalt #5266eb のみ**（CTA と選択ハイライト）/ **影は使わない**
  （段差は面の明度だけで作る）/ ボタン・入力・ナビはピル、カードは12px /
  **font-weight は 500 まで**（600・700・bold は使わない）/
  **本文に純白 #ffffff を使わない**（Cobalt 塗りの上の文字だけが純白）。
- **ライトは Mercury 原典に無く、当方が同じ原則で導出**（単一アクセント・影なし・
  純白は塗り上の文字だけ）。面は #ffffff（主役）→ #ededf3 → #e2e3ed の3段で
  ダークと逆向きに同じ階層を作る。
- **密度は Mercury の spacious を採らず、既存のコンパクトのまま**（常駐の
  Millerカラム型ファイルブラウザに 72px のセクション余白は不適切）。採用したのは
  配色・フラットな段差・ピル形状・中庸ウェイト。
- 意味を持つ信号色（P4/クラウドのバッジ、成功/警告/エラー）は «単一アクセント則の
  例外»。ただし彩度を落として sys.status_* トークンから引く（テーマ追従）。
- エリア別アクセントは design_tokens.json の `area_accents`（6色、低彩度で
  同じトーンの色相違い。機能上の区別のため）。ダーク/ライト別に定義。
- **テーマ切替は設定「テーマ」→ 再起動**（r82 で main.py が設定を読むようになった。
  それまで mode="dark" 固定で設定が効いていなかった）。ウィジェット個別の
  スタイルは生成時に qss_vars() を読むため、実行中の動的切替はしない。
  **Maya 内起動では apply_theme を呼ばない**（Maya 全体の QApplication に
  stylesheet/palette を当てると Maya の見た目を壊す）。代わりに
  `theme_engine.set_mode()` でモードだけ設定に合わせ、個別スタイルを追従させる。
- **視認性の階層（r56、ユーザー指示）**: ブラウジング > ブックマーク/履歴/
  プリセット > ヘッダ（メニューバー含む）。theme_engine が objectName で面を
  分ける: `#mfmBrowserPanel`（plane_focus=一段明るい面＋強いヘアライン枠、
  内側余白4px。WA_StyledBackground 必須）/ `#mfmSidePanel` `#mfmPresetBar`
  （地の色・枠なし・11px・控えめな文字）/ `QMenuBar` `#mfmHeaderCorner`
  （最も沈めた plane_header・薄い文字・コンボは透明）。起動CTAは
  `#mfmLaunchCta`（塗り潰しではなく violet ティント＋枠）。色は全て
  design_tokens.json の plane_* / cta_* トークン。新しい面を足す時は
  この3段のどれかに必ず属させる（4段目を作らない）。
- 「重複フォルダ検出」パネルは **廃止**（r56）。複数選択＋平坦/共通子フォルダ
  表示が上位互換で、深度6の再帰スキャンはリンク越しI/Oの温床だったため。
  ui/duplicate_folder_panel.py は削除（復活させない）。

## UI構成

- **プリセット選択はエリア毎に独立**（QuickNavBar._active）。🔗リンクボタン
  （歯車の左、ON=明るい）ONの時だけ変更を全エリアへ伝播。エディタ保存後の
  refresh_all は «各エリアの選択を変えずに» ボタンだけ再構築する。
  エリアの選択プリセットは browser_areas_state に保存（キー "preset"）。
- **右クリックメニューの対象決定はExplorer準拠**（_show_context_menu）:
  カーソル直下の項目が未選択ならその項目のみを対象、選択済みなら選択全体
  （ただしパンくず祖先は _drop_ancestor_dirs で除外）。本体選択リストを
  そのまま使うと「.ma右クリックでMayaメニューが出ない」が再発する。
  * **右クリックのプレスは選択を壊さない**（r55、eventFilter 冒頭で
    RightButton を分岐）。選択済み項目→何もしない／未選択→その項目だけ選択
    （current は動かさない）／空白→カラムの選択解除。プレス・リリースとも消費
    するが ContextMenu イベントは QWidgetWindow が accept 状態に関係なく送る
    （Windows はリリース時）ため、メニューは出る。従来は左クリックと同じ
    「プレス消費→リリースで単一選択化」に入り、メニュー表示前に複数選択が
    解けていた。QTest は ContextMenu を合成しないので、テストでは
    QContextMenuEvent を手動送信する（test_context_menu.py）。
  * **キーボード操作（Ctrl+C/X/V・Delete）の対象は _operation_targets()**（r59）。
    本体選択 _get_selected_paths() はパンくず（祖先フォルダ）を含むため、
    そのまま使うと Delete で祖先フォルダごと削除対象になる（実際にそうなって
    いた）。右クリックと同じ _drop_ancestor_dirs で除外する。削除確認は必ず
    対象名を列挙する。クリップボードは Explorer 互換（text/uri-list＋
    Preferred DropEffect。切り取り＝Move、貼り付け先＝単一フォルダ選択 or 現在地）。
    **r72: フォーカスが平坦ビュー／共通子フォルダカラムにある時は、その
    «最下層の選択物» だけ**（_deep_view_targets。平坦ビューで Delete を押すと
    元の上位フォルダが対象になっていた指摘）。平坦ビューは静的モデルなので
    ファイル操作（_run_file_op）完了後に _refresh_flat_view で作り直す
    （放置すると「削除できない」ように見える）。平坦ビューの表示時に
    各ファイルのフォルダの連携状態も要求する（_request_flat_integration_status、
    右クリックに Perforce を出すため）。
  * **インライン名前変更の Enter は _KeyFilter で消費**（r72）。returnPressed 任せ
    だとキーが QListView へ伝播して activated が発火し、パス欄がフォルダへ戻る
    ／ファイルが開く（テストで確定）。
  * **右クリックの連携メニューは1回目から出す**（r72）: manager._do_status は
    root_for（ms）で確定したプロバイダを fetch_status（p4 fstat、秒）の前に
    _dir_providers へ公開し、actions_for は未取得なら最大 0.4 秒だけ
    プロバイダ確定を待つ（状態は待たない）。**actions_for は複数フォルダ
    対応**: 従来は paths[0] のフォルダに全ファイルの basename を結合していて、
    平坦ビュー（複数階層）の Revert が存在しないパスで失敗した。フォルダごとに
    実体パスへ変換し (プロバイダ, root) 単位でまとめて渡す。実行後の状態再取得も
    対象ファイルのフォルダ全部（_run_integration_action の paths）。
  * **名前変更は F2 でカラム内インライン編集**（r61/r62、_rename_inline →
    _open_inline_editor）。**Qt の view.edit() は使わない**: QFileSystemModel::flags
    は書き込み権限の無いファイル（Perforce 同期の read-only）に ItemIsEditable を
    付けず、edit() が黙って失敗する（実機で「F2 が効かない」原因）。自前の
    QLineEdit（#mfmInlineRename）を項目矩形に重ね、確定は os.rename＋RenameOp。
    Enter/フォーカス喪失=確定、Esc=取消、拡張子を除いて初期選択。current は
    動かさない（列スライド防止）。カラム外（平坦ビュー等）はダイアログへ退避。
  * **名前変更の入口（r67）**: F2 ／ 右クリック ／ «ゆっくり2回クリック»
    （単一選択済み項目の再クリック → ダブルクリック間隔+80ms 後に開始。
    ダブルクリック・次のプレスで取消。_schedule_reclick_rename）。エディタ内
    Tab/Shift+Tab は確定して同じカラムの次/前の項目へ（隣のパスを先に取り、
    確定後 60ms で _rename_inline(path)）。
  * **新規フォルダ Ctrl+Shift+N / 新規テキスト Ctrl+Shift+T**（r73、
    _create_new_item）: 作成先は «アクティブなカラム»（フォーカスのある子
    QListView の rootIndex → 無ければ _paste_target_dir の規則）。名前は
    Explorer 風「新しいフォルダ (2)」で衝突回避。作成後は QFileSystemModel に
    項目が現れるのを 100ms×25 回まで待ってインライン名前変更へ
    （_rename_when_visible。出なければダイアログ）。空白右クリックにも同項目。
  * **複製 Ctrl+D**（r67、ui/duplicate_dialog.py）: 同じ場所へ «元名_Copy»
    （重複時 _Copy2…）。ダイアログで名前指定 or Replace モード（Search/Replace、
    「サブフォルダ以下にも適用」でフォルダ中身も改名）。実行は
    file_operations.duplicate_items をワーカー＋進捗で、Undo は CopyOp。
  * **カラムのプレスで明示的に setFocus(MouseFocusReason)**（r62）。QColumnView
    は current の親カラム以外を NoFocus にするため、末尾カラムをクリックしても
    フォーカスが移らず、BrowserPanel の WidgetWithChildrenShortcut（F2/Ctrl+Z/
    Ctrl+C…）が効かなかった。
  * **Undo/Redo**（r62、core/undo_stack.py）: 名前変更/移動/コピー/削除。
    Ctrl+Z / Ctrl+Y（BrowserPanel の QAction）、編集メニューは表示用（同じ
    ショートカットを付けると «あいまい» で両方死ぬ）。削除は同一ボリュームの
    作業ごみ箱 `<volume>\.mfm_trash\<id>\` へ rename で移し（Undo=戻す）、
    記録がスタックから消える時（上限50/Redo分岐破棄/終了）に OS のごみ箱へ
    （recycle: SHFileOperationW FOF_ALLOWUNDO → send2trash → 恒久削除）。
    作業ごみ箱へ移せない時は直接ごみ箱（Undo 不可）。部分失敗の再実行を許す
    ため _safe_rename は「既に目的の状態」を成功扱いにする。
  * **長時間になり得るファイル操作は必ず _run_file_op 経由**（r62）: ワーカー
    スレッド＋QProgressDialog（0.4秒超で表示、キャンセル付き、WindowModal）。
    コピー/移動/削除/Undo/Redo が対象。キャンセルしても «完了した分» は Undo
    記録に残す（copy_items/move_items/delete_to_work_trash の results/pairs
    引数）。UI スレッドで shutil を回す新規コードを書かない。
  * **空フォルダでも子カラムを出す**（r62、FileFilterProxyModel.hasChildren）。
    QSortFilterProxyModel はフェッチ済みで行0なら False を返し、QColumnView は
    ヘッダ無しの «プレビュー列» を出すだけだった。フォルダなら常に True
    （ドライブ階層＝親が無効な行は触らない）。
  * **削除は読み取り専用を外してから**（r61、file_operations.delete_items）。
    Perforce/SVN の同期ファイルは read-only で unlink が PermissionError になり
    「削除に失敗」になっていた。失敗は "path — 理由" で返す。
  * メニューは複数選択で出す。単一限定は「Maya で開く／関連付けで開く／
    エクスプローラーで表示／名前変更／プロパティ」のみ。Maya へのインポート／
    リファレンスは複数対応（D&D と同じ一括経路 _dnd_callback、リファレンスは
    デフォルトNamespace でダイアログ無し）。
  * カーソル直下の解決に **QColumnView.indexAt() を使ってはならない**（各カラムの
    ヘッダ分ビューポートマージン52pxを考慮せず1〜2行ズレる。r34の失敗）。
    グローバル座標を含むカラム（子QListView）のビューポートを探し、その
    座標系で indexAt する（r40）。QCursor/widgetAt はオフスクリーンで動かない
    ため使わない。
- **ヘッダ内ウィジェットはグローバルQSSの min-height/padding を必ず打ち消す**。
  QSSの max-height はコンテンツ高に効くため padding も明示0にしないと
  ヘッダ予約高(_COL_HEADER_H=52)を超えて先頭項目に被る（2026-09 実例）。

- エリア左余白は BrowserArea の body レイアウトで一括確保する（プリセット行
  だけにマージンを付けると下のパネル群と左端が揃わず見辛い、と指摘あり）。
- **カラム毎の読み込み中スピナー**（r24）: CappedColumnView が150msポーリング。
  判定は「canFetchMore（fetch未開始）」OR「directoryLoaded 未受信（列挙中、
  note_dir_loaded のセッション内記録で判定）」＋60秒安全弁。
  * r22の失敗: canFetchMore は fetch開始直後にFalseへ変わるため単独では一瞬も
    表示されない。
  * r23の失敗: rowCount==0 条件を付けたが、起動時のパス復元では祖先チェーンが
    列挙完了前にノード化され rowCount==1 になるため表示されなかった。
  * rowCount は列挙完了の判定に使ってはならない。逆に消灯を directoryLoaded
    «再発火» に依存するのも禁止（pip版Qtの罠）→ セッション内記録なら安全。
- **カラム幅の変更**（r42〜r45）: Qt標準の QColumnViewGrip は setCornerWidget
  実装で「両スクロールバー表示時の角」にしか現れず実用不可 → 自前の
  _ColumnResizeHandle（ビューポートの子、カラム境界をまたぐ12px、ドラッグで
  幅変更、ダブルクリックで内容に合わせる）。幅は setColumnWidths で保持し、
  設定 "column_widths" に保存・復元。既定幅は300（従来256）。
  * setColumnWidths は右側カラムを再配置しない。再配置は **必ず Qt の doLayout**
    （self.resizeEvent(QResizeEvent) を直接呼ぶ＝_relayout_columns）に任せる。
    自前で setGeometry すると非表示カラム/スクロールオフセットと食い違い、
    カラム群が右へずれて左に空白＋スクロール不能になった（r43の失敗、致命的）。
  * **レイアウト自己修復**（_check_column_layout_health、150ms毎）: 先頭カラムの
    x>2（左に空白）を検知したら hscroll を先頭へ戻し原点へ寄せて doLayout。
    原因を問わず「見えない階層ができる」状態を数百ms以内に解消する（保険）。
  * **真因（r54で確定・修正）**: QColumnView は「カラム x == 内部 offset ==
    -hbar.value」の三位一体で動く。setRootIndex（全カラム再構築）は offset を
    0 に戻すが、スクロールアニメーション中は updateScrollbars() が早期 return
    して hbar.value が残るため offset と value が恒久的にずれ、「末尾カラムが
    半端な位置で切れる／右に空白／最右端でも見えない階層」になる。
    対策: CappedColumnView.setRootIndex/setModel の直前に hbar.value を 0 へ
    （_reset_hscroll_for_rebuild）＋260/700/1300/2200ms 後に current 列を可視化
    （_ensure_current_column_visible、scrollTo は使わない。r71: 深いパスは
    260ms 時点で列が未生成のことがあり mayapy の回帰で右外に残った → 複数回。
    2回目以降は前回から hbar が動いていれば＝手動スクロールとみなし打ち切り）。幅変更後は
    _normalize_hscroll で過スクロール（右の空白）を解消して範囲再計算。
    **setRootIndex を直接呼ぶ新規コードを書かない**（必ずこの override 経由）。
  * **深度キャップ（column_max_depth のルート自動シフト）は完全廃止**。設定
    適用時に再有効化されると上記の再構築がクリック毎に走り致命傷になった。
    set_max_depth は値の保存のみ、設定ダイアログのスピンは非表示。
  * 回帰テスト: tests/offscreen/test_hscroll_consistency.py（連続クリック／
    アニメ中の navigate_to／戻る／幅変更で x・maximum・右端の整合を検証）。
- カラム内オーバーレイの「◀ 上の階層へ」ボタンは**廃止**（2026-09-03、
  使われず紛らわしいとの指摘）。アドレスバーの◀（戻る）は別物で存続。

- BrowserArea（ui/browser_area.py）= プリセット行＋ブックマーク/履歴＋BrowserPanel の
  1ユニット。MainWindow が縦スプリッタで複数管理（追加/削除/並替/状態保存、
  エリア別カラーアクセント）。状態キー: `browser_areas_state`。
- 平坦カラム（ui/flat_column.py）: 複数選択の統合結果を「次のカラム」風に表示。
  flatten_files は 5000件/2秒の安全弁付き。
  * **平坦ビューの D&D は _DragListView**（r74）: QStandardItemModel の既定
    mimeData に URL が無いため自前で text/uri-list を QDrag。選択済み項目の
    修飾なしプレスは保留（選択を崩さない）→ Move 閾値で選択全体をドラッグ／
    動かさず離せば単一選択。drag_finished(paths) → BrowserPanel が
    CappedColumnView._notify_drag_finished へ流す（DCC 落下時の通知は共通）。
- 共通子フォルダのドリルダウン（ui/common_columns.py）: 複数選択の子階層を同名統合で
  カラム表示し、選択で平坦ビューを再帰的に絞り込む。
  * 緑バーの **深さスイッチ**（r50）: 「⇊ 全階層」(既定) ⇔ 「⇣ 直下のみ」。
    flatten_files(recursive=) → FlatColumn.set_recursive → 設定 "flat_recursive"。
    全ての子フォルダカラムで連動（_on_flat_depth_changed）。ラベルは
    QSizePolicy.Ignored で縮小可、スイッチは Fixed（文言の省略防止）。
