# EXE ビルドガイド

## 必要環境

| 項目 | 推奨 |
|---|---|
| OS | Windows 10/11 64bit（EXE生成） |
| Python | 3.10 〜 3.12（Mayaのバージョンに揃えると安全） |
| PySide | PySide6 推奨（Maya 2025+）/ PySide2（Maya 2023/2024） |
| PyInstaller | 6.x 以上 |

> **注意**: PyInstaller は「ビルドしたOS と同じOSの実行ファイル」しか作れません。  
> Windows .exe を作るには Windows マシンでビルドする必要があります。

---

## Windows — ワンクリックビルド

```bat
cd MayaFileManager
build_exe.bat
```

スクリプトが以下を自動実行します：
1. pip を最新化
2. PySide6 / send2trash / pyinstaller をインストール
3. 前回のビルドをクリーン
4. PyInstaller で `MayaFileManager.exe` を生成
5. `dist/MayaFileManager.exe` に出力

---

## 手動ビルド（詳細制御したい場合）

```bat
:: 1. 仮想環境を作る（推奨）
python -m venv .venv
.venv\Scripts\activate

:: 2. 依存パッケージをインストール
pip install PySide6 send2trash pyinstaller

:: EXR サムネイル対応が必要な場合
pip install opencv-python

:: 3. ビルド実行
pyinstaller MayaFileManager.spec --clean --noconfirm

:: 出力: dist\MayaFileManager.exe
```

---

## macOS

```bash
chmod +x build_exe.sh
./build_exe.sh
# 出力: dist/MayaFileManager.app
```

---

## Linux

```bash
chmod +x build_exe.sh
./build_exe.sh
# 出力: dist/MayaFileManager  (単一バイナリ)
```

---

## よくあるエラーと対処

### `ModuleNotFoundError: No module named 'PySide6'`
```bat
pip install PySide6
```

### `WARNING: UPX is not available`
無視してかまいません。UPX は圧縮のみでビルド自体には不要です。  
必要なら: https://github.com/upx/upx/releases から `upx.exe` をダウンロードして PATH に追加。

### アンチウイルスが EXE を削除する
PyInstaller 製の EXE は誤検知されることがあります。  
`dist\MayaFileManager.exe` をアンチウイルスの除外リストに追加してください。

### `ImportError: cannot import name 'xxx' from 'PySide6'`
PySide6 のバージョンが古い可能性があります：
```bat
pip install --upgrade PySide6
```

### `The 'icon' parameter is invalid`
`resources/icons/mfm.ico` が存在しない場合、spec ファイルの `icon=` 行が自動でスキップされます。  
アイコンを設定したい場合は 256x256 の `.ico` ファイルを配置してください。

---

## EXE サイズの目安

| 構成 | サイズ |
|---|---|
| PySide6 + 最小セット | 約 35〜50 MB |
| + OpenCV (EXRサムネイル) | 約 80〜120 MB |

> UPX 圧縮を有効にすると 20〜30% 削減できます。

---

## Maya 内での使用（EXE不要）

EXE は Maya 外スタンドアロン起動用です。  
Maya のシェルフから呼ぶ場合は EXE 不要で直接 Python を使います：

```python
# Maya シェルフボタン
import sys
sys.path.insert(0, r"C:\tools\MayaFileManager")
import main
main.show_in_maya()
```

---

## 配布パッケージの構成

```
dist/
  MayaFileManager.exe      ← これだけ配布すれば動く（単一ファイル）
```

設定は実行時に自動生成されます：
```
C:\Users\<name>\.maya_file_manager\
  settings.json
  state_global.json
```
