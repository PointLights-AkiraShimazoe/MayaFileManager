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

    # r146（2026-11-12）: テスト中はログを一時フォルダへ隔離している
    # （_common.py の MFM_LOG_DIR）。見るべきは «差し替えていない時の置き場所»。
    from core import log_dir as ld
    real = os.path.realpath(ld.default_log_dir())
    assert real.startswith(home), ("ログの既定の置き場所がホーム配下に無い", real)
    assert not real.startswith(tool + os.sep), \
        ("ログの既定の置き場所がツールフォルダ配下にある（ドライブ障害で書けなくなる）", real)
    assert real == os.path.realpath(os.path.join(home, ".maya_file_manager", "logs")), real
    print("logs live under the user profile, not the tool drive: OK")

    # 3 つのログは «同じ 1 つの置き場所» を使う（別々に組み立てない）
    here = os.path.realpath(ld.user_log_dir())
    for name, path in (("startup", bu._MFM_STARTUP_LOG),
                       ("freeze", bu._MFM_FREEZE_LOG),
                       ("ui", bu._MFM_UI_LOG)):
        assert os.path.realpath(os.path.dirname(path)) == here, (name, path, here)
        assert not os.path.realpath(path).startswith(tool + os.sep), (name, path)
    # 連携・Maya 連携・Maya の起動のログも同じ場所
    from core.integrations import manager as _im
    from core import maya_bridge as _mb, maya_version as _mv
    for name, path in (("integrations", _im._LOG_PATH), ("maya", _mb._BRIDGE_LOG),
                       ("launch", _mv._launch_log_path())):
        assert os.path.realpath(os.path.dirname(path)) == here, (name, path, here)
    print("all logs share one location: OK")

    # テストは実機のログ置き場へ書かない
    assert here != real, "テストが実機のログ置き場を使っている: %s" % here
    print("tests do not write into the real log folder: OK")

    # 置き場が実在し、書ける
    assert os.path.isdir(bu._MFM_LOG_DIR), bu._MFM_LOG_DIR
    bu._mfm_slow_note("テスト書き込み")
    assert os.path.exists(bu._MFM_FREEZE_LOG), "フリーズログに書けない"
    print("freeze log is writable: OK")

    # フリーズ監視も同じ場所を使う（別々に定義していると片方だけ直し忘れる）
    import io
    # r146: 見張りは ui/freeze_watchdog.py へ分離した
    src = io.open(os.path.join(tool, "ui", "freeze_watchdog.py"),
                  encoding="utf-8").read()
    assert "_MFM_FREEZE_LOG" in src, "監視側が共通の定義を使っていない"
    assert 'os.path.dirname(os.path.abspath(__file__))),\n            "mfm_freeze.log")' not in src, \
        "監視側が独自にツールフォルダのパスを組み立てている"
    print("watchdog uses the shared path: OK")
    finish(True)


run(s1, delay=200)
