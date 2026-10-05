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
def _mfm_trace(_msg):
    # r120: «userSetup が実行されたか» を後から切り分けられるようにする。
    # Manager 側のログ（~/.maya_file_manager/logs/mfm_maya.log）に相乗りする。
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
    # 1) この Maya が «既に» レンジ内のポートを開いているなら何もしない。
    #    マネージャーから起動した Maya は -command で先にポートを開くので、
    #    ここで «別の空きポート» を開くと 1 つの Maya が 2 回接続リストに
    #    出てしまう（r119b）。commandPort(q=True) は «この Maya が開いて
    #    いるか» を答える（他プロセスのポートは False）。
    for _p in _ports:
        try:
            if _cmds.commandPort(":%%d" %% _p, q=True):
                _mfm_trace("already open port %%d" %% _p)
                return _p
        except Exception:
            pass
    # 2) 開いていなければ、空いているポートを順に試す。
    #    他の Maya が使っているポートは open が失敗するので次へ。
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
# 古い版が入ったままだと «入れたのに効かない» が起きるので、Manager が
# 検出して更新を促せるようにする（r119b）。
SNIPPET_VERSION = 4

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


def _usersetup_text(version=None):
    import os
    p = usersetup_path(version)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def is_usersetup_installed(version=None) -> bool:
    text = _usersetup_text(version)
    return bool(text and _US_BEGIN in text)


def installed_snippet_version(version=None):
    """入っているスニペットの版。入っていなければ None、版表記が無い古い物は 0。"""
    text = _usersetup_text(version)
    if not text or _US_BEGIN not in text:
        return None
    import re as _re
    block = text.split(_US_BEGIN, 1)[1].split(_US_END, 1)[0]
    m = _re.search(r"mfm-snippet-version:\s*(\d+)", block)
    return int(m.group(1)) if m else 0


def usersetup_is_outdated(version=None) -> bool:
    """入ってはいるが «古い版» か。

    r119b: 0.9.2 が書いた版には Manager 側の関数（_swallow）が紛れ込んで
    おり、Maya 側で NameError になってポートが開かなかった。入れ直さない
    限り直らないので、Manager 側から検出して更新を促す。"""
    v = installed_snippet_version(version)
    return v is not None and v < SNIPPET_VERSION


def outdated_targets():
    """古いスニペットが入っている場所の一覧（None=共通 / "2026" …）。"""
    out = []
    if usersetup_is_outdated(None):
        out.append(None)
    for v in installed_versions():
        if usersetup_is_outdated(v):
            out.append(v)
    return out


def has_own_usersetup(version) -> bool:
    """そのバージョンが «自前の userSetup.py» を持っているか（中身は問わない）。

    r120: これがあっても共通側は «隠されない»（Maya は両方実行する）。
    表示用の情報として残してあるだけで、有効/無効の判定には使わない。"""
    import os
    return os.path.isfile(usersetup_path(version))


def bridge_effective_for(version) -> bool:
    """そのバージョンの Maya で連携が効くか。

    r120: 共通（全バージョン）に入っていれば効く。そのバージョン専用に
    入っていても効く。どちらかにあれば良い。"""
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


def _split_usersetup_header(text: str):
    """shebang / エンコーディング宣言だけを «先頭に残す» ために切り出す。
    PEP 263 の coding 宣言はファイルの 1〜2 行目にしか効かないので、
    連携ブロックはその «直後» に入れなければならない。"""
    lines = (text or "").split("\n")
    head = []
    for i, ln in enumerate(lines[:2]):
        s = ln.strip()
        if i == 0 and s.startswith("#!"):
            head.append(ln)
            continue
        if s.startswith("#") and "coding" in s:
            head.append(ln)
            continue
        break
    rest = "\n".join(lines[len(head):])
    return (("\n".join(head) + "\n") if head else ""), rest


def install_usersetup(version=None) -> str:
    """連携スニペットを userSetup.py の «先頭» へ挿入（既存ブロックは置換）。
    書き込んだファイルのパスを返す。version=None で全バージョン共通。

    r120: 以前は «末尾へ追記» していた。userSetup.py は Python の import で
    読まれるので、既存の中身が途中で例外を出すと «そこで打ち切られ»、
    末尾の連携ブロックは永久に実行されない。実機で全バージョンに
    Bridge=True が入っているのにポートが 1 つも開かない（open ports: []）
    状態になったのがこれ。先頭に置けば、他人のコードが何をしようと
    連携だけは生き残る。
    """
    import os
    p = usersetup_path(version)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    text = ""
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    # 既存ブロック（旧版・旧位置＝末尾にあるものを含む）をいったん取り除く
    if _US_BEGIN in text and _US_END in text:
        head = text.split(_US_BEGIN)[0]
        tail = text.split(_US_END, 1)[1]
        text = head.rstrip("\n")
        if text:
            text += "\n"
        text += tail.lstrip("\n")
    header, body = _split_usersetup_header(text)
    text = header + _US_SNIPPET
    if body.strip():
        text += "\n" + body.lstrip("\n")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    bridge_log("install_usersetup: wrote %r (head-insert, snippet v%d)"
               % (p, SNIPPET_VERSION))
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
            bridge_log("send port=%s thread=%s from=%s code=%r"
                       % (self.port, _th.current_thread().name, caller, head))
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
