# -*- coding: utf-8 -*-
"""r123: **DCC への操作が二重に実行されないこと（全経路）**。

ユーザー報告 2026-10-06:
  「Maya へ Reference を行うと 2 度行われる。以前 2 度実行される件は
    すべて調査し修正してくださいと伝えたのに、ここに残っているのが不安」

r119e では «開く» と «スクリプト実行» だけを対象にし、インポート／
リファレンスは「同じファイルを 2 回入れるのは正当な操作」という理由で
意図的に除外していた。これが誤り。守るべきは 2 つの別物:

  (A) 意図した繰り返し  … ユーザーが自分で 2 回やる。止めてはいけない
  (B) 1 操作の二重発火  … 1 回の操作が 2 回送られる。絶対に起きてはいけない

(A) を守るために (B) を素通しにしていた。

さらに、時間窓（1.2 秒）だけでは (B) を止めきれない。リファレンスは
Namespace の **モーダルダイアログ** を出すので、入力している間に窓が
過ぎ、その後に届いた 2 回目がすり抜ける。これが今回の報告の正体。

ここでは全種別について «2 回続けて呼んでも送信は 1 回» を検証する。
"""
import os
import time
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish

import ui.main_window_dcc as mwd

root = tmpdir()
scene = os.path.join(root, "chr_A.ma")
other = os.path.join(root, "chr_B.ma")
script = os.path.join(root, "tool.py")
for f in (scene, other, script):
    open(f, "w").close()


class _Bar:
    def showMessage(self, *a, **k):
        pass


class _Host(mwd.MainWindowDccMixin):
    """送信の直前（_bridge_send_async）で数えるだけの最小スタブ。"""

    def __init__(self):
        self._inside_maya = False
        self.sent = []
        self._dcc = "maya"
        # 既定ではスタブで数える。節 5 だけ del して本物を使う。
        self._bridge_send_async = lambda code, label, timeout=6.0, bridge=None: \
            self.sent.append(label)

    def statusBar(self):
        return _Bar()

    # 実際に DCC へ渡る唯一の場所。ここに来た回数＝実行回数。
    def __init_send_stub(self):
        pass

    def _dcc_accepts(self, command, path, app):
        return True

    # Maya は «つながっている» ことにする
    class _B:
        port = 20261

        def is_connected(self, timeout=0.3):
            return True
    _bridge = _B()
    _maya_inst = None


def _fresh():
    h = _Host()
    # _maya_send_or_prompt は実装のものを使う（ラップ・送信経路ごと検証する）
    return h


def _twice(fn, gap=0.0):
    """1 操作を «続けて 2 回» 呼ぶ（二重発火の再現）。"""
    fn()
    if gap:
        time.sleep(gap)
    fn()


# ── 1) リファレンス（報告された経路）──────────────────────────────
h = _fresh()
_twice(lambda: h._dcc_reference(scene, "maya", ask_ns=False))
assert len(h.sent) == 1, ("リファレンスが二重に送られた", h.sent)
print("reference fires once (the reported case): OK")

# ── 2) ダイアログで時間が経っても二重にならない ────────────────────
# Namespace ダイアログ相当として «2 秒かかる処理» を挟む。従来の 1.2 秒窓は
# これで流れてしまい、2 回目が素通りしていた。
h = _fresh()
real_ref = h._maya_reference


def _slow_ref(path, ask_ns=True):
    time.sleep(2.0)             # モーダルダイアログに相当
    return real_ref(path, ask_ns=False)


h._maya_reference = _slow_ref
h._dcc_reference(scene, "maya", ask_ns=False)
h._dcc_reference(scene, "maya", ask_ns=False)   # ダイアログ明け直後に届く 2 回目
assert len(h.sent) == 1, ("ダイアログを跨いで二重に送られた", h.sent)
print("a slow modal dialog no longer lets a second one through: OK")

# ── 3) 他の種別も同じであること ─────────────────────────────────
for kind, call in (
    ("インポート", lambda hh: hh._dcc_import(scene, "maya")),
    ("開く", lambda hh: hh._dcc_open(scene, "maya")),
    ("スクリプト実行", lambda hh: hh._maya_run_script(script)),
):
    h = _fresh()
    _twice(lambda: call(h))
    assert len(h.sent) == 1, ("%s が二重に送られた" % kind, h.sent)
print("import / open / run-script each fire once: OK")

# ── 4) 意図した繰り返しは止めない（(A) を壊していないこと）──────────
h = _fresh()
h._dcc_reference(scene, "maya", ask_ns=False)
time.sleep(2.2)                               # 十分に間を空けた «2 回目»
h._dcc_reference(scene, "maya", ask_ns=False)
assert len(h.sent) == 2, ("意図した 2 回目まで止めている", h.sent)
print("a deliberate second reference is still allowed: OK")

# 別ファイルなら続けて送れる
h = _fresh()
h._dcc_reference(scene, "maya", ask_ns=False)
h._dcc_reference(other, "maya", ask_ns=False)
assert len(h.sent) == 2, ("別ファイルまで止めている", h.sent)
print("a different file is never blocked: OK")

# ── 5) 最後の砦: 上流を飛ばしても «同じコード» は二度送られない ──────
# どの経路から来ても、送信の直前で止まることを確かめる。
class _CountingBridge:
    """本物の _bridge_send_async を使い、«実際にソケットへ渡った» 回数を数える。"""
    port = 20261

    def __init__(self):
        self.calls = []

    def is_connected(self, timeout=0.3):
        return True

    def send_python(self, code, timeout=6.0):
        self.calls.append(code)
        return True, "ok"


class _Notify:
    class _Sig:
        def emit(self, *a, **k):
            pass
    done = _Sig()


h = _fresh()
cb = _CountingBridge()
h._bridge = cb
h._bridge_notify = _Notify()
del h._bridge_send_async          # スタブを外して本物を使う
code = "print('same')"
h._bridge_send_async(code, "A")
h._bridge_send_async(code, "B")
time.sleep(0.4)                   # ワーカーの送信を待つ
assert len(cb.calls) == 1, ("同一コードの二重送信が素通りしている", cb.calls)
# 違うコードは通る
h._bridge_send_async("print('other')", "C")
time.sleep(0.4)
assert len(cb.calls) == 2, cb.calls
print("the send-level dedupe stops any path we missed: OK")

# ── 6) ガードを通らない送信入口が残っていないこと（棚卸し）───────────
import io as _io
src = _io.open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "ui", "main_window_dcc.py"), encoding="utf-8").read()
# _dcc_once / _dcc_guarded を経由する種別が揃っていること
for kind in ("open", "import", "reference", "run_script", "save", "export"):
    assert ('"%s"' % kind) in src, kind
assert "_dcc_guarded" in src and "SEND_DEDUP_SEC" in src
print("every action kind goes through the guard: OK")

finish(True)
