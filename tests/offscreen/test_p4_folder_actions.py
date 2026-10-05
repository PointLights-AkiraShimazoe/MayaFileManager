# -*- coding: utf-8 -*-
"""r121: フォルダに対する Perforce 操作が P4V と同じ «その下すべて» になること。

ユーザー報告 2026-10-05:
  「フォルダを選択して行うと、その以下の未追加が追加される挙動のはずですが、
    この manager ではエラーで終了します」

原因は 2 つあった:
  1. p4 CLI に «素のフォルダパス» を渡していた。p4 はそれをファイルとして
     扱うのでエラーになる。再帰指定（<dir>/...）が要る。sync だけが
     付けていて、add / edit / revert は付けていなかった。
  2. p4 はファイル単位で成否を返す。フォルダ一括では「もう追加済み」等が
     必ず混ざり、rc も 0 以外になる。それを一括で «失敗» と判定していたため、
     実際には追加できていても「失敗しました」で終わっていた。
"""
import os
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish

from core.integrations.p4_provider import P4Provider

root = tmpdir()
sub = os.path.join(root, "Models")
os.makedirs(sub)
f1 = os.path.join(root, "a.ma")
open(f1, "w").close()

p = P4Provider()
p.p4 = "p4"          # 検出を飛ばして実行形だけ見る
p.p4vc = None
p.p4v = None
p._conn_args = []

# ── 1) フォルダには /... が付き、ファイルには付かない ────────────────
got = p.recurse([sub, f1])
assert got[0] == sub.replace("\\", "/").rstrip("/") + "/..." or \
    got[0] == sub.rstrip("\\/") + "/...", ("フォルダに再帰指定が付いていない", got)
assert got[1] == f1, ("ファイルに余計な指定が付いている", got)
# 末尾の区切りがあっても二重にならない
assert p.recurse([sub + os.sep])[0].endswith("/...") and \
    "//..." not in p.recurse([sub + os.sep])[0], p.recurse([sub + os.sep])
print("a folder becomes <dir>/... and a file is left alone: OK")

# ── 2) add / edit / revert / sync すべてに効いていること ──────────────
sent = []
p.run_cli_async = lambda cmd, cwd, what, **k: sent.append((what, list(cmd)))
acts = p.actions(root, [sub])
labels = [a[0] for a in acts if a[0]]
for want in ("追加", "チェックアウト", "最新を取得", "変更を元に戻す"):
    hit = [a for a in acts if a[0] and want in a[0]]
    assert hit, ("%s の項目が無い" % want, labels)
    sent.clear()
    hit[0][1]()                      # 実行（run_cli_async を差し替え済み）
    assert sent, ("%s でコマンドが組み立てられていない" % want)
    _what, cmd = sent[0]
    assert any(str(x).endswith("/...") for x in cmd), \
        ("%s がフォルダを再帰で渡していない" % want, cmd)
    assert sub not in cmd, ("%s が素のフォルダパスを渡している" % want, cmd)
print("add / checkout / sync / revert all pass <dir>/...: OK")

# ── 3) 「一部は対象外」を失敗にしないこと ─────────────────────────────
mixed = (
    "//depot/Proj/Models/new_01.ma#1 - opened for add\n"
    "//depot/Proj/Models/new_02.ma#1 - opened for add\n"
    "D:\\ws\\Proj\\Models\\old.ma - can't add existing file\n"
)
ok, msg = p.classify_cli_result(1, mixed, "")      # p4 は rc!=0 を返す
assert ok is True, ("一部が対象外なだけで失敗にしている", msg)
assert "2" in msg and "1" in msg, ("件数が出ていない", msg)
print("a mixed result (some added, some already there) counts as success: OK")

# 全部が «対象外» でも失敗ではない（やることが無かっただけ）
ok2, _m2 = p.classify_cli_result(1, "x.ma - can't add existing file\n", "")
assert ok2 is True
# 本当の失敗はきちんと失敗
ok3, msg3 = p.classify_cli_result(
    1, "", "Perforce client error:\n\tConnect to server failed\n")
assert ok3 is False and "onnect to server failed" in msg3, (ok3, msg3)
ok4, _m4 = p.classify_cli_result(
    1, "//depot/x.ma - must resolve before submitting\n", "")
assert ok4 is False
print("a real error is still reported as a failure: OK")

# 判定材料が無い時は従来どおり rc で決める
assert p.classify_cli_result(0, "", "")[0] is True
assert p.classify_cli_result(2, "", "")[0] is False
print("with nothing to classify it falls back to the exit code: OK")

finish(True)
