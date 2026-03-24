# GitHub Actions でEXEをビルドする手順

## 前提

- GitHub アカウント（無料でOK）
- Git（インストール済みであること）

---

## STEP 1 — GitHubにリポジトリを作成

1. https://github.com/new を開く
2. Repository name: `MayaFileManager`
3. **Private**（非公開）を選択 ← ソースを公開したくない場合
4. "Create repository" をクリック

---

## STEP 2 — ローカルからpush

```bat
cd MayaFileManager

:: Git 初期化
git init
git add .
git commit -m "Initial commit"

:: GitHub のリポジトリURLを設定（自分のユーザー名に変更）
git remote add origin https://github.com/YOUR_USERNAME/MayaFileManager.git
git branch -M main
git push -u origin main
```

---

## STEP 3 — EXEビルドをトリガー（タグをpush）

```bat
:: バージョンタグを付けてpush → 自動でEXEビルドが走る
git tag v1.0.0
git push origin v1.0.0
```

---

## STEP 4 — ダウンロード

1. GitHub リポジトリページを開く
2. **Actions** タブ → "Build MayaFileManager.exe" → 最新のワークフロー
3. 完了後（2〜5分）:

   **タグpushの場合** → **Releases** タブに `MayaFileManager_v1.0.0_windows.zip` が自動添付される

   **手動実行の場合** → Actions → Summary → **Artifacts** セクションからダウンロード

---

## 手動でいつでもビルドする方法

タグなしでも手動実行できます：

1. GitHub → **Actions** タブ
2. 左サイドバー "Build MayaFileManager.exe" をクリック
3. 右上 **"Run workflow"** ボタン
4. 2〜5分待つ → Artifacts からダウンロード

---

## ビルドにかかる時間の目安

| フェーズ | 時間 |
|---|---|
| Windows環境セットアップ | 約1分 |
| pip install PySide6 | 約1〜2分 |
| PyInstaller ビルド | 約2〜3分 |
| **合計** | **約4〜6分** |

---

## トラブルシューティング

### Actions タブが見えない
- Settings → Actions → General → "Allow all actions" に設定

### ビルドが失敗する
1. Actions → 失敗したワークフロー → ログを展開
2. エラーメッセージをコピーして Claude に貼り付ける

### Release が作られない
- タグのpushでないと Release は作成されません
- `git tag v1.0.0 && git push origin v1.0.0` で再実行

---

## ファイル構成（追加されたもの）

```
MayaFileManager/
├── .github/
│   └── workflows/
│       ├── build.yml   ← EXEビルド（タグpush / 手動）
│       └── ci.yml      ← 構文チェック（push/PR時）
├── .gitignore
└── ... (既存ファイル)
```
