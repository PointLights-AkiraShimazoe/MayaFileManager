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

## 開発・デバッグの約束事

- 起動: `run_dev.bat`（mayapy優先、無ければ python）。デバッグは `$env:MFM_DEBUG=1` で
  `%USERPROFILE%\mfm_debug.log` に出力。
- **ビルドマーカー**: `ui/browser_panel.py` の BrowserPanel init ログに `rNN` マーカー、
  ファイル末尾に EOF センチネルログがある。ログに **initマーカーとEOF行の両方**が
  揃って初めて「最新かつ末尾欠損なし」と判断できる。コード変更時は両方を更新すること。
- **ファイル同期の罠**: Claude の Edit がユーザーPCの実ファイルに反映されるまで遅延があり、
  末尾切り詰めの中間状態でも起動できてしまう（コメント境界で切れると構文エラーにならない）。
  「こちらで動くのに実機で動かない」時は、まずログのマーカー/EOF行で実機コードの鮮度を疑う。
  bashマウント（/sessions/.../mnt/）も同様に遅延・切り詰めが起きる。
- **検証手順**: bashサンドボックスに PySide6 + /tmp/qtlibs（apt-get download で
  libegl1等を展開）を用意し、/tmp/pkg にコードを複製してオフスクリーン
  （QT_QPA_PLATFORM=offscreen）で QTest によるクリック再現＋ピクセル検証を行う。
  マウントが切り詰められている場合は head + 既知の末尾を継ぎ足して再構成する。

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

## UI構成

- BrowserArea（ui/browser_area.py）= プリセット行＋ブックマーク/履歴＋BrowserPanel の
  1ユニット。MainWindow が縦スプリッタで複数管理（追加/削除/並替/状態保存、
  エリア別カラーアクセント）。状態キー: `browser_areas_state`。
- 平坦カラム（ui/flat_column.py）: 複数選択の統合結果を「次のカラム」風に表示。
  flatten_files は 5000件/2秒の安全弁付き。
- 共通子フォルダのドリルダウン（ui/common_columns.py）: 複数選択の子階層を同名統合で
  カラム表示し、選択で平坦ビューを再帰的に絞り込む。
