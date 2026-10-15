# -*- coding: utf-8 -*-
"""r146: 「全部壊れている」ように見えた 4 件（ユーザー報告 2026-10-11）。

  A. 削除もエクスプローラー表示も保存も無反応
     → 最前面表示を Qt のフラグでやっていたため、本体から出した
       モーダルダイアログが Windows の TOPMOST 規則で «本体の裏» に回り、
       入力がそこで止まっていた。フラグを使わない実装へ。
  B. 各ウィンドウの ✕ が押せない
     → 同じフラグ変更の副作用（adjustFlags がタイトルバー部品を足さなくなる）。
  C. エクスプローラーで表示が効かない
     → explorer の引数をリストで渡していて «/select, path» と空白が入り、
       エクスプローラーが解釈できなかった。
  D. 命名ルールが意味のわからない壊れ方
     → 接続中シーン名を初期値に入れたせいで «ファイルを選んでいる» 扱いになり、
       自動命名が一度も働かなかった。変数名の重複表示も «壊れて見える» 一因。
"""
import io
import os
import re

from _common import *  # noqa: F401,F403
from _common import finish, sm

from core.compat import Qt
from ui import dialog_util

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# ── A/B: 最前面表示はフラグを触らない ───────────────────────────────
src = io.open(os.path.join(ROOT, "ui", "main_window.py"), encoding="utf-8").read()
# 説明文に名前が出るのは正常。«フラグを立てる呼び出し» が無いことを見る。
import ast as _ast
_flagged = []
for node in _ast.walk(_ast.parse(src)):
    if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute) \
            and node.func.attr in ("setWindowFlag", "setWindowFlags"):
        for a in node.args:
            if getattr(a, "attr", "") == "WindowStaysOnTopHint":
                _flagged.append(node.lineno)
check(not _flagged,
      "main_window が最前面フラグを立てない（✕ が消える原因）（%r）" % (_flagged,))
check("win_topmost" in src, "SetWindowPos 側の実装を使っている")

from core import win_topmost
check(hasattr(win_topmost, "set_topmost"), "set_topmost がある")

from ui.topmost_keeper import TopmostKeeper
check(hasattr(TopmostKeeper, "eventFilter") and hasattr(TopmostKeeper, "set_active"),
      "新しく出た窓も一緒に最前面へ上げるお守り役がある")

# ── B: ダイアログの ✕ ─────────────────────────────────────────────
f = dialog_util.window_flags_with_buttons(Qt.Dialog)
check(bool(f & Qt.WindowCloseButtonHint), "✕ のヒントが立つ")
check(bool(f & Qt.WindowSystemMenuHint), "システムメニューも立つ（✕ の前提）")
check(not (f & Qt.WindowContextHelpButtonHint), "？ボタンは落とす")
f2 = dialog_util.window_flags_with_buttons(Qt.Window, minmax=True)
check(bool(f2 & Qt.WindowMinimizeButtonHint)
      and bool(f2 & Qt.WindowMaximizeButtonHint),
      "minmax=True で最小化・最大化も立つ")

# «素の & ~WindowContextHelpButtonHint» が残っていないこと。
# これをやると customize 扱いになり、✕ が付かなくなる。
raw = []
pat = re.compile(r"windowFlags\(\)\s*&\s*~")
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "ui")):
    if "__pycache__" in dirpath:
        continue
    for name in sorted(files):
        if not name.endswith(".py") or name == "dialog_util.py":
            continue
        path = os.path.join(dirpath, name)
        for i, line in enumerate(io.open(path, encoding="utf-8"), 1):
            if pat.search(line):
                raw.append("%s:%d" % (name, i))
check(not raw, "フラグの素の削り方が残っていない（%s）" % (", ".join(raw) or "なし"))

# すべての QDialog が ✕ を用意している（1 つ漏れると «その窓だけ押せない»）
missing = []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "ui")):
    if "__pycache__" in dirpath:
        continue
    for name in sorted(files):
        if not name.endswith(".py"):
            continue
        text = io.open(os.path.join(dirpath, name), encoding="utf-8").read()
        if re.search(r"^class \w+\(QDialog\):", text, re.M) \
                and "apply_window_buttons" not in text:
            missing.append(name)
check(not missing,
      "すべての QDialog が ✕ を用意している（%s）" % (", ".join(missing) or "漏れなし"))

# ── C: エクスプローラーで表示 ─────────────────────────────────────
fo = io.open(os.path.join(ROOT, "core", "file_operations.py"), encoding="utf-8").read()
reveal = fo[fo.index("def reveal_in_explorer"):]
reveal = reveal[:reveal.index("\ndef ", 1)]
_popen = []
for node in _ast.walk(_ast.parse(fo)):
    if isinstance(node, _ast.Call) and getattr(node.func, "attr", "") == "Popen":
        for a in node.args:
            if isinstance(a, _ast.List) and a.elts \
                    and getattr(a.elts[0], "value", "") == "explorer":
                _popen.append(node.lineno)
check(not _popen,
      "explorer をリストで渡さない（空白が入って壊れる形）（%r）" % (_popen,))
check('explorer /select,"%s"' in reveal,
      "1 引数の正しい形で渡している")
check("p.parent.exists()" in reveal,
      "実体が無い時も黙って return しない（親を開くか理由を投げる）")

from _common import panel_source
bp = panel_source()
check("_reveal_path" in bp, "失敗をユーザーに伝える経路がある")

# ── D: 自動命名 ───────────────────────────────────────────────────
dcc = io.open(os.path.join(ROOT, "ui", "main_window_dcc.py"), encoding="utf-8").read()
check("initial_is_selection=picked" in dcc,
      "接続中シーン名を «選択» として渡さない")

from ui.save_dialog import SaveDialog
import inspect
sig = inspect.signature(SaveDialog.__init__)
check("initial_is_selection" in sig.parameters,
      "保存ダイアログが «選択由来かどうか» を受け取る")

# 実際に効くか: 一時フォルダへルールを置き、シーン名を初期値にして開く
import tempfile
base = tempfile.mkdtemp(prefix="mfm_r146_")
target = os.path.join(base, "CHR", "Cast", "SK_CHR_Gee", "Models")
os.makedirs(target)
rule = {
    "levels": "{Section}/{Category}/SK_{Section}_{Chara}/{Flow}",
    "filename": "SK_{Section}_{Chara}",
    "ver": {"latest_label": "cur", "prefix": "v", "digits": 3,
            "incremental": False},
}
sm.set("auto_naming_enabled", True, save=False)
sm._global_state["auto_naming_by_preset"] = {
    "T": {"enabled": True, "rules": {base: rule}}}

from core import auto_name as _an
vals = _an.match_levels(base, target, rule["levels"])
check(vals == {"Section": "CHR", "Category": "Cast",
               "Chara": "Gee", "Flow": "Models"},
      "同じ変数を 2 段で使っても値が揃う（%r）" % (vals,))

dlg = SaveDialog("maya", "save", target, "SomeOpenScene.ma", sm,
                 initial_is_selection=False)
got = os.path.splitext(dlg._name_edit.text())[0]
check(got == "SK_CHR_Gee",
      "シーン名が入っていても自動命名が働く（%r）" % (dlg._name_edit.text(),))
check(dlg._name_edit.text().lower().endswith(".ma"),
      "拡張子は保存時に付く（%r）" % (dlg._name_edit.text(),))
_txt = dlg._auto_rule_text()
check("SK_{Section}_{Chara}" in _txt and "Chara=Gee" in _txt,
      "どのルールが効いているか画面に出る（%r）" % (_txt,))

dlg2 = SaveDialog("maya", "save", target, "Picked.ma", sm,
                  initial_is_selection=True)
check(os.path.splitext(dlg2._name_edit.text())[0] == "Picked",
      "選んだファイルがある時はその名前のまま（%r）" % (dlg2._name_edit.text(),))

dlg.deleteLater()
dlg2.deleteLater()

# 変数リストの重複表示
sd = io.open(os.path.join(ROOT, "ui", "settings_dialog.py"), encoding="utf-8").read()
check("uniq" in sd and 'names.count(n) > 1' in sd,
      "変数の重複を畳んで出す（Section が 2 回出ない）")

# ── ヘッダ: Maya の起動設定（引数指定）ボタンの位置 ──────────────
# ユーザー指示 2026-10-13:
#   「maya の引数指定のボタンは、maya バージョン表示の左側に置いてください。」
from ui.dcc_header import DccHeader
hdr = DccHeader()
hdr.resize(620, 56)
hdr.show()
app.processEvents()
check(hdr.maya_setup_btn.x() < hdr.maya_ver.x(),
      "起動設定ボタンが Maya バージョンの «左» にある（btn=%d ver=%d）"
      % (hdr.maya_setup_btn.x(), hdr.maya_ver.x()))
check(hdr.maya_ver.x() < hdr.maya_badge.x() < hdr.switch.x(),
      "[M][切替] のかたまりはその右のまま")
check(hdr.maya_setup_btn.x() < hdr.maya_conn.x() + hdr.maya_conn.width(),
      "下段の接続コンボが起動設定ボタンの下まで伸びている")
hdr.hide()
hdr.deleteLater()

finish(not fails)
