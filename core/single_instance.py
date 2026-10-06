# -*- coding: utf-8 -*-
"""単一起動（r126）
==================

スタンドアロンのマネージャーを «1 つだけ» にする。2 つ目を起動したら、
既に開いている方を前面に出して自分は静かに終わる。

なぜ必要か（2026-10-06 の調査）:
  - 設定ファイルは «起動時に読んだ内容を丸ごと書き戻す» 作りだった。
    2 つ開くと後から保存した方が相手の変更を巻き戻す。実験で、B が
    無関係な設定を 1 つ変えて保存しただけで A が増やしたプリセットが
    消えることを確認した（r126 で保存側もマージ方式に直したが、
    そもそも 2 つ開く必要が無い）。
  - DCC へ «空きポートで Maya を起動» する時、2 つのマネージャーが同時に
    探すと同じポートを掴む（find_free_port は見た瞬間の空きしか見ない）。
  - 連携ログを起動時に上書きしていたため、後から起動した方が相手の記録を
    消していた（r126 で追記へ変更済み）。

仕組みは Qt の QLocalServer（Windows では名前付きパイプ）。ファイルの
ロックと違い、プロセスが落ちれば OS 側が後始末するので «ロックが残って
二度と起動できない» 事故が起きにくい。それでも取りこぼしに備えて、
接続できない名前は一度消してから作り直す。

**Maya の中で開くパネルは対象外。** あれは Maya のプロセスの一部であって
«2 つ目のマネージャー» ではない。スタンドアロンと併用しても壊れないよう、
設定の保存は r126 でマージ方式にしてある。
"""
from core.diag import swallow as _swallow

import getpass
import os


def _default_key() -> str:
    """ユーザーごとに別の名前にする（共有 PC で互いを締め出さないため）。"""
    try:
        user = getpass.getuser()
    except Exception:
        user = str(os.getuid() if hasattr(os, "getuid") else "user")
    return "MayaFileManager-%s" % user


class SingleInstance:
    """1 つ目なら acquire() が True。2 つ目なら False。

    QtNetwork が使えない環境（持っていない PySide など）では **常に True**
    を返す。起動できないより «複数起動を許す» 方が害が小さい。
    """

    def __init__(self, key: str = ""):
        self.key = key or _default_key()
        self._server = None
        self._on_second_launch = None

    def acquire(self) -> bool:
        try:
            from PySide6.QtNetwork import QLocalServer, QLocalSocket
        except ImportError:
            try:
                from PySide2.QtNetwork import QLocalServer, QLocalSocket  # noqa: F401
            except ImportError:
                return True        # QtNetwork が無い → 排他しない
        # 既に誰かいるか、300ms だけ聞いてみる
        probe = QLocalSocket()
        probe.connectToServer(self.key)
        alive = probe.waitForConnected(300)
        if alive:
            probe.abort()
            return False
        probe.abort()
        # 誰もいない。前回の異常終了で名前だけ残っている場合に備えて消す
        try:
            QLocalServer.removeServer(self.key)
        except Exception as _e:
            _swallow(_e, "core/single_instance.py acquire(removeServer)")
        server = QLocalServer()
        try:
            server.setSocketOptions(QLocalServer.UserAccessOption)
        except Exception as _e:
            _swallow(_e, "core/single_instance.py acquire(socketOptions)")
        if not server.listen(self.key):
            # 名前を取れない＝他が握っている（競り合いに負けた）
            return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def set_second_launch_handler(self, fn):
        """2 つ目が起動した時に呼ばれる（既存ウィンドウを前面に出す用）。"""
        self._on_second_launch = fn

    def _on_new_connection(self):
        try:
            sock = self._server.nextPendingConnection()
            if sock is not None:
                sock.disconnectFromServer()
            fn = self._on_second_launch
            if callable(fn):
                fn()
        except Exception as _e:
            _swallow(_e, "core/single_instance.py _on_new_connection")

    def notify_existing(self) -> bool:
        """既に動いている方へ «前に出て» と伝える。"""
        try:
            from PySide6.QtNetwork import QLocalSocket
        except ImportError:
            try:
                from PySide2.QtNetwork import QLocalSocket
            except ImportError:
                return False
        try:
            sock = QLocalSocket()
            sock.connectToServer(self.key)
            if not sock.waitForConnected(500):
                return False
            sock.write(b"raise\n")
            sock.waitForBytesWritten(300)
            sock.disconnectFromServer()
            return True
        except Exception as _e:
            _swallow(_e, "core/single_instance.py notify_existing")
            return False

    def release(self):
        try:
            if self._server is not None:
                self._server.close()
                self._server = None
        except Exception as _e:
            _swallow(_e, "core/single_instance.py release")
