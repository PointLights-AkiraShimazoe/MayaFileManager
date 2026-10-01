# -*- coding: utf-8 -*-
"""
外部サービス連携（バージョン管理・クラウドストレージ）
=====================================================

プロバイダ方式:
  - Git（GitHub / TortoiseGit / git CLI）   … git_provider
  - Subversion（TortoiseSVN / svn CLI）     … svn_provider
  - Perforce（P4V / p4vc / p4 CLI）          … p4_provider
  - クラウド（OneDrive / Dropbox / Google Drive） … cloud_provider

安全設計（絶対条件）:
  * 検出・状態取得・外部コマンド実行は **全てワーカースレッド**。UIスレッドでは
    キャッシュ辞書の参照しかしない。
  * 各プロバイダは起動時に1回だけ検出し、未検出なら以後一切呼ばれない。
  * 外部コマンドは必ずタイムアウト付き。失敗・例外は握りつぶして「状態なし」。
    連続失敗したプロバイダは自動で無効化する（重くなる/クラッシュの芽を摘む）。
  * ワークスペース判定は lstat/readlink のみ（リンクを辿る stat はしない）。
    シンボリックリンク/ジャンクションは readlink で実体パスに置き換えてから判定。
"""

from .manager import get_manager  # noqa: F401
