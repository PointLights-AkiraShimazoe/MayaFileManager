# -*- coding: utf-8 -*-
"""r117: 監視ログは «監視対象と別のディスク» に置く。

ツールフォルダ直下に書いていると、**そのドライブが無応答になった時に
記録そのものがブロック**され、肝心な場面で証拠が残らない
（2026-10-01、D: 無応答で UI が固まったのに mfm_freeze.log が空だった）。
"""
import os
from _common import *  # noqa: F401,F403
from _common import finish, run


def s1():
    from ui import browser_util as bu

    home = os.path.realpath(os.path.expanduser("~"))
    # ui/browser_util.py → ui → リポジトリルート
    tool = os.path.realpath(
        os.path.dirname(os.path.dirname(os.path.abspath(bu.__file__))))

    for name, path in (("startup", bu._MFM_STARTUP_LOG),
                       ("freeze", bu._MFM_FREEZE_LOG),
                       ("ui", bu._MFM_UI_LOG)):
        real = os.path.realpath(path)
        assert real.startswith(home), \
            ("%s ログがホーム配下に無い" % name, real)
        assert not real.startswith(tool + os.sep), \
            ("%s ログがツールフォルダ配下にある（ドライブ障害で書けなくなる）"
             % name, real)
    print("logs live under the user profile, not the tool drive: OK")

    # 置き場が実在し、書ける
    assert os.path.isdir(bu._MFM_LOG_DIR), bu._MFM_LOG_DIR
    bu._mfm_slow_note("テスト書き込み")
    assert os.path.exists(bu._MFM_FREEZE_LOG), "フリーズログに書けない"
    print("freeze log is writable: OK")

    # フリーズ監視も同じ場所を使う（別々に定義していると片方だけ直し忘れる）
    import io
    src = io.open(os.path.join(tool, "ui", "main_window_dcc.py"),
                  encoding="utf-8").read()
    assert "_MFM_FREEZE_LOG" in src, "監視側が共通の定義を使っていない"
    assert 'os.path.dirname(os.path.abspath(__file__))),\n            "mfm_freeze.log")' not in src, \
        "監視側が独自にツールフォルダのパスを組み立てている"
    print("watchdog uses the shared path: OK")
    finish(True)


run(s1, delay=200)
