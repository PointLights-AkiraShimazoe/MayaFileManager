"""
Maya Bridge — スタンドアロン版マネージャーと起動済みMayaの連携
==============================================================

マネージャーの「起動」ボタンで起動した Maya は commandPort（Python）を
開いている（launch_maya(command_port=...) 参照）。本モジュールはその
ポートへ TCP で Python コードを送り込み、「Mayaで開く／インポート／
リファレンス」をスタンドアロン版から実行可能にする。

- 接続先は localhost 固定（外部へは公開しない前提のローカルツール連携）
- コマンドごとに接続→送信→切断（commandPort は逐次接続を受け付ける）
- シーンオープン等の長時間処理は応答を待たない（タイムアウト＝送信成功扱い）

手動で起動した Maya と連携したい場合は、Maya のスクリプトエディタで
    import maya.cmds as cmds
    cmds.commandPort(name=":20261", sourceType="python")
を実行すれば同じポートに接続できる。
"""
from core.diag import swallow as _swallow  # r112

import os as _os
import re
import socket

_BRIDGE_LOG = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                            "mfm_maya.log")


def bridge_log(msg: str):
    """Maya連携（ポートスキャン/識別/userSetup）の経緯をツールフォルダ直下へ記録。"""
    try:
        import datetime
        with open(_BRIDGE_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
    except OSError:
        pass

DEFAULT_PORT = 20261
# 複数Mayaの同時起動に対応するポートレンジ（起動ごとに空きを割り当てる）
PORT_RANGE = tuple(range(20261, 20270))


def scan_open_ports(host: str = "127.0.0.1", timeout: float = 0.15):
    """レンジ内で接続を受け付けているポート（=連携可能なMaya候補）を返す。
    localhostへの接続試行は開いていれば即成功・閉じていれば即拒否のため高速。"""
    found = []
    for p in PORT_RANGE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, p))
            found.append(p)
        except OSError:
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass
    return found


def find_free_port(host: str = "127.0.0.1") -> int:
    """レンジ内で未使用の最初のポートを返す（新規Maya起動用）。"""
    used = set(scan_open_ports(host))
    for p in PORT_RANGE:
        if p not in used:
            return p
    return PORT_RANGE[-1]


# Maya識別用: バージョン・シーン名・PIDをセンチネル付きで返すコード。
# 注意1: commandPort(python) は «単一の式» の評価結果しか返さない。
#        import文などを含むコードを送ると応答が None/空になり識別に失敗する
#        （2026-09 実機で「Maya?」表示の原因になった）。必ず式のまま保つこと。
# 注意2: 応答には他のスクリプト出力等が混入し得るため（echoOutput事故の事例
#        あり）、応答は必ず parse_identify() で検証・抽出する。
IDENTIFY_CODE = (
    "'MFMID<' + str(__import__('maya.cmds', fromlist=['about'])"
    ".about(version=True))"
    " + '><' + (__import__('maya.cmds', fromlist=['file'])"
    ".file(q=True, sceneName=True) or '')"
    " + '><' + str(__import__('os').getpid()) + '>MFMID'"
)

IDENTIFY_RE = re.compile(
    r"MFMID<([^<>\r\n]{0,40})><([^<>\r\n]*)><(\d{0,12})>MFMID")


def parse_identify(reply):
    """識別応答から (version, scene_path, pid) を抽出する。不正なら None。
    pid は取得できなければ None。"""
    if not reply:
        return None
    m = IDENTIFY_RE.search(reply)
    if not m:
        return None
    pid = int(m.group(3)) if m.group(3) else None
    return m.group(1).strip(), m.group(2).strip(), pid


# ── userSetup.py への連携インストール ────────────────────────────────
# マネージャー以外から起動したMaya（ショートカット起動等）とも接続できるよう、
# 全Mayaが起動時に空きポートで commandPort を開くスニペットを
# Documents/maya/scripts/userSetup.py（全バージョン共通）へ書き込む。

_US_BEGIN = "# >>> MayaFileManager bridge >>>"
_US_END = "# <<< MayaFileManager bridge <<<"

# 【重要・自動変換禁止】以下の文字列は **Maya の userSetup.py にそのまま貼られる**。
# Manager のモジュールではないので、`_swallow()` など当パッケージの関数を
# 書いてはいけない（Maya 側で NameError になり、ポートが一切開かなくなる）。
# r112 の一括変換がここへ `_swallow` を入れてしまい、ポートが埋まっている
# 2 台目以降の Maya が «接続リストに出ない» 原因になっていた（r119 で修復）。
# except は素の `pass` のままにすること。noqa: PLS-NO-SWALLOW
PORT_SNIPPET_BODY = """
def _mfm_open_bridge_port():
    import maya.cmds as _cmds
    for _p in range(%d, %d):
        try:
            if _cmds.commandPort(":%%d" %% _p, q=True):
                continue          # 既に誰かが使用中のポートは飛ばす
        except Exception:
            pass
        try:
            _cmds.commandPort(name=":%%d" %% _p, sourceType="python")
            return _p
        except Exception:
            pass
    return None
""" % (PORT_RANGE[0], PORT_RANGE[-1] + 1)

_US_SNIPPET = _US_BEGIN + """
# MayaFileManager: 起動時に連携用commandPortを自動で開く（レンジ内の空きを使用）
""" + PORT_SNIPPET_BODY + """
try:
    import maya.utils as _mu
    _mu.executeDeferred(_mfm_open_bridge_port)
except Exception:
    pass
""" + _US_END + "\n"


def open_port_snippet() -> str:
    """«既に起動している» Maya のスクリプトエディタ（Python）に貼って、
    その場で連携ポートを開くためのコード（r119）。
    userSetup.py は «次回起動から» しか効かないので、作業中の Maya を
    Manager から見えるようにする唯一の手段がこれになる。"""
    return (PORT_SNIPPET_BODY.strip() + "\n\n"
            "print('MayaFileManager bridge port: %s' % _mfm_open_bridge_port())\n")


def maya_app_dir():
    """Maya のユーザー設定ディレクトリ（MAYA_APP_DIR 相当）を返す。
    優先順: 環境変数 MAYA_APP_DIR → Windows の «ドキュメント» 既知フォルダ
    （OneDrive等へのリダイレクトを正しく反映）/maya → ~/Documents/maya。
    注意: ~/Documents 固定だと、ドキュメントが OneDrive にリダイレクトされた
    環境では Maya が読まない場所へ書いてしまう（2026-09 実機で発生）。"""
    import os
    env = os.environ.get("MAYA_APP_DIR")
    if env:
        return env
    docs = None
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            # CSIDL_PERSONAL (=5): 「ドキュメント」。既知フォルダの移動を反映
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                docs = buf.value
        except Exception:
            docs = None
    if not docs:
        docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs, "maya")


def usersetup_path():
    """全Mayaバージョン共通の userSetup.py のパス（<maya app dir>/scripts）。"""
    import os
    return os.path.join(maya_app_dir(), "scripts", "userSetup.py")


def is_usersetup_installed() -> bool:
    import os
    p = usersetup_path()
    if not os.path.isfile(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            return _US_BEGIN in f.read()
    except OSError:
        return False


def install_usersetup() -> str:
    """連携スニペットを userSetup.py へ追記（既存ブロックは置換）。
    書き込んだファイルのパスを返す。"""
    import os
    p = usersetup_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    text = ""
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    if _US_BEGIN in text and _US_END in text:
        # 既存ブロックを最新版に置換
        head = text.split(_US_BEGIN)[0]
        tail = text.split(_US_END, 1)[1]
        text = head + _US_SNIPPET.rstrip("\n") + tail
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + _US_SNIPPET
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    bridge_log("install_usersetup: wrote %r" % p)
    return p


def escape_path(path: str) -> str:
    """Pythonコード文字列に埋め込むためのパスエスケープ（/区切り化）。"""
    return (path or "").replace("\\", "/").replace("'", "\\'")


class MayaBridge:
    """Maya commandPort への軽量クライアント。"""

    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1"):
        self.host = host
        self.port = int(port)

    def set_port(self, port: int):
        """接続先Maya（ポート）を切り替える。"""
        self.port = int(port)

    def _connect(self, timeout: float) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((self.host, self.port))
        return s

    def is_connected(self, timeout: float = 0.6) -> bool:
        """commandPort に到達できるか（= 連携可能な Maya が起動しているか）。"""
        try:
            s = self._connect(timeout)
            s.close()
            return True
        except OSError:
            return False

    def send_python(self, code: str, timeout: float = 3.0):
        """Python コードを送信する。

        Returns:
            (ok, reply):
              ok=True  … 送信成功。reply は Maya からの応答文字列
                         （長時間処理で応答待ちを打ち切った場合は None）
              ok=False … 接続/送信失敗。reply はエラーメッセージ
        """
        try:
            s = self._connect(1.5)
        except OSError as e:
            return False, f"Mayaに接続できません: {e}"
        try:
            s.settimeout(timeout)
            s.sendall(code.encode("utf-8") + b"\n")
            try:
                data = s.recv(65536)
                reply = (data.decode("utf-8", errors="replace")
                         .replace("\x00", "").strip())
            except socket.timeout:
                reply = None   # シーンオープン等の長時間処理は待たない
            return True, reply
        except OSError as e:
            return False, f"送信に失敗しました: {e}"
        finally:
            try:
                s.close()
            except OSError:
                pass
