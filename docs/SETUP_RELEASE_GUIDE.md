# MayaFileManager 公開・リリース手順書（島添さん用）

**最終更新**: 2026-06-11 / 対象リポジトリ: https://github.com/PointLights-AkiraShimazoe/MayaFileManager

## ゴール

GitHubのReleasesページから `MayaFileManager_vX.X.X_Setup.exe` をダウンロード → ダブルクリックでインストール完了、の状態を維持すること。EXE・インストーラーのビルドはすべてGitHub Actionsが行うため、**ローカルにPythonやInno Setupは不要**。

## 1. 事前準備（初回のみ）

1. **Git for Windows** が入っていること（`git --version` で確認）
2. **GitHub認証**: `gh auth login`（GitHub CLI）または PAT/SSHキーでpushできること
3. NAS上のリポジトリを使う場合は safe.directory 設定（設定済みのはず）:
   ```
   git config --global --add safe.directory "%(prefix)///192.168.0.226/disk1/tools/maya/builds/MayaFileManager"
   git config --global --add safe.directory "Z:/tools/maya/builds/MayaFileManager"
   ```

## 2. 今回の変更の反映（Cowork作業分）

Coworkが編集した作業コピーは `PLs-Tools/MayaFileManager`（.git付き）にあります。

```bat
cd C:\Users\owner\Documents\tools\maya\scripts\PLs-Tools\MayaFileManager
git status                  :: 変更内容を確認
git add -A
git commit -m "Maya 2027対応・M3テーマエンジン・インストーラーCI統合"
git push origin main        :: ブランチ名がmasterの場合は読み替え
```

NAS側 (Z:\tools\maya\builds\MayaFileManager) を正本として使う場合は、push後にNAS側で `git pull` してください。

## 3. リリース（タグを打つだけ）

```bat
git tag v1.1.0
git push origin v1.1.0
```

→ GitHub Actions「Build MayaFileManager.exe」が自動起動（4〜7分）し、Releasesに以下が添付されます：

- `MayaFileManager_v1.1.0_Setup.exe` … **インストーラー（推奨配布物）**
- `MayaFileManager_v1.1.0_windows.zip` … EXE単体（ポータブル）

## 4. 動作確認チェックリスト

- [ ] Actionsタブで「Build MayaFileManager.exe」が緑チェック
- [ ] CI（Syntax Check）が Python 3.9 / 3.10 / 3.11 / 3.13 すべて緑（=Maya 2023〜2027互換）
- [ ] Releasesから Setup.exe をDL → インストール → 起動確認
- [ ] アンインストール → 再インストールでブックマーク・履歴が残っていること（設定は削除しない仕様）
- [ ] M3ダークテーマが適用されていること（起動時コンソールに theme_engine エラーが出ないこと）

## 5. トラブルシューティング

| 症状 | 対処 |
|---|---|
| Actionsでインストーラー生成失敗 | ログの「Build installer」を確認。ISCC.exeが見つからない場合はchocoで自動インストールされる |
| テーマが旧配色になる | `config/design_tokens.json` の構文エラー。コンソールに fallback メッセージが出る |
| pushが拒否される | `gh auth status` で認証確認。NASの場合は safe.directory を再確認 |

## 6. テーマのカスタマイズ

色・角丸・行高はすべて `config/design_tokens.json` で管理（QSS直書き禁止）。
`core/theme_engine.py` の `export_qss()` で生成QSSを確認できます。
ライトテーマは `apply_theme(app, mode="light")` で適用可能（切替UIは今後実装）。
