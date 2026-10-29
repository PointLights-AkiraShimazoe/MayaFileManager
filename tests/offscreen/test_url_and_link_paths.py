# -*- coding: utf-8 -*-
"""r146（ユーザー指示 2026-10-26）

1. アドレス欄に «リンクで終わるパス»（例: <クラウドのドライブ>/<フォルダ>/<名前>.lnk）を
   入れて Enter → 移動する。以前はファイル扱いで «関連付けで開く» に流れ、
   Manager は動かなかった（リンクの直下まで入れると移動できた）。
     * ショートカット（ファイル）→ 参照先へ
     * フォルダとして見えるリンク（Google Drive のショートカット）→ そこへ
2. URL をアドレス欄に貼って実行 → 既定のブラウザで開く。表示は動かさない。
3. Preset に URL を登録 → ボタンでブラウザで開く。
4. file:// の URL はローカルパスとして移動する。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, tmpdir, finish, QTest

from core.compat import QtGui, QToolButton
import ui.browser_panel as _bp

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def settle(ms=150):
    QTest.qWait(ms)
    app.processEvents()


def wait_path(b, want, timeout_ms=6000):
    waited = 0
    while waited < timeout_ms:
        if b._norm(b.current_path() or "") == b._norm(want):
            return True
        settle(100)
        waited += 100
    return False


opened_urls = []
QtGui.QDesktopServices.openUrl = staticmethod(lambda u: (opened_urls.append(u.toString()), True)[1])
opened_files = []

root = tmpdir()
start = os.path.join(root, "start")
target = os.path.join(root, "realTarget")
drive_link = os.path.join(root, "folderLink.lnk")        # フォルダとして見えるリンク
shortcut = os.path.join(root, "fileLink.lnk")           # ショートカットのファイル
for d in (start, os.path.join(target, "ch0001"), os.path.join(drive_link, "ch0001")):
    os.makedirs(d)
open(shortcut, "wb").close()

b = make_panel(1200, 600)
b._open_path_externally = lambda p: opened_files.append(p)
b.navigate_to(start)
check(wait_path(b, start), "前提: start にいる")
settle(1000)


def enter(text):
    """アドレス欄（左のディレクトリ欄）に貼って Enter。ユーザーの操作と同じ:
    欄の中身を丸ごと置き換え、returnPressed を出す。"""
    b._addr_bar.dir_edit.setText(text)
    b._addr_bar.dir_edit.returnPressed.emit()


# 1a) ショートカット（ファイル）で終わるパス → 参照先へ
_orig_resolve = _bp.resolve_windows_shortcut
_bp.resolve_windows_shortcut = lambda p: target if p.lower().endswith("filelink.lnk") else None
enter(shortcut.replace("\\", "/"))
check(wait_path(b, target), "ショートカットで終わるパス → 参照先へ移動: %r" % b.current_path())
check(not opened_files, "関連付けで開くへ流れない: %r" % opened_files)
_bp.resolve_windows_shortcut = _orig_resolve

# 1b) フォルダとして見えるリンク（Google Drive）で終わるパス → そこへ
enter(drive_link.replace("\\", "/"))
check(wait_path(b, drive_link), "フォルダとして見えるリンク → 移動: %r" % b.current_path())

# 1c) リンクの直下（従来から動いていた経路）も変わらない
enter(os.path.join(drive_link, "ch0001").replace("\\", "/"))
check(wait_path(b, os.path.join(drive_link, "ch0001")), "リンクの直下も移動できる")

# 2) URL をアドレス欄に貼って実行 → ブラウザで開く、表示は動かない
here = b.current_path()
for url in ("https://example.com/path?q=1", "www.example.org"):
    del opened_urls[:]
    enter(url)
    settle(300)
    check(opened_urls and opened_urls[-1].rstrip("/").endswith(url.split("www.")[-1].rstrip("/")),
          "URL をブラウザで開く: %r → %r" % (url, opened_urls))
    check(b._norm(b.current_path()) == b._norm(here), "URL では移動しない")
    check(b._norm(b._addr_bar.text()) == b._norm(here), "アドレス欄は元のパスに戻る: %r" % b._addr_bar.text())

# 3) Preset に URL を登録 → ボタンでブラウザで開く
from ui.nav_panels import QuickNavBar
presets = sm.get_quick_nav_presets() or {}
presets["mfm_test_url"] = [{"label": "Docs", "path": "https://example.com/docs"},
                           {"label": "Start", "path": start}]
sm.save_quick_nav_presets(presets)
sm.set("quick_nav_preset", "mfm_test_url", save=False)
bar = QuickNavBar(sm)
bar.navigate_requested.connect(b.navigate_to)
if hasattr(bar, "refresh"):
    bar.refresh()
settle(100)
btns = {x.text(): x for x in bar.findChildren(QToolButton)}
check("Docs" in btns, "前提: Preset のボタンがある: %r" % sorted(btns))
del opened_urls[:]
if "Docs" in btns:
    btns["Docs"].click()
    settle(300)
check(opened_urls == ["https://example.com/docs"], "Preset の URL ボタンでブラウザで開く: %r" % opened_urls)
check(b._norm(b.current_path()) == b._norm(here), "Preset の URL でも移動しない")
if "Start" in btns:
    btns["Start"].click()
    check(wait_path(b, start), "Preset のフォルダは今までどおり移動")

# 4) file:// はローカルパスとして移動
from core.compat import QUrl
enter(QUrl.fromLocalFile(target).toString())
check(wait_path(b, target), "file:// の URL はそのフォルダへ移動: %r" % b.current_path())

finish(not fails)
