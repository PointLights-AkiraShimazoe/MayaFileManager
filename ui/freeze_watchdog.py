# -*- coding: utf-8 -*-
"""UI スレッドのフリーズ見張り（r146 で ui/main_window_dcc.py から分離）。

MainWindow から ``install(window)`` を 1 回呼ぶ。見張りはプロセスに 1 本だけで、
ウィンドウは «脈拍» を打つタイマーを持つだけ。詳しい経緯は install() の docstring。
"""
from core.diag import swallow as _swallow
from core.diag import diag_off as _diag_off
import os
from core.compat import QTimer


# ---------------------------------------------------------------------------
# フリーズ見張りの «プロセスに 1 つだけ» の持ち物（r146）
#   ウィンドウごとに持つと、2 枚目を作った時に 1 枚目の file が回収され、
#   faulthandler が閉じた fd を書きに行って落ちる。詳しくは
#   _install_freeze_watchdog の docstring。
# ---------------------------------------------------------------------------
_FREEZE_BEAT = [0.0]          # 最後に UI スレッドが生きていた時刻
_FREEZE_WD_INSTALLED = False  # 見張りを入れたか
_FREEZE_WD_FILE = None        # faulthandler に渡した file（絶対に手放さない）
_FREEZE_REARM = None          # ネイティブダンプの再武装（既定は None）


def install(window):
    """UI スレッドが 1.5 秒以上止まったら、その瞬間のスタックを記録する。

    出力先は **ユーザープロファイル直下**（~/.maya_file_manager/logs）。
    r117: 以前はツールフォルダ直下に書いていたため、**ツールを置いた
    ドライブが無応答になると記録そのものがブロック**され、肝心な時に
    何も残らなかった（2026-10-01、D: 無応答時に実際に発生）。

    r146（重要・2026-10-14 に実機で特定）
    ------------------------------------
    ここが **アプリをランダムに落としていた**。Windows + Maya の Python
    3.13 で、回帰スイートが 3221225477（ACCESS_VIOLATION）で断続的に
    落ちる件を追ったところ、犯人は faulthandler の
    ``dump_traceback_later`` だった（これを止めると 2/6 → 6/6 合格）。
    理由は 2 つ。

    1. ``dump_traceback_later`` は **GIL を持たない C のスレッド**から
       全スレッドのフレームを歩く。UI スレッドが «止まっている» ように
       見えても、実際には C++ 側で Python のフレームを組み替えている
       最中なので、歩いた先が解放済みになりうる。
    2. 渡した file を **こちらが手放すと落ちる**。ウィンドウごとに
       ``window._fh_file`` を開いていたので、2 枚目のウィンドウを作って
       1 枚目が回収されると、faulthandler は閉じた fd を書きに行く。

    対策:
    * 仕掛けは **プロセスに 1 つだけ**（ウィンドウを何枚作っても 1 回）。
    * file は **モジュール変数で持ち続ける**（回収させない）。
    * ``dump_traceback_later`` は **既定で使わない**。本当に必要な時だけ
      ``MFM_FREEZE_DUMP=1`` で入れる（調査用。製品では使わないこと）。
      普段は Python 側の見張り（_current_frames）だけで記録する。
    """
    if _diag_off("no_freeze_watchdog"):
        return
    import threading
    import time as _time
    import sys as _sys
    import traceback as _tb
    from ui.browser_panel import _mfm_log, _MFM_FREEZE_LOG
    global _FREEZE_WD_INSTALLED, _FREEZE_WD_FILE, _FREEZE_REARM
    freeze_log = _MFM_FREEZE_LOG

    def _dump(text):
        try:
            with open(freeze_log, "a", encoding="utf-8") as f:
                import datetime
                f.write("[%s] %s\n"
                        % (datetime.datetime.now().strftime("%H:%M:%S"),
                           text))
        except OSError:
            pass
        _mfm_log(text)

    if not _FREEZE_WD_INSTALLED:
        _FREEZE_WD_INSTALLED = True
        # ── クラッシュ時の記録（これは «落ちた時だけ» 走るので安全）──
        try:
            import faulthandler
            import datetime as _dt
            # モジュール変数で持つ＝プロセスが終わるまで閉じない。
            _FREEZE_WD_FILE = open(freeze_log, "a", encoding="utf-8")
            _FREEZE_WD_FILE.write("\n[%s] === faulthandler 監視開始 ===\n"
                                  % _dt.datetime.now().strftime("%H:%M:%S"))
            _FREEZE_WD_FILE.flush()
            faulthandler.enable(file=_FREEZE_WD_FILE, all_threads=True)
        except Exception as _e:
            _swallow(_e, "ui/main_window_dcc.py faulthandler.enable")

        # ── «止まっている最中» のネイティブダンプ（既定は入れない）──
        if os.environ.get("MFM_FREEZE_DUMP"):
            def _rearm():
                try:
                    import faulthandler
                    faulthandler.dump_traceback_later(
                        1.5, repeat=False, file=_FREEZE_WD_FILE, exit=False)
                except Exception as _e:
                    _swallow(_e, "ui/main_window_dcc.py _rearm")
            _FREEZE_REARM = _rearm
            _rearm()

        main_id = threading.get_ident()

        def _watch():
            last_dump = 0.0
            while True:
                _time.sleep(0.5)
                now = _time.monotonic()
                stalled = now - _FREEZE_BEAT[0]
                if stalled > 1.5 and now - last_dump > 5.0:
                    # r108: ドラッグ等 «意図して UI スレッドを握っている»
                    # 区間は記録しない（本物のフリーズが埋もれるため）
                    try:
                        from ui.browser_panel import mfm_blocking_reason
                        if mfm_blocking_reason():
                            continue
                    except Exception as _e:
                        _swallow(_e, "ui/main_window_dcc.py _watch")
                    last_dump = now
                    try:
                        frames = _sys._current_frames()
                        frm = frames.get(main_id)
                        if frm is not None:
                            _dump("=== UIフリーズ検出 (%.1f秒停止) "
                                  "メインスレッド ===\n%s"
                                  % (stalled, "".join(_tb.format_stack(frm))))
                        # r146（実機 2026-10-16 16:00、フォルダ移動後に 40 秒超）:
                        # メインスレッドが app.exec() の中（C++）で止まっている
                        # 時は、Python 側だけでは何も分からない。長い停止では
                        # 他のスレッドの Python スタックも 1 回だけ出し、
                        # «誰がロックを握っていたか» を後から追えるようにする。
                        if stalled > 5.0 and frm is not None:
                            names = {t.ident: t.name for t in threading.enumerate()}
                            parts = []
                            for tid, f in frames.items():
                                if tid == main_id:
                                    continue
                                parts.append("--- thread %s (%s)\n%s" % (
                                    tid, names.get(tid, "?"),
                                    "".join(_tb.format_stack(f, limit=6))))
                            if parts:
                                _dump("=== 他スレッド（参考） ===\n" + "".join(parts))
                    except Exception as _e:
                        _swallow(_e, "ui/main_window_dcc.py _watch(dump)")

        threading.Thread(target=_watch, daemon=True,
                         name="mfm-freeze-watchdog").start()

    # ── 脈拍はウィンドウごとに打つ（見張りはプロセスに 1 つ）──────
    _FREEZE_BEAT[0] = _time.monotonic()

    def _heartbeat():
        _FREEZE_BEAT[0] = _time.monotonic()
        if _FREEZE_REARM is not None:
            _FREEZE_REARM()

    t = QTimer(window)
    t.setInterval(200)
    t.timeout.connect(_heartbeat)
    t.start()
    window._wd_timer = t
