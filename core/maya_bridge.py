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

import os
import os as _os          # 旧コード互換（関数内で import os しているものがある）
import re
import socket
import sys

# r119: ログは «監視対象と別の故障単位» へ。従来はツールフォルダ直下
# （= ツールを置いたドライブ）で、そのドライブが落ちると連携の記録まで
# 失われた（r117 と同じ轍）。出せなければツールフォルダへ退避する。
try:
    _BRIDGE_LOG_DIR = _os.path.join(_os.path.expanduser("~"),
                                    ".maya_file_manager", "logs")
    _os.makedirs(_BRIDGE_LOG_DIR, exist_ok=True)
except OSError:
    _BRIDGE_LOG_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_BRIDGE_LOG = _os.path.join(_BRIDGE_LOG_DIR, "mfm_maya.log")


def bridge_log(msg: str):
    """Maya連携（ポートスキャン/識別/userSetup）の経緯を記録する。"""
    try:
        import datetime
        with open(_BRIDGE_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
    except OSError:
        pass

DEFAULT_PORT = 20261
# 複数Mayaの同時起動に対応するポートレンジ（起動ごとに空きを割り当てる）
PORT_RANGE = tuple(range(20261, 20270))


def listening_ports():
    """この PC で **聞いている**（LISTEN 状態の）TCP ポート {port: pid}（r146）。

    接続を試さずに OS の表から読む。Windows は iphlpapi の
    GetExtendedTcpTable、Linux は /proc/net/tcp。取れない環境は None。

    なぜ要るか: «繋がらない» には 2 種類ある。閉じている（誰も聞いていない）
    のと、聞いてはいるが受け付けが詰まっている（Maya がシーン読み込み中で
    accept が止まり、待ち行列が埋まった）の 2 つ。接続の時間切れだけでは
    区別できない（実機 2026-10-20: この PC では閉じたポートへの接続も
    «拒否» ではなく時間切れになった＝ファイアウォールの仕様）。"""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes as wt
            iphlpapi = ctypes.windll.iphlpapi
            AF_INET, TCP_TABLE_OWNER_PID_LISTENER = 2, 3
            size = wt.DWORD(0)
            iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False,
                                         AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0)
            buf = ctypes.create_string_buffer(max(4, size.value))
            rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False,
                                              AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0)
            if rc != 0:
                return None

            class _Row(ctypes.Structure):
                _fields_ = [("state", wt.DWORD), ("local_addr", wt.DWORD),
                            ("local_port", wt.DWORD), ("remote_addr", wt.DWORD),
                            ("remote_port", wt.DWORD), ("pid", wt.DWORD)]
            n = wt.DWORD.from_buffer_copy(buf.raw[:4]).value
            out = {}
            off = 4
            for _ in range(n):
                row = _Row.from_buffer_copy(buf.raw[off:off + ctypes.sizeof(_Row)])
                off += ctypes.sizeof(_Row)
                port = socket.ntohs(row.local_port & 0xFFFF)
                out[port] = int(row.pid)
            return out
        out = {}
        for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(fn) as f:
                    next(f)
                    for line in f:
                        parts = line.split()
                        if len(parts) > 3 and parts[3] == "0A":      # LISTEN
                            out[int(parts[1].rsplit(":", 1)[1], 16)] = 0
            except OSError:
                continue
        return out
    except Exception:
        return None


def scan_ports(host: str = "127.0.0.1", timeout: float = 0.15):
    """レンジ内のポートを (open, busy) に分けて返す（r146）。

    open … 接続できた（＝応答できる Maya がいる）
    busy … 聞いてはいる（LISTEN）が接続できない。Maya はシーンの読み込み中など
           本体が忙しいと commandPort の accept が止まり、こちらが投げた接続が
           待ち行列（backlog）に溜まる。溜まり切ると新しい接続は通らない。
           **閉じたのではない。** 判定は OS の LISTEN 表（listening_ports）で
           行う。接続の時間切れでは決めない（この PC では閉じたポートも
           時間切れになる）。表が取れない環境では busy は常に空。

    実機 2026-10-20 15:40: 1 台目の Maya にシーンを開かせた直後、2 台目を
    起動すると 1 台目が一覧から消えた（scan: open ports=[] → [20262]）。
    1 台目は読み込み中で accept が止まり、見張りの 1.5 秒ごとの問い合わせで
    backlog が埋まって繋がらなくなり、«閉じた» と見なして落としていた
    （ユーザー報告「複数起動のコネクトリスト追加のエラー」）。"""
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
    listening = listening_ports() or {}
    busy = [p for p in PORT_RANGE if p in listening and p not in found]
    return found, busy


def scan_open_ports(host: str = "127.0.0.1", timeout: float = 0.15):
    """レンジ内で接続を受け付けているポート（=連携可能なMaya候補）を返す。
    localhostへの接続試行は開いていれば即成功・閉じていれば即拒否のため高速。"""
    return scan_ports(host, timeout)[0]


def find_free_port(host: str = "127.0.0.1") -> int:
    """レンジ内で未使用の最初のポートを返す（新規Maya起動用）。

    r146: 接続が時間切れのポート（読み込み中で accept が止まっている Maya）も
    «使用中»。空きと見て予約すると、起動した Maya がそのポートを開けず隣へ
    逃げ、予約だけが残る（実機 2026-10-20、:20261 → :20262）。"""
    open_, busy = scan_ports(host)
    used = set(open_) | set(busy)
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

# 【重要・自動変換禁止】以下の文字列は **Maya 側で実行される**。
# Manager のモジュールではないので、`_swallow()` など当パッケージの関数を
# 書いてはいけない（Maya 側で NameError になり、ポートが一切開かなくなる）。
# r112 の一括変換がここへ `_swallow` を入れてしまい、ポートが埋まっている
# 2 台目以降の Maya が «接続リストに出ない» 原因になっていた（r119 で修復）。
# except は素の `pass` のままにすること。noqa: PLS-NO-SWALLOW
#
# r146: **ASCII のみ** で書く（コメントも英語）。Maya の設定フォルダに置く
# ファイルは文字コードの問題を起こしてはならない。test_bridge_snippet が
# `isascii()` で見張る。
PORT_SNIPPET_BODY = """
def _mfm_trace(_msg):
    # Leave a trace so "did the bridge code run at all" can be told later.
    # Shares the Manager's log (~/.maya_file_manager/logs/mfm_maya.log).
    try:
        import os as _os, datetime as _dt
        _d = _os.path.join(_os.path.expanduser("~"), ".maya_file_manager", "logs")
        try:
            _os.makedirs(_d)
        except Exception:
            pass
        _f = open(_os.path.join(_d, "mfm_maya.log"), "a")
        try:
            _f.write("[%%s] maya: %%s\\n"
                     %% (_dt.datetime.now().strftime("%%H:%%M:%%S"), _msg))
        finally:
            _f.close()
    except Exception:
        pass


def _mfm_open_bridge_port():
    import maya.cmds as _cmds
    _ports = range(%d, %d)
    # 1) If THIS Maya already has a port in the range, do nothing.
    #    A Maya launched by the Manager opens its port via -command first;
    #    opening another one here would list one Maya twice (r119b).
    #    commandPort(q=True) answers for this Maya only (other processes: False).
    for _p in _ports:
        try:
            if _cmds.commandPort(":%%d" %% _p, q=True):
                _mfm_trace("already open port %%d" %% _p)
                return _p
        except Exception:
            pass
    # 2) Otherwise take the first free port. A port held by another Maya
    #    fails to open, so move on to the next one.
    for _p in _ports:
        try:
            _cmds.commandPort(name=":%%d" %% _p, sourceType="python")
            _mfm_trace("opened port %%d" %% _p)
            return _p
        except Exception:
            pass
    _mfm_trace("no free port in range")
    return None
""" % (PORT_RANGE[0], PORT_RANGE[-1] + 1)

# スニペットの版。中身を変えたら **必ず上げる**。
# 古い版が入ったままだと 「入れたのに効かない」 が起きるので、Manager が
# 検出して更新を促せるようにする（r119b）。
#   v5 (r146): userSetup.py 書き込み方式 → Maya モジュール方式へ。
SNIPPET_VERSION = 5

# 旧方式（〜v4）: 他人の userSetup.py に挟んでいたブロックの目印。
# r146 以降は **検出と除去にだけ** 使う。書き込むことは二度と無い。
_US_SNIPPET = _US_BEGIN + """
# MayaFileManager: 起動時に連携用commandPortを自動で開く（レンジ内の空きを使用）
# mfm-snippet-version: %d
""" % SNIPPET_VERSION + PORT_SNIPPET_BODY + """
_mfm_trace("userSetup bridge block reached")
try:
    import maya.utils as _mu
    _mu.executeDeferred(_mfm_open_bridge_port)
except Exception:
    pass
""" + _US_END + "\n"

# ---------------------------------------------------------------------------
# r146: Maya モジュール方式（零接触インストール）
# ---------------------------------------------------------------------------
# 実機事故 2026-10-24: 旧方式は **他人の userSetup.py を書き換えて** いた。
# 文字コードを捨てて書き戻したため Maya が起動不能になった環境が出た。
# 根本対策として、Manager は Maya 側に **自分専用のファイルだけ** を置く:
#
#   <maya app dir>/modules/MayaFileManagerBridge.mod
#   <maya app dir>/modules/MayaFileManagerBridge/scripts/userSetup.py
#
# .mod は Maya のモジュール機構（MAYA_MODULE_PATH 既定に $MAYA_APP_DIR/modules
# が含まれる）が読み、`scripts` を PYTHONPATH に足す。Maya は sys.path 上の
# userSetup.py を全部実行するので、専用フォルダの userSetup.py が走る。
# 既存の userSetup.py（ユーザー・スタジオのもの）には **一切触れない**。
# 専用ファイルは ASCII のみ・全体を try/except で包み、Maya を止めない。
MODULE_NAME = "MayaFileManagerBridge"
MODULE_MARK = "# MayaFileManager bridge (Maya module)"
MOD_FILE_TEXT = "+ %s 1.0 ./%s\n" % (MODULE_NAME, MODULE_NAME)


def _indent(text, n=4):
    pad = " " * n
    return "".join((pad + ln) if ln.strip() else ln for ln in text.splitlines(True))


MODULE_SNIPPET = (
    MODULE_MARK + "\n"
    "# Written by MayaFileManager. This folder belongs to the Manager and is\n"
    "# the only thing it writes into Maya's user folder (plus the .mod file).\n"
    "# ASCII only. Everything is wrapped so this file can never stop Maya.\n"
    "# mfm-snippet-version: %d\n"
    "try:\n" % SNIPPET_VERSION
    + _indent(PORT_SNIPPET_BODY.strip("\n") + "\n")
    + "\n"
    "    _mfm_trace(\"module userSetup bridge reached\")\n"
    "    import maya.utils as _mu\n"
    "    _mu.executeDeferred(_mfm_open_bridge_port)\n"
    "except Exception:\n"
    "    pass\n")
assert MODULE_SNIPPET.isascii(), "module userSetup.py must be ASCII only"


def open_port_snippet() -> str:
    """«既に起動している» Maya のスクリプトエディタ（Python）に貼って、
    その場で連携ポートを開くためのコード（r119）。
    userSetup.py は «次回起動から» しか効かないので、作業中の Maya を
    Manager から見えるようにする唯一の手段がこれになる。"""
    return (PORT_SNIPPET_BODY.strip() + "\n\n"
            "print('MayaFileManager bridge port: %s' % _mfm_open_bridge_port())\n")


# Maya のユーザー設定ディレクトリを «Maya に聞く» ためのコード。
# commandPort は単一の式しか返さないので式のまま保つこと。
APP_DIR_CODE = ("'MFMDIR<' + __import__('maya.cmds', fromlist=['internalVar'])"
                ".internalVar(userAppDir=True) + '>MFMDIR'")


def parse_app_dir(reply):
    """APP_DIR_CODE の応答から Maya の userAppDir を取り出す。失敗で None。"""
    import re as _re
    m = _re.search(r"MFMDIR<(.*?)>MFMDIR", reply or "", _re.S)
    if not m:
        return None
    d = (m.group(1) or "").strip().replace("/", os.sep).rstrip(os.sep)
    return d or None


# 実行中に確定した値（Maya に聞けた場合）と、ユーザー指定の上書き。
_app_dir_confirmed = None
_app_dir_override = None


def set_maya_app_dir(path, confirmed=False):
    """Maya の userAppDir を外から確定させる。
    confirmed=True は «Maya 本人に聞いた» 値（最優先で信頼する）。"""
    global _app_dir_confirmed, _app_dir_override
    if not path:
        return
    path = str(path).replace("/", os.sep).rstrip(os.sep)
    if confirmed:
        _app_dir_confirmed = path
    else:
        _app_dir_override = path
    bridge_log("maya_app_dir set: %r (confirmed=%s)" % (path, confirmed))


def app_dir_confirmed() -> bool:
    """«Maya 本人に聞いた値» を持っているか。

    注意: Manager の環境変数 MAYA_APP_DIR が設定されていても True にしない。
    MAYA_APP_DIR は Maya.env や起動用バッチで «Maya のプロセスにだけ»
    設定されていることがあり（その場合 Manager からは見えない）、逆に
    Manager 側にだけ設定されていて Maya は別の場所を使うこともある。
    聞けるなら必ず聞く。"""
    return bool(_app_dir_confirmed)


def app_dir_source() -> str:
    """現在の maya_app_dir() が «どう決まった値か» を表すキー。
    UI がそのまま文言に使う: confirmed / env / manual / found / guess。"""
    if _app_dir_confirmed:
        return "confirmed"
    if _app_dir_override:
        return "manual"
    if _os.environ.get("MAYA_APP_DIR"):
        return "env"
    for d in maya_app_dir_candidates():
        if _looks_like_maya_app_dir(d):
            return "found"
    return "guess"


def _looks_like_maya_app_dir(d):
    """«Maya の設定フォルダらしさ» を数える。0 なら候補として弱い。
    2023〜2029 のバージョンフォルダと scripts/ の有無で判定する。"""
    score = 0
    try:
        if not os.path.isdir(d):
            return 0
        for name in os.listdir(d):
            if len(name) == 4 and name.isdigit() and 2015 <= int(name) <= 2039:
                if os.path.isdir(os.path.join(d, name)):
                    score += 2
            elif name == "scripts" and os.path.isdir(os.path.join(d, name)):
                score += 1
    except OSError:
        return 0
    return score


def _known_documents():
    """Windows の «ドキュメント» 既知フォルダ（OneDrive 等へのリダイレクト反映）。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        # CSIDL_PERSONAL (=5)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            return buf.value
    except Exception:
        pass
    return None


def maya_app_dir_candidates():
    """ありうる場所を返す（重複なし）。

    先頭が %USERPROFILE%\Documents\maya であることに意味がある:
    実在性で決着が付かない時はこれを採るため（Maya は OneDrive の
    フォルダ移動を無視して実体の Documents を使う）。"""
    out = []
    home = os.path.expanduser("~")
    for base in (os.path.join(home, "Documents"), _known_documents()):
        if not base:
            continue
        d = os.path.join(base, "maya")
        if d not in out:
            out.append(d)
    return out


def maya_app_dir():
    """Maya のユーザー設定ディレクトリ（MAYA_APP_DIR 相当）を返す。

    優先順:
      1. Maya 本人に聞いた値（set_maya_app_dir(confirmed=True)）
      2. ユーザーがダイアログで指定した値
      3. 環境変数 MAYA_APP_DIR（Manager から見えている場合のみ）
      4. 候補の «実物らしさ»（20xx のバージョンフォルダ / scripts の有無）
      5. %USERPROFILE%/Documents/maya

    MAYA_APP_DIR を 1 より上に置かないのは、これが «Maya のプロセスにだけ»
    設定されていることがあるため（Maya.env・起動バッチ・ランチャー）。
    Manager 側に見えている値が Maya の実際の値とは限らず、逆もある。
    聞けるなら必ず Maya 本人に聞く。

    注意: «ドキュメント» 既知フォルダが OneDrive へリダイレクトされていても、
    Maya がそこを使うとは限らない（2026-10 実機: 既知フォルダは
    OneDrive 配下の「ドキュメント」なのに、Maya の
    internalVar(userAppDir=True) は %USERPROFILE%/Documents/maya）。
    «推測» で書くと Maya が読まない場所に userSetup.py を置くことになり、
    連携が «入れたのに効かない» 状態になる。だから実在性で選び、
    可能なら Maya 本人に聞いた値で上書きする。"""
    if _app_dir_confirmed:
        return _app_dir_confirmed
    if _app_dir_override:
        return _app_dir_override
    env = os.environ.get("MAYA_APP_DIR")
    if env:
        return env
    cands = maya_app_dir_candidates()
    best, best_score = None, 0
    for d in cands:
        sc = _looks_like_maya_app_dir(d)
        if sc > best_score:
            best, best_score = d, sc
    if best:
        return best
    # どちらも実在しない（＝まだ一度も Maya を起動していない）場合は
    # %USERPROFILE%\Documents\maya を採る。Maya は OneDrive の
    # フォルダ移動を無視して実体の Documents を使うため（2026-10 実機で確認）、
    # 既知フォルダ側へ作ると «Maya が読まないファイル» ができてしまう。
    return (cands[0] if cands
            else os.path.join(os.path.expanduser("~"), "Documents", "maya"))


def usersetup_path(version=None):
    """userSetup.py のパス。

    version=None … **全バージョン共通**（`<maya app dir>/scripts/userSetup.py`）
    version="2026" … そのバージョン専用
      （`<maya app dir>/2026/scripts/userSetup.py`）

    【r120・検証済み】**Maya は見つかった userSetup.py を «全部» 実行する。**
    r119c では「最初に見つかった 1 つだけ」という前提でバージョン別への
    退避を促していたが、これは誤りだった。2026-10-02 の実機ログで、1 回の
    Maya 起動に対してスニペットの痕跡が 2 行（共通＋バージョン別）出ることを
    確認している。したがって **共通に 1 つ書けば全バージョンに効く**。
    バージョン別は «そのバージョンだけに効かせたい» 時の選択肢にすぎない。
    なお 2 回実行されてもポートは重複しない（スニペット側で
    「この Maya が既に開いていれば何もしない」を見ている）。"""
    import os
    base = maya_app_dir()
    if version:
        return os.path.join(base, str(version), "scripts", "userSetup.py")
    return os.path.join(base, "scripts", "userSetup.py")


def installed_versions():
    """Maya の設定フォルダ直下にあるバージョンフォルダ（新しい順）。"""
    import os
    out = []
    try:
        for name in os.listdir(maya_app_dir()):
            if len(name) == 4 and name.isdigit() and 2015 <= int(name) <= 2039:
                if os.path.isdir(os.path.join(maya_app_dir(), name)):
                    out.append(name)
    except OSError as _e:
        _swallow(_e, "core/maya_bridge.py installed_versions")
    out.sort(reverse=True)
    return out


def modules_dir(version=None):
    """Maya のモジュール置き場。version=None で全バージョン共通
    （`<maya app dir>/modules`）、"2026" でそのバージョン専用。"""
    base = maya_app_dir()
    if version:
        return os.path.join(base, str(version), "modules")
    return os.path.join(base, "modules")


def bridge_mod_path(version=None):
    """Manager が書く .mod ファイルのパス。"""
    return os.path.join(modules_dir(version), MODULE_NAME + ".mod")


def bridge_module_root(version=None):
    """Manager 専用のモジュールフォルダ（.mod の隣）。"""
    return os.path.join(modules_dir(version), MODULE_NAME)


def bridge_module_usersetup(version=None):
    """Manager 専用の userSetup.py（モジュールフォルダ内）。"""
    return os.path.join(bridge_module_root(version), "scripts", "userSetup.py")


def _module_usersetup_text(version=None):
    p = bridge_module_usersetup(version)
    try:
        with open(p, "rb") as f:
            return f.read().decode("ascii", "replace")
    except OSError:
        return None


def is_module_installed(version=None) -> bool:
    """モジュール方式で入っているか（.mod と専用 userSetup.py の両方がある）。"""
    if not os.path.isfile(bridge_mod_path(version)):
        return False
    text = _module_usersetup_text(version)
    return bool(text and MODULE_MARK in text)


def module_snippet_version(version=None):
    """モジュール方式で入っている版。入っていなければ None。"""
    if not is_module_installed(version):
        return None
    import re as _re
    m = _re.search(r"mfm-snippet-version:\s*(\d+)", _module_usersetup_text(version) or "")
    return int(m.group(1)) if m else 0


def install_bridge(version=None) -> str:
    """連携をモジュール方式で入れる。書いた .mod のパスを返す。

    書くのは Manager 専用の 2 ファイルだけ。既存の userSetup.py には触れない
    （旧方式のブロックの除去は remove_legacy_block() が別に行う）。"""
    root = bridge_module_root(version)
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    us = bridge_module_usersetup(version)
    with open(us, "w", encoding="ascii", newline="\n") as f:
        f.write(MODULE_SNIPPET)
    mod = bridge_mod_path(version)
    with open(mod, "w", encoding="ascii", newline="\n") as f:
        f.write(MOD_FILE_TEXT)
    bridge_log("install_bridge: wrote %r and %r (module, snippet v%d)"
               % (mod, us, SNIPPET_VERSION))
    return mod


# 旧名。呼び出し元の互換のために残す（中身はモジュール方式）。
install_usersetup = install_bridge


def uninstall_bridge(version=None) -> bool:
    """モジュール方式のファイルを取り除く。何か消したら True。"""
    import shutil
    done = False
    mod = bridge_mod_path(version)
    if os.path.isfile(mod):
        os.remove(mod)
        done = True
    root = bridge_module_root(version)
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
        done = True
    if done:
        bridge_log("uninstall_bridge: removed %r" % (root,))
    return done


# ---------------------------------------------------------------------------
# 旧方式（他人の userSetup.py に挟んだブロック）: 検出と安全な除去だけ
# ---------------------------------------------------------------------------
def _legacy_raw(version=None):
    p = usersetup_path(version)
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def legacy_block_present(version=None) -> bool:
    """旧方式のブロックが userSetup.py に残っているか。
    バイト列で目印を探すので、文字コードが壊れていても判定できる。"""
    raw = _legacy_raw(version)
    return bool(raw) and _US_BEGIN.encode("ascii") in raw


def legacy_snippet_version(version=None):
    raw = _legacy_raw(version)
    if not raw or _US_BEGIN.encode("ascii") not in raw:
        return None
    import re as _re
    text = raw.decode("utf-8", "replace")
    block = text.split(_US_BEGIN, 1)[1].split(_US_END, 1)[0]
    m = _re.search(r"mfm-snippet-version:\s*(\d+)", block)
    return int(m.group(1)) if m else 0


def remove_legacy_block(version=None):
    """旧方式のブロックを userSetup.py から取り除く。

    取り除いたファイルのパスを返す。ブロックが無ければ None。
    文字コードが判別できなければ **触らず** ValueError。

    実機事故 2026-10-24 の反省をそのまま守る:
      * 必ずバックアップ（userSetup.py.mfm_bak_<日時>）
      * 文字コードはファイルのまま（coding 宣言 → utf-8 → cp932、厳密）
      * 改行もファイルのまま
      * ブロック以外の 1 文字も変えない
      * 取り除いた結果が空なら（元々 Manager が作ったファイル）ファイルごと消す
        — バックアップは残る"""
    p = usersetup_path(version)
    raw = _legacy_raw(version)
    if not raw or _US_BEGIN.encode("ascii") not in raw:
        return None
    enc = _detect_usersetup_encoding(raw)
    if enc is None:
        bridge_log("remove_legacy_block: %r は utf-8 でも cp932 でも読めないので触らない" % (p,))
        raise ValueError(
            "userSetup.py の文字コードが判別できないため書き換えません: %s" % p)
    text = raw.decode(enc)
    if "\ufffd" in text:
        bridge_log("remove_legacy_block: %r には置換文字 U+FFFD が含まれる（旧版の"
                   "インストーラが壊した可能性）。ブロックだけ取り除く" % (p,))
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = text.replace("\r\n", "\n")
    import datetime as _dt
    bak = p + ".mfm_bak_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(bak, "wb") as f:
        f.write(raw)
    head = text.split(_US_BEGIN, 1)[0]
    rest = text.split(_US_BEGIN, 1)[1]
    if _US_END in rest:
        tail = rest.split(_US_END, 1)[1]
    else:
        tail = ""           # 終端が無い（壊れている）→ 目印以降を捨てる
    if tail.startswith("\n"):
        tail = tail[1:]
    out = head + tail
    if not out.strip():
        os.remove(p)
        bridge_log("remove_legacy_block: %r は連携ブロックだけだったので削除 (backup %r)"
                   % (p, bak))
        return p
    with open(p, "w", encoding=enc, newline=newline) as f:
        f.write(out)
    bridge_log("remove_legacy_block: removed block from %r (enc=%s, backup %r)"
               % (p, enc, bak))
    return p


def _detect_usersetup_encoding(raw: bytes):
    """userSetup.py の文字コード。coding 宣言があればそれ、無ければ
    utf-8 → cp932 の順に厳密に試す。どれでも読めなければ None。"""
    import re as _re
    head = raw[:200]
    m = _re.search(rb"coding[:=]\s*([-\w.]+)", head)
    if m:
        name = m.group(1).decode("ascii", "replace").lower()
        try:
            raw.decode(name)
            return name
        except (LookupError, UnicodeDecodeError):
            pass
    for name in ("utf-8", "cp932"):
        try:
            raw.decode(name)
            return name
        except UnicodeDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# 呼び出し元向けのまとめ（モジュール方式＋旧方式を合わせて見る）
# ---------------------------------------------------------------------------
def is_usersetup_installed(version=None) -> bool:
    """連携がその場所に入っているか（モジュール方式または旧方式）。"""
    return is_module_installed(version) or legacy_block_present(version)


def installed_snippet_version(version=None):
    """入っている版。モジュール方式を優先。入っていなければ None。"""
    v = module_snippet_version(version)
    if v is not None:
        return v
    return legacy_snippet_version(version)


def usersetup_is_outdated(version=None) -> bool:
    """入ってはいるが «古い» か。旧方式のブロックが残っていれば常に古い
    （モジュール方式へ移行させる）。モジュール方式なら版で判断。"""
    if legacy_block_present(version):
        return True
    v = module_snippet_version(version)
    return v is not None and v < SNIPPET_VERSION


def outdated_targets():
    """古い連携が入っている場所の一覧（None=共通 / "2026" …）。"""
    out = []
    if usersetup_is_outdated(None):
        out.append(None)
    for v in installed_versions():
        if usersetup_is_outdated(v):
            out.append(v)
    return out


def legacy_targets():
    """旧方式のブロックが残っている場所の一覧。"""
    out = []
    if legacy_block_present(None):
        out.append(None)
    for v in installed_versions():
        if legacy_block_present(v):
            out.append(v)
    return out


def has_own_usersetup(version) -> bool:
    """そのバージョンが «自前の userSetup.py» を持っているか（中身は問わない）。
    表示用。有効/無効の判定には使わない。"""
    return os.path.isfile(usersetup_path(version))


def bridge_effective_for(version) -> bool:
    """そのバージョンの Maya で連携が効くか。共通かそのバージョン専用の
    どちらかに入っていれば効く（Maya は両方読む）。"""
    return is_usersetup_installed(None) or is_usersetup_installed(version)


def ineffective_versions():
    """連携が効かないバージョンの一覧（共通にもバージョン別にも無い）。"""
    return [v for v in installed_versions() if not bridge_effective_for(v)]


def installed_targets():
    """連携が入っている場所の一覧。[None(=共通), "2026", ...]。"""
    out = []
    if is_usersetup_installed(None):
        out.append(None)
    for v in installed_versions():
        if is_usersetup_installed(v):
            out.append(v)
    return out


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

    def send_python(self, code: str, timeout: float = 3.0, tag: str = ""):
        """Python コードを送信する。

        Returns:
            (ok, reply):
              ok=True  … 送信成功。reply は Maya からの応答文字列
                         （長時間処理で応答待ちを打ち切った場合は None）
              ok=False … 接続/送信失敗。reply はエラーメッセージ
        """
        # r119e: «誰が・いつ・何を» 送ったかを必ず残す。
        # 「シーンを開いた後にもう一度コマンドが走る」の調査で、送信の記録が
        # 無いために推測しかできなかった。二重送信はログで一発で分かる。
        try:
            import threading as _th
            import traceback as _tb
            head = (code or "").strip().splitlines()[0][:120] if code else ""
            caller = ""
            for fr in reversed(_tb.extract_stack()[:-1]):
                if "maya_bridge.py" not in fr.filename:
                    caller = "%s:%d %s" % (_os.path.basename(fr.filename),
                                           fr.lineno, fr.name)
                    break
            # r145: «どの操作か» が分からないログは調査の役に立たない
            # （実機 2026-10-08: リファレンス／インポートが 2 回実行された件で、
            #  1 行目だけでは両者を区別できず原因を絞れなかった）。
            # tag（操作ラベル）と呼び出し経路の積み上げを必ず残す。
            trail = " < ".join(
                "%s:%d %s" % (_os.path.basename(fr.filename), fr.lineno, fr.name)
                for fr in reversed(_tb.extract_stack()[:-1])
                if "maya_bridge.py" not in fr.filename)
            bridge_log("send port=%s tag=%r thread=%s from=%s len=%d sha=%s\n"
                       "        trail: %s\n        code1=%r"
                       % (self.port, tag, _th.current_thread().name, caller,
                          len(code or ""),
                          __import__("hashlib").sha1(
                              (code or "").encode("utf-8", "replace")
                          ).hexdigest()[:10],
                          trail[-600:], head))
        except Exception:
            pass
        try:
            s = self._connect(1.5)
        except OSError as e:
            bridge_log("send port=%s FAILED connect: %s" % (self.port, e))
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
