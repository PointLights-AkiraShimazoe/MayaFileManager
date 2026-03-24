# EXE ビルド手順

## 必要なもの

| ツール | バージョン | 入手先 |
|---|---|---|
| Python | 3.10 以上 | https://python.org |
| PySide6 または PySide2 | 最新推奨 | pip |
| PyInstaller | 5.13 以上 | pip |
| UPX（任意・圧縮） | 最新 | https://github.com/upx/upx/releases |

---

## Windows

### ワンクリックビルド

```
build_exe.bat をダブルクリック
```

内部でやっていること:
1. `pip install -r requirements.txt`
2. `python -m PyInstaller MayaFileManager.spec --noconfirm`

### 手動ビルド

```cmd
cd MayaFileManager
pip install -r requirements.txt
python -m PyInstaller MayaFileManager.spec --noconfirm
```

成果物: `dist\MayaFileManager\MayaFileManager.exe`

---

## macOS

```bash
cd MayaFileManager
chmod +x build_exe.sh
./build_exe.sh
```

成果物: `dist/MayaFileManager/MayaFileManager`

### .app バンドルにしたい場合

```bash
pip install pyinstaller
pyinstaller MayaFileManager.spec --noconfirm --windowed --osx-bundle-identifier com.pointlights.mfm
```

---

## Linux

```bash
cd MayaFileManager
./build_exe.sh
```

成果物: `dist/MayaFileManager/MayaFileManager`

---

## アイコン生成（任意）

```bash
python generate_icon.py
```

生成後、`MayaFileManager.spec` の icon 行のコメントを外す:

```python
# Before:
# icon=os.path.join(ROOT, "resources", "icons", "app.ico"),

# After:
icon=os.path.join(ROOT, "resources", "icons", "app.ico"),
```

---

## ビルド成果物の構成

```
dist/
└── MayaFileManager/
    ├── MayaFileManager.exe   ← 起動ファイル（これだけ実行）
    ├── _internal/            ← Qt/Python ランタイム（変更不要）
    │   ├── PySide6/
    │   ├── ...
    ├── config/               ← 設定テンプレート
    └── resources/            ← リソース（アイコン等）
```

`dist/MayaFileManager/` フォルダごと ZIP して配布してください。

---

## よくある問題

### Windows Defender が誤検知する
PyInstaller 製 exe は誤検知されやすいため、除外設定を追加するか
コードサイニング証明書を購入して署名してください。

### 起動が遅い（onefile モード）
`MayaFileManager.spec` の最下部で onefile/onedir を切り替えできます。
配布サイズより起動速度を優先する場合は onedir（デフォルト）を使用してください。

### `ModuleNotFoundError: core.xxx` が出る
`build_hooks/runtime_hook.py` が正しく含まれているか確認してください。
または `pyinstaller --paths=.` を追加:

```bash
python -m PyInstaller MayaFileManager.spec --paths=. --noconfirm
```

### PySide2 環境でビルドしたい（Maya 2023/2024 内部と統一）
`requirements.txt` の PySide2 行を有効にしてビルドしてください。
スタンドアロン起動では PySide6 の方が高機能です。

---

## Maya 内ではそのまま使える

exe が不要な Maya 内部使用の場合は zip を展開して:

```python
# Maya の Script Editor または userSetup.py に追記
import sys
sys.path.insert(0, r"C:/path/to/MayaFileManager")
import main
main.show_in_maya()
```
