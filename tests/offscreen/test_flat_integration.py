# -*- coding: utf-8 -*-
"""r146: 平坦ビューの連携（P4V / クラウド）— バッジと複数選択の実行。

ユーザー報告 2026-10-13:
  「以前の安定バージョンとしてリリースしたもので、平坦モードで複数選択で
    チェックアウトをした場合に、正常に実行されていなかった。
    平坦モードに P4V やクラウドストレージのアイコンがつかないのも同様に。」

見つかった原因は 3 つ。

  A. 平坦ビューのリスト表示が **素の QStyledItemDelegate** だった。
     カラム側は r58 から StatusBadgeDelegate を使っているのに、ここだけ
     バッジを描く人がいなかった（＝一度も出ていなかった）。
  B. 状態が届いた時に平坦ビューを **誰も描き直していなかった**。
     カラム側は status_updated を受けて再描画している。
  C. actions_for() が «プロバイダ未確定のフォルダのファイルを黙って捨てる»
     作りだった。状態取得はワーカー 1 本の直列なので、平坦ビューのように
     フォルダが何十個もあると 0.4 秒の待ちでは終わらず、**選択の一部だけ**
     がチェックアウトされていた。
  D. コマンドラインの長さ上限（Windows 32767 文字）。数百件選ぶと超える。
"""
import ast
import io
import os

from _common import *  # noqa: F401,F403
from _common import finish

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


# ── A) バッジのデリゲート ────────────────────────────────────────
fc = _src("ui/flat_column.py")
check("StatusBadgeDelegate" in fc,
      "平坦ビューのリスト表示が連携バッジを描くデリゲートを使う")
check("QStyledItemDelegate(v)" not in fc,
      "素のデリゲートへ戻していない（戻すとバッジが消える）")

from ui.flat_column import FlatColumn
col = FlatColumn()
col._apply_view_mode()
dele = col._view.itemDelegate()
check(dele.__class__.__name__ == "StatusBadgeDelegate",
      "実際に入っているのは %s" % dele.__class__.__name__)
check(callable(getattr(col, "_path_of", None)),
      "デリゲートへ渡すパス取得がある")
col.deleteLater()

# ── B) 状態が届いたら描き直す ────────────────────────────────────
bp = _src("ui/browser_panel.py")
check("_on_flat_integration_status" in bp, "平坦ビュー用の再描画がある")
check("status_updated.connect(self._on_flat_integration_status)" in bp,
      "状態更新につないでいる")
check("_ensure_flat_badge_refresh" in bp, "つなぎ込みが 1 回だけ行われる")

# ── C) 未確定フォルダを捨てない ──────────────────────────────────
mgr = _src("core/integrations/manager.py")
check("_resolve_providers_sync" in mgr,
      "間に合わなかったフォルダは同期で確定する")
# 文字列の有無ではなく «呼び出し» を見る（説明文に名前が出るのは正常）。
_sync_fn = None
for node in ast.walk(ast.parse(mgr)):
    if isinstance(node, ast.FunctionDef) and node.name == "_resolve_providers_sync":
        _sync_fn = node
_sync_calls = [n.func.attr for n in ast.walk(_sync_fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
check("fetch_status" not in _sync_calls,
      "同期側では重い fetch_status を呼ばない（%r）" % (sorted(set(_sync_calls)),))
check("root_for" in _sync_calls, "root_for だけを使う")
tree = ast.parse(mgr)
fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "actions_for":
        fn = node
calls = [n.func.attr for n in ast.walk(fn)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
check("_resolve_providers_sync" in calls,
      "actions_for がそれを呼ぶ（%r）" % (sorted(set(calls)),))

# ── D) コマンドラインの分割 ──────────────────────────────────────
base = _src("core/integrations/base.py")
check("split_cli_batches" in base and "CLI_ARG_CHARS" in base,
      "長すぎるコマンドラインを分割する仕掛けがある")

ns = {}
btree = ast.parse(base)
limit = None
for node in btree.body:
    if isinstance(node, ast.ClassDef):
        for b in node.body:
            if isinstance(b, ast.Assign) and getattr(b.targets[0], "id", "") == "CLI_ARG_CHARS":
                limit = b.value.value
            if isinstance(b, ast.FunctionDef) and b.name == "split_cli_batches":
                b.decorator_list = []
                mod = ast.Module(body=[b], type_ignores=[])
                ast.fix_missing_locations(mod)
                exec(compile(mod, "<split>", "exec"), ns)


class _C:
    CLI_ARG_CHARS = limit
    split_cli_batches = classmethod(ns["split_cli_batches"])


check(isinstance(limit, int) and limit < 32767,
      "上限が Windows の 32767 より小さい（%r）" % (limit,))
files = ["C:/very/long/path/to/a/file_%04d.ma" % i for i in range(1200)]
cmd = ["p4", "-p", "x:1", "edit"] + files
batches = _C.split_cli_batches(cmd, 4)
check(len(batches) > 1, "長い時は分かれる（%d 本）" % len(batches))
check(all(b[:4] == ["p4", "-p", "x:1", "edit"] for b in batches),
      "どの回にも固定部分が付く")
check(sum(len(b) - 4 for b in batches) == len(files),
      "ファイルが 1 件も欠けない（%d / %d）"
      % (sum(len(b) - 4 for b in batches), len(files)))
got = []
for b in batches:
    got.extend(b[4:])
check(got == files, "並び順も変わらない")
check(max(sum(len(a) + 3 for a in b) for b in batches) <= limit,
      "どの回も上限を超えない")
check(_C.split_cli_batches(["p4", "edit", "a.ma"], 2) == [["p4", "edit", "a.ma"]],
      "短い時は 1 本のまま")
check(_C.split_cli_batches(["p4", "info"], 2) == [["p4", "info"]],
      "ファイルが無くても落ちない")

p4 = _src("core/integrations/p4_provider.py")
for verb in ("edit", "add", "sync", "revert"):
    check(('["%s"] + rpaths' % verb) in p4 and "file_from=1" in p4,
          "p4 %s が分割対象になっている" % verb)

finish(not fails)
