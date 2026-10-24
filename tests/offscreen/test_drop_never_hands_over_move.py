# -*- coding: utf-8 -*-
"""r146（データ消失の根治）: 外から落とされたファイルに、OS へ «移動した» と
答えてはいけない。

ユーザー報告 2026-10-24（A-18）:
  「外からコンパクトを解除してドロップしたデータが消えました。元の場所にも
    無いし、ドロップした場所にも無いです。移動でもコピーでも無くなるのは
    絶対 NG です。」

原因: Windows の D&D では、受け手が Drop に MoveAction を答えると、送り手
（Explorer）が «非最適化移動» として元ファイルを自分で消す。こちらは
«コピー／移動» をメニューで選ばせてから処理するので、選んだ時には元が
無い。さらに _on_files_dropped は «元が無いものは黙って飛ばす» ので、
何の知らせも無く消えていた。コンパクトに限らず、本体のカラムへの
修飾キー無しのドロップ全般、ブックマーク欄へのドロップも同じ型だった。

ここでは «Explorer の振る舞い»（MoveAction と答えられたら元を消す）を
再現した送り手から、ユーザーの実際の経路で落とす:
  1. 本体のカラムへ → メニューで «コピー» → 元も先もある
  2. 本体のカラムへ → メニューで «移動» → 先にだけある（こちらが動かす）
  3. コンパクトのパックへ → 同上
  4. ブックマーク欄（ツリー／パネル）、プリセット欄、クイックナビ欄 → 元が残る
  5. 元が既に無い → 黙らずに知らせ、ログに残す
  6. 全ての dropEvent / Drop 分岐が accept_drop_as_copy を通る（AST）"""
import ast
import glob
import io
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, tmpdir, finish, QTest

from core.compat import Qt, QtCore, QtGui, QUrl, QMimeData, QListView, QMessageBox
import ui.browser_util as _bu

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def settle(ms=120):
    QTest.qWait(ms)
    app.processEvents()


def write(p, text):
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


_KEEP = []          # QDropEvent は mime を所有しない。Python 側で抱えておく


def make_drop(path, mods=Qt.NoModifier):
    mime = QMimeData()
    _KEEP.append(mime)
    mime.setUrls([QUrl.fromLocalFile(path)])
    ev = QtGui.QDropEvent(QtCore.QPointF(10, 10),
                          Qt.CopyAction | Qt.MoveAction | Qt.LinkAction,
                          mime, Qt.LeftButton, mods, QtCore.QEvent.Drop)
    # Explorer は同じドライブ内なら «移動» を提案してくる
    ev.setDropAction(Qt.MoveAction)
    return ev


def explorer_drops(path, deliver, mods=Qt.NoModifier):
    """Explorer を真似た送り手。受け手が MoveAction と答えたら、
    DoDragDrop から戻った直後に «元ファイルを自分で消す»（非最適化移動）。"""
    ev = make_drop(path, mods)
    deliver(ev)
    answered = ev.dropAction() if ev.isAccepted() else None
    if answered == Qt.MoveAction and os.path.exists(path):
        os.remove(path)
    return answered


root = tmpdir()
SRC = os.path.join(root, "src")
DST = os.path.join(root, "dst")
os.makedirs(SRC)
os.makedirs(DST)

# ── 本体のカラム ─────────────────────────────────────────────────
b = make_panel(1300, 700)
b.navigate_to(DST)
settle(3600)          # navigate 直後 2.6 秒の作り直しを越える


def dst_view():
    for _ in range(80):
        for v in b._column_view.findChildren(QListView):
            if not v.isVisible():
                continue
            p = b._column_view._path_for_index(v.rootIndex()) or ""
            if p and os.path.normcase(os.path.normpath(p)) == os.path.normcase(DST):
                return v
        settle(100)
    return None


view = dst_view()
check(view is not None, "前提: 落とし先のカラムが出ている")

chosen = {"action": None}
b._ask_drop_action = lambda *_a, **_k: chosen["action"]


def run_ops():
    # ワーカーの完了まで待つ
    for _ in range(60):
        settle(100)
        if not getattr(b, "_file_op_running", False):
            pass
    settle(300)


def drop_on_column(ev):
    b._column_view.eventFilter(view.viewport(), ev)


for action in ("copy", "move"):
    src = os.path.join(SRC, "data_%s.ma" % action)
    write(src, "payload-" + action)
    chosen["action"] = action
    answered = explorer_drops(src, drop_on_column)
    check(answered == Qt.CopyAction,
          "カラム（%s）: OS へは «コピー» と答える（答え=%r）" % (action, answered))
    run_ops()
    dst = os.path.join(DST, os.path.basename(src))
    check(os.path.exists(dst) and read(dst) == "payload-" + action,
          "カラム（%s）: 落とした先にある" % action)
    if action == "copy":
        check(os.path.exists(src), "カラム（copy）: 元も残っている")
    else:
        check(not os.path.exists(src), "カラム（move）: こちらが動かしたので元は無い")

# Ctrl / Shift で確定するドロップも同じ
for mods, action in ((Qt.ControlModifier, "copy"), (Qt.ShiftModifier, "move")):
    src = os.path.join(SRC, "mod_%s.ma" % action)
    write(src, "mod-" + action)
    answered = explorer_drops(src, drop_on_column, mods)
    check(answered == Qt.CopyAction, "カラム（修飾キー %s）: «コピー» と答える" % action)
    run_ops()
    dst = os.path.join(DST, os.path.basename(src))
    check(os.path.exists(dst), "カラム（修飾キー %s）: 落とした先にある" % action)
    check(os.path.exists(src) == (action == "copy"),
          "カラム（修飾キー %s）: 元の有無が正しい" % action)

# ── コンパクトのパック ────────────────────────────────────────────
import ui.compact_mode as _cm


class _Win(object):
    _browser = b


class _Ctl(object):
    _win = _Win()
    expanded = []

    def expand(self, **k):
        self.expanded.append(k)


ctl = _Ctl()
ctl.drop_on_puck = lambda mime: _cm.CompactController.drop_on_puck(ctl, mime)


class _PuckSelf(object):
    controller = ctl
    _has_files = staticmethod(_cm._Puck._has_files)


b.current_path = lambda: DST
for action in ("copy", "move"):
    src = os.path.join(SRC, "puck_%s.ma" % action)
    write(src, "puck-" + action)
    chosen["action"] = action
    answered = explorer_drops(src, lambda ev: _cm._Puck.dropEvent(_PuckSelf(), ev))
    check(answered == Qt.CopyAction, "パック（%s）: «コピー» と答える" % action)
    run_ops()
    dst = os.path.join(DST, os.path.basename(src))
    check(os.path.exists(dst) and read(dst) == "puck-" + action,
          "パック（%s）: 落とした先にある" % action)
    check(os.path.exists(src) == (action == "copy"),
          "パック（%s）: 元の有無が正しい" % action)

# ── ファイルを «受け取らない» 欄（パスを読むだけ）───────────────
from ui.bookmark_panel import BookmarkTree, BookmarkPanel
from ui.preset_editor import ReferenceEntryWidget
from ui.quick_nav_editor import NavItemRow

keep = os.path.join(SRC, "keep_me.ma")
write(keep, "keep")
keep_dir = os.path.join(SRC, "keep_dir")
os.makedirs(keep_dir)

tree = BookmarkTree()
got = []
tree.external_paths_dropped.connect(lambda ps: got.append(ps))
answered = explorer_drops(keep, tree.dropEvent)
check(got and os.path.exists(keep), "ブックマーク（ツリー）: 登録しても元は消えない（答え=%r）" % (answered,))


class _BMSelf(object):
    def __init__(self):
        self.got = []

    def _on_external_paths(self, ps):
        self.got.append(ps)


bms = _BMSelf()
explorer_drops(keep, lambda ev: BookmarkPanel.dropEvent(bms, ev))
check(bms.got and os.path.exists(keep), "ブックマーク（パネル）: 元は消えない")

ref = ReferenceEntryWidget({"namespace": "", "path": "", "enabled": True})
explorer_drops(keep, ref.dropEvent)
check(os.path.exists(keep), "プリセット欄: 元は消えない")

row = NavItemRow({"label": "", "path": ""})
explorer_drops(keep_dir, row.dropEvent)
check(os.path.isdir(keep_dir), "クイックナビ欄: 元は消えない")

# ── 元が既に無い → 黙らずに知らせ、ログに残す ───────────────────
warned = []
_orig_warn = QMessageBox.warning
QMessageBox.warning = staticmethod(lambda *a, **k: (warned.append(a), QMessageBox.Ok)[1])
gone = os.path.join(SRC, "already_gone.ma")
chosen["action"] = "copy"
b._on_files_dropped([gone], DST, None)
settle(200)
QMessageBox.warning = _orig_warn
check(warned and any(gone in str(x) for x in warned[-1]),
      "元が無い時は知らせる（黙って飛ばさない）")
log = os.path.join(_bu._MFM_LOG_DIR, "mfm_fileops.log")
check(os.path.isfile(log) and "MISSING" in io.open(log, encoding="utf-8").read(),
      "ファイル操作のログに残る: %s" % log)

# ── 構造の見張り（AST）─────────────────────────────────────────────
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def _calls(node, name):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nm == name:
                out.append(n)
    return out


def _mentions_move(node):
    return any(isinstance(n, ast.Attribute) and n.attr == "MoveAction"
               for n in ast.walk(node))


bad = []
for path in glob.glob(os.path.join(ROOT, "ui", "*.py")) + glob.glob(os.path.join(ROOT, "core", "*.py")):
    tree_ = ast.parse(io.open(path, encoding="utf-8").read())
    rel = os.path.relpath(path, ROOT)
    for fn in ast.walk(tree_):
        if not isinstance(fn, ast.FunctionDef):
            continue
        if fn.name == "dropEvent":
            if _calls(fn, "acceptProposedAction"):
                bad.append("%s:%d dropEvent が acceptProposedAction（移動を答え得る）" % (rel, fn.lineno))
            if _mentions_move(fn):
                bad.append("%s:%d dropEvent が MoveAction を扱う" % (rel, fn.lineno))
        if fn.name == "_handle_view_drag":
            for n in ast.walk(fn):
                if isinstance(n, ast.If) and any(
                        isinstance(x, ast.Attribute) and x.attr == "Drop"
                        for x in ast.walk(n.test)):
                    body = ast.Module(body=n.body, type_ignores=[])
                    if _mentions_move(body):
                        bad.append("%s:%d Drop 分岐が MoveAction を答える" % (rel, n.lineno))
                    if not _calls(body, "accept_drop_as_copy"):
                        bad.append("%s:%d Drop 分岐が accept_drop_as_copy を通らない" % (rel, n.lineno))
check(not bad, "全てのドロップの受け口が «コピー» で答える: %s" % (bad or "OK"))

if fails:
    print("FAILED:", fails)
finish(not fails)
