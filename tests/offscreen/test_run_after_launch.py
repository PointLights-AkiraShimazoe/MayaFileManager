# -*- coding: utf-8 -*-
"""r145: 「起動しますか？」で Yes を押したら、その操作が起動後に実行される。

ユーザー報告 2026-10-09:
  「そしてYesで起動しましたが、そのままファイルのOpenは実行されませんでした。
    起動後、実行しようとしたコマンドが実行されるようにしてください。」

従来は «起動して、ステータスバーに「完了後にもう一度実行してください」» で
終わっていた。1 回の操作で済むはずのものを 2 回やらせていた。

ここで固定すること:
  1) つながるまでは送らない
  2) つながったら送る（起動直後の «ポートは開いたがまだ動かせない» を避けるため
     連続して確認できてから送る）
  3) 送るのは «最初に組み立てたコードそのまま» ＝ 操作 ID が同じなので、
     あとから手で実行し直しても DCC 側で二重実行にならない
  4) 待ちすぎない（時間切れでやめる）
  5) 新しい操作が来たら古い待ちは捨てる（1 つだけ覚える）
"""
import os
import time

from _common import *  # noqa: F401,F403
from _common import app, finish, QTest

import ui.main_window_dcc as mwd

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class _Bar:
    def showMessage(self, *a, **k):
        pass


class _Bridge:
    port = 20261

    def __init__(self):
        self.up = False

    def is_connected(self, timeout=0.3):
        return self.up


class _Host(mwd.MainWindowDccMixin):
    def __init__(self):
        self.sent = []
        self._bridge = _Bridge()
        self._bl_bridge = _Bridge()
        self._bridge_send_async = lambda code, label, timeout=6.0, bridge=None: \
            self.sent.append((label, code))

    def statusBar(self):
        return _Bar()


h = _Host()
h._queue_after_launch("maya", "CODE-A", "開く a.ma")
check(getattr(h, "_pending_after_launch", None) is not None, "起動待ちを覚える")

# 1) つながっていなければ送らない
for _ in range(3):
    h._poll_pending_after_launch()
check(h.sent == [], "つながるまでは送らない（%r）" % (h.sent,))

# 2) つながったら送る（連続確認のぶんだけ待つ）
h._bridge.up = True
h._poll_pending_after_launch()
check(h.sent == [], "1 回見えただけでは送らない（起動直後の取りこぼし対策）")
h._poll_pending_after_launch()
check(len(h.sent) == 1, "連続で見えたら送る（%r）" % (h.sent,))
check(h.sent[0][1] == "CODE-A", "3) 最初のコードをそのまま送る（操作 ID も同じ）")
check(getattr(h, "_pending_after_launch", None) is None, "送ったら待ちを捨てる")

# もう一度叩いても二度送らない
h._poll_pending_after_launch()
check(len(h.sent) == 1, "待ちが無ければ何も起きない")

# 4) 時間切れ
h2 = _Host()
h2._queue_after_launch("maya", "CODE-B", "開く b.ma")
h2._pending_after_launch["t"] = time.monotonic() - (h2.PENDING_LAUNCH_SEC + 1)
h2._bridge.up = True
h2._poll_pending_after_launch()
check(h2.sent == [] and getattr(h2, "_pending_after_launch", None) is None,
      "待ちすぎたらやめる（%r）" % (h2.sent,))

# 5) 新しい操作が来たら差し替え
h3 = _Host()
h3._queue_after_launch("maya", "CODE-C", "開く c.ma")
h3._queue_after_launch("maya", "CODE-D", "開く d.ma")
h3._bridge.up = True
h3._poll_pending_after_launch()
h3._poll_pending_after_launch()
check([c for _l, c in h3.sent] == ["CODE-D"],
      "最後の操作だけが実行される（%r）" % (h3.sent,))

# Blender も同じ仕組みを通る
h4 = _Host()
h4._queue_after_launch("blender", "CODE-E", "Blender: 開く e.blend")
h4._bl_bridge.up = True
h4._poll_pending_after_launch()
h4._poll_pending_after_launch()
check([c for _l, c in h4.sent] == ["CODE-E"], "Blender も起動後に実行（%r）" % (h4.sent,))
check(h4._bridge.up is False, "Maya 側のつながりは見ない")

finish(not fails)
