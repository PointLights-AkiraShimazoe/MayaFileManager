# -*- coding: utf-8 -*-
"""r146（2026-11-16）: «前の処理が終わっていない» は **送り先（Maya）ごと** に数える。

ユーザー報告 2026-11-16:
  「違う Maya でファイルを開いている最中に、別の Maya で違うファイルを開こうと
    しているのに、前の処理が終わってないと出て実行できません」

原因: «実行中» の印をマネージャー全体で 1 つしか持っていなかった。印の目的は
«読み込み中の Maya に次の «開く» を重ねない»（重ねると Maya ごと固まる。実機
2026-10-02）ことで、これは送り先ごとの話。1 つしか無いと、ある Maya が
読み込み中の間、関係のない別の Maya へも送れない。さらに、どの送り先の応答でも
その 1 つを落としていたので、別の Maya の応答で «読み込み中» の印まで消えていた。

固定すること:
  1) A が読み込み中でも、別の接続先 B へは «開く» を送れる
  2) A へは、A が終わるまで次の «開く» を送らない（従来の保護は残す）
  3) B の応答は B の印だけ落とす（A の印は残る）
  4) A が応答するようになったら A の印だけ落ち、A へまた送れる
  5) 見張りは印のある送り先だけに、送り先ごとに聞く
  6) Maya が読み込み中でも Blender へは送れる（逆も同じ）
"""
import os
import threading
import time

from _common import *  # noqa: F401,F403
from _common import app, sm, tmpdir, finish, run, QTest
from ui.main_window import MainWindow

fails = []
PORT_A, PORT_B, PORT_BL = 20261, 20262, 20271


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class _FakeBridge:
    """commandPort の代わり。送られたものを数え、決めた応答を返す。
    応答 None ＝ «送れたが返事が無い»（読み込み中・確認ダイアログ待ち）。"""

    def __init__(self, port, replies):
        self.port = port
        self.host = "127.0.0.1"
        self.replies = replies
        self.sent = []

    def set_port(self, port):
        self.port = int(port)

    def is_connected(self, timeout=0.3):
        return True

    def send_python(self, code, timeout=3.0, tag=""):
        self.sent.append((self.port, tag))
        return True, self.replies.get(self.port)


def pump(cond, limit=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def s1():
    root = tmpdir()
    files = {}
    for n in ("a.ma", "b.ma", "c.ma", "d.ma", "e.blend", "f.ma"):
        files[n] = os.path.join(root, n)
        open(files[n], "w").close()

    w = MainWindow(sm); w.resize(1100, 700); w.show()
    w._scan_running["maya"] = True          # 本物のスキャンは動かさない
    w._scan_running["blender"] = True
    QTest.qWait(250); app.processEvents()
    w._raise_sent_dcc = lambda bridge: None  # 実ウィンドウの最前面化はしない
    w._start_dcc_watch = lambda: None        # 見張りのタイマーは回さない（5 で直接呼ぶ）
    poll_real = w._poll_dcc_idle
    w._poll_dcc_idle = lambda: None

    replies = {PORT_A: None, PORT_B: "opened", PORT_BL: None}
    maya = _FakeBridge(PORT_A, replies)
    blender = _FakeBridge(PORT_BL, replies)
    w._bridge, w._bl_bridge = maya, blender
    done = []
    w._bridge_notify.done.connect(lambda ok, label, reply: done.append(label))

    def sent_to(port):
        return [t for p, t in maya.sent + blender.sent if p == port]

    # ── A へ «開く»（返事なし＝読み込み中）────────────────────────────
    w._dcc_open(files["a.ma"], "maya")
    pump(lambda: len(done) >= 1)
    check(len(sent_to(PORT_A)) == 1, "A へ 1 回送られる（%r）" % (maya.sent,))

    # ── 2) A へは、終わるまで次を送らない ────────────────────────────
    w._dcc_open(files["c.ma"], "maya")
    pump(lambda: False, 0.1)
    check(len(sent_to(PORT_A)) == 1,
          "読み込み中の A へは次の «開く» を送らない（%r）" % (maya.sent,))

    # ── 1) 接続先を B に切り替えれば送れる ───────────────────────────
    maya.set_port(PORT_B)
    w._dcc_open(files["b.ma"], "maya")
    pump(lambda: len(sent_to(PORT_B)) >= 1, 1.0)
    check(len(sent_to(PORT_B)) == 1,
          "A が読み込み中でも、別の接続先 B へは送れる（%r）" % (maya.sent,))
    pump(lambda: False, 0.2)             # B の応答（"opened"）を届かせる

    # ── 3) B の応答は B の印だけ落とす ───────────────────────────────
    w._dcc_open(files["d.ma"], "maya")
    pump(lambda: len(sent_to(PORT_B)) >= 2, 1.0)
    check(len(sent_to(PORT_B)) == 2, "応答が返った B へは続けて送れる（%r）" % (maya.sent,))
    pump(lambda: False, 0.2)
    maya.set_port(PORT_A)
    w._dcc_open(files["c.ma"], "maya")
    pump(lambda: False, 0.1)
    check(len(sent_to(PORT_A)) == 1,
          "B の応答では A の印は落ちない。A へはまだ送らない（%r）" % (maya.sent,))

    # ── 6) Maya が読み込み中でも Blender へは送れる ──────────────────
    w._dcc_open(files["e.blend"], "blender")
    pump(lambda: len(sent_to(PORT_BL)) >= 1, 1.0)
    check(len(sent_to(PORT_BL)) == 1,
          "Maya が読み込み中でも Blender へは送れる（%r）" % (blender.sent,))
    pump(lambda: False, 0.2)

    # ── 5) 見張りは印のある送り先ごと ────────────────────────────────
    started = []
    orig_thread = threading.Thread

    class _NoThread(orig_thread):
        def start(self):
            started.append(self.name)
    threading.Thread = _NoThread
    try:
        poll_real()
        check(len(started) == 2, "見張りは印のある 2 つの送り先へ聞く（%r）" % started)
        poll_real()
        check(len(started) == 2, "返事を待っている送り先へは重ねて聞かない（%r）" % started)
        w._on_dcc_idle(PORT_BL, False)               # Blender はまだ応答しない
        poll_real()
        check(len(started) == 2, "応答が無かった送り先へは間を空ける（%r）" % started)
    finally:
        threading.Thread = orig_thread

    # ── 4) A が応答するようになったら A の印だけ落ちる ───────────────
    w._on_dcc_idle(PORT_A, True)
    w._dcc_open(files["f.ma"], "maya")
    pump(lambda: len(sent_to(PORT_A)) >= 2, 1.0)
    check(len(sent_to(PORT_A)) == 2, "終わった A へはまた送れる（%r）" % (maya.sent,))
    pump(lambda: False, 0.2)
    w._dcc_open(files["e.blend"], "blender")
    pump(lambda: False, 0.1)
    check(len(sent_to(PORT_BL)) == 1,
          "A が終わっても、読み込み中の Blender の印は残る（%r）" % (blender.sent,))

    # ── 全消し（接続し直し等）は全部の印を落とす ─────────────────────
    w._clear_dcc_pending()
    w._dcc_open(files["a.ma"], "maya")
    w._dcc_open(files["e.blend"], "blender")
    pump(lambda: len(sent_to(PORT_A)) >= 3 and len(sent_to(PORT_BL)) >= 2, 1.0)
    check(len(sent_to(PORT_A)) == 3 and len(sent_to(PORT_BL)) == 2,
          "全消しの後はどちらへも送れる（%r / %r）" % (maya.sent, blender.sent))
    pump(lambda: False, 0.2)
    w._clear_dcc_pending()
    finish(not fails)


run(s1, delay=800)
