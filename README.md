# Maya File Manager (MFM)

Maya 2023以降対応のスタンドアロン＆Maya内蔵ファイルマネージャー。  
PySide2 / PySide6 両対応。

---

## ディレクトリ構成

```
MayaFileManager/
├── main.py                  # エントリーポイント（スタンドアロン / Maya プラグイン）
├── config/
│   └── default_settings.json
├── core/
│   ├── compat.py            # PySide2/6 互換レイヤー
│   ├── maya_version.py      # Maya インストール検索・ファイルバージョン検出・起動
│   ├── settings_manager.py  # JSON 永続化（グローバル / Maya バージョン別）
│   ├── bookmark_manager.py  # ブックマーク CRUD・ツリー管理
│   ├── file_operations.py   # コピー・移動・削除・バッチリネーム・FBX・自動命名
│   └── thumbnail_generator.py # 非同期サムネイル生成（LRU キャッシュ付き）
└── ui/
    ├── launcher_dialog.py   # Maya バージョン選択 & 起動ダイアログ
    ├── main_window.py       # メインウィンドウ（ドック・メニュー・ツールバー）
    ├── browser_panel.py     # ファイルブラウザ（カラム / リスト / サムネイル）
    ├── bookmark_panel.py    # ブックマークパネル（ツリー・D&D）
    └── preset_editor.py     # リファレンスプリセットエディタ
```

---

## 起動方法

### スタンドアロン（Maya 外）

```bash
# Launcher ダイアログから Maya バージョンを選択して起動
python main.py

# Launcher をスキップしてマネージャーを直接開く
python main.py --no-launcher
```

### Maya 内（シェルフボタン / userSetup.py）

```python
import sys
sys.path.insert(0, r"C:/path/to/MayaFileManager")
import main
main.show_in_maya()
```

### Maya プラグインとして登録

```
Plug-in Manager → MayaFileManager/main.py を登録
→ メニューバーに "File Manager" が追加される
```

---

## 状態の保存先

```
~/.maya_file_manager/
    settings.json          ← 設定（全 Maya 共通）
    state_global.json      ← UI 状態（全 Maya 共通）
    state_maya_2023.json   ← Maya 2023 専用オーバーライド
    state_maya_2024.json   ← Maya 2024 専用オーバーライド
```

---

## 主要機能一覧

| カテゴリ | 機能 |
|---|---|
| **ブラウザ** | カラム / リスト / サムネイルビュー切替 |
| | 最大カラム深度設定 |
| | ドライブスイッチ |
| | テキストフィルタ |
| | ソート（名前 / 種類 / 更新日時） |
| **ファイル操作** | コピー・移動・削除（複数選択可） |
| | バッチリネーム（replace / prefix / suffix / sequence / regex） |
| | 関連付けアプリで開く |
| | エクスプローラーで表示 |
| **Maya 連携** | Maya でOpen / Import / Reference |
| | FBX Import / Export |
| | D&D でインポート・オープン |
| **ブックマーク** | フォルダ・ディレクトリ・ファイルのブックマーク |
| | D&D で並べ替え・フォルダ整理 |
| | カラーラベル |
| | Maya バージョン別 / 共通切替 |
| **履歴** | 保持件数設定可 |
| | Maya バージョン別 / 共通切替 |
| **クイックナビ** | プリセット切替可能なナビゲーションボタン |
| **自動命名** | ディレクトリ別ルール・シーケンス番号 |
| **リファレンスプリセット** | Namespace・ファイル・コンストレイン・スクリプトをセットで保存・適用 |
| **サムネイル** | LRU キャッシュ・非同期生成・EXR/HDR 対応 |
| **状態管理** | 全設定を Maya から独立した JSON で管理 |
| | グローバル / Maya バージョン別オーバーライド |

---

## 依存パッケージ

| パッケージ | 用途 | 必須 |
|---|---|---|
| PySide2 または PySide6 | UI | ✅ |
| send2trash | ゴミ箱への削除 | 任意 |
| opencv-python | EXR/HDR サムネイル | 任意 |

```bash
pip install send2trash
pip install opencv-python  # EXR サポートが必要な場合
```

---

## 今後の実装予定（TODO）

- [ ] 設定ダイアログ（UI）
- [ ] クイックナビプリセットエディタ（UI）
- [ ] リファレンス内容変更エディタ（既存 Reference の編集）
- [ ] バッチリネームダイアログ（UI）
- [ ] 重複フォルダ検出・統合表示
- [ ] 自動命名ルールエディタ（UI）
- [ ] FBX Export ダイアログ（オプション付き）
- [ ] 選択ファイル以下の重複フォルダ表示
- [ ] Maya との双方向 D&D（Maya ビューポートへのドロップ）
