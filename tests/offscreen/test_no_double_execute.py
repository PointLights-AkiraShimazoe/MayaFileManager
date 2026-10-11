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

r145 でこの «時間窓» を全廃した。窓は (A) を壊すうえ、(B) も止めきれて
いなかった（実機 2026-10-08: リファレンスとインポートが 2 回実行された）。

新しい保証の置き方:
  (A) 送信側は «ユーザーが呼んだ回数» をそのまま送る。捨てない。
  (B) 1 操作につき 1 個の操作 ID を発行し、DCC 側が «同じ ID は 1 回しか
      実行しない» ことで保証する（core/dcc_log の _OPID）。

ここでは全種別について «呼んだ回数だけ送る» ことと «1 操作 = 1 ID» を検証する。
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
        self.op_ids = []
        # 既定ではスタブで数える。
        def _send(code, label, timeout=6.0, bridge=None):
            self.sent.append(label)
            # 本物の _bridge_send_async は «予約（_dcc_arm）» を «実行中
            # （_dcc_pending）» に昇格させる。開くの «前の読み込みが終わる
            # まで受けない» はこの印で効くので、スタブでも同じことをする。
            import time as _t
            arm = getattr(self, "_dcc_arm", None)
            if arm:
                self._dcc_pending = (arm[0], _t.monotonic())
                self._dcc_arm = None
            # r145: 送ったコードに埋まっている操作 ID を拾う
            import re as _re
            m = _re.search(r"_OPID = \\?'([0-9a-f]*)", code or "")
            self.op_ids.append(m.group(1) if m else "")
        self._bridge_send_async = _send

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


def _call(fn, n=2):
    """1 操作を n 回呼ぶ。"""
    for _ in range(n):
        fn()


def _ids_of(h):
    return [c for c in h.op_ids if c]


# ── 1) 呼んだ回数だけ送る（握りつぶさない）─────────────────────────
h = _fresh()
_call(lambda: h._dcc_reference(scene, "maya", ask_ns=False))
assert len(h.sent) == 2, ("連続した同じ操作が握りつぶされている", h.sent)
print("a repeated reference is sent every time: OK")

# ── 2) «重ねられる» 種別は握りつぶさない ─────────────────────────
h = _fresh()
_call(lambda: h._dcc_import(scene, "maya"))
assert len(h.sent) == 2, ("インポートが握りつぶされた", h.sent)
print("import is not swallowed either: OK")

# ── 2b) «後戻りできない» 種別（開く / スクリプト実行）だけは別扱い ──
# 前の処理が終わるまで次を受けない。これは «重複を捨てる» のではなく
# «まだ走っている» という状態の話なので残す（読み込み中に次を送ると
# Maya ごと固まる。実機 2026-10-02）。
for kind, call in (
    ("開く", lambda hh: hh._dcc_open(scene, "maya")),
    ("スクリプト実行", lambda hh: hh._maya_run_script(script)),
):
    h = _fresh()
    _call(lambda: call(h))
    assert len(h.sent) == 1, ("%s の «進行中» ガードが効いていない" % kind, h.sent)
assert "run_script" in mwd.DESTRUCTIVE_KINDS and "open" in mwd.DESTRUCTIVE_KINDS
print("open / run-script refuse a second send while the first is running: OK")

# ── 3) 1 操作 = 1 ID、操作ごとに別の ID ──────────────────────────
h = _fresh()
_call(lambda: h._dcc_reference(scene, "maya", ask_ns=False))
ids = _ids_of(h)
assert len(ids) == 2, ("操作 ID が発行されていない", h.op_ids)
assert ids[0] != ids[1], ("別の操作なのに同じ ID", ids)
assert not getattr(h, "_dcc_op_id", ""), "操作が終わったら ID は空に戻す"
print("each operation carries its own op id: OK")

# ── 4) 時間窓の残骸が残っていないこと ────────────────────────────
import io as _io
src = _io.open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "ui", "main_window_dcc.py"), encoding="utf-8").read()
for kind in ("open", "import", "reference", "run_script", "save", "export"):
    assert ('"%s"' % kind) in src, kind
assert "_dcc_guarded" in src and "_dcc_begin_op" in src
assert "SEND_DEDUP_SEC" not in src, "送信側の時間窓が残っている"
assert "_last_dcc_send" not in src, "使われない時間記録が残っている"
print("no leftovers from the time-window dedupe: OK")

finish(True)
