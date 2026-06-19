"""
ヘッドレス スモークテスト
=========================
GUIを画面なし(offscreen)で実際に構築し、主要な回帰を高速に検知する。
CI(GitHub Actions)とローカルの両方で実行可能。

実行方法:
    QT_QPA_PLATFORM=offscreen python tests/smoke_test.py
    （Windowsは run_smoke.bat を使うのが簡単）

合格で終了コード0、失敗で1（メッセージ付き）。
"""

import os
import sys
import time
import tempfile

# 画面が無い環境でもQtを動かす
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# リポジトリルートを import パスに追加（tests/ の親）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_failures = []


def check(cond, label, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _failures.append(label)


def main():
    from core.compat import QApplication, QMainWindow, QFrame
    app = QApplication.instance() or QApplication(sys.argv)

    from core.settings_manager import SettingsManager
    from core.thumbnail_generator import ThumbnailManager
    from ui.browser_panel import BrowserPanel

    sm = SettingsManager()
    tm = ThumbnailManager(cache_size=32, thumb_size=64)
    panel = BrowserPanel(sm, tm)
    win = QMainWindow()
    win.setCentralWidget(panel)
    win.resize(1400, 950)
    win.show()
    for _ in range(6):
        app.processEvents()

    # --- 1. レイアウト回帰: アドレスバー行が縦に伸びて空白を作っていないか ---
    toolbar = panel.findChildren(QFrame)[0]
    th = toolbar.geometry().height()
    vh = panel._view_stack.geometry().height()
    check(th < 60, "レイアウト: ツールバーが本来の高さに固定されている", f"toolbar_h={th}")
    check(vh > 800, "レイアウト: 列ビューが残りの高さを占有している", f"view_stack_h={vh}")

    # --- 2. symlink/ジャンクション検出ヘルパー ---
    base = tempfile.mkdtemp(prefix="mfm_smoke_")
    real = os.path.join(base, "real")
    os.makedirs(real)
    for name in ("a.ma", "b.mb", "c.txt"):
        open(os.path.join(real, name), "w").close()
    link = os.path.join(base, "link")
    symlink_ok = True
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        symlink_ok = False
        print("[SKIP] symlinkを作成できない環境のためsymlinkテストをスキップ")

    if symlink_ok:
        check(panel._is_symlink_or_junction(link), "検出: symlinkをリンクと判定")
        check(not panel._is_symlink_or_junction(real), "検出: 実体をリンクでないと判定")

        # --- 3. symlinkの中身がブラウズできる（リンクのパスを維持して子が出る）---
        panel._navigate_now(link)
        for _ in range(12):
            app.processEvents()
        kept = (os.path.normpath(panel._current_path) == os.path.normpath(link))
        check(kept, "symlink: リンクのパスを維持している(実体ドライブへ飛ばない)",
              f"current={panel._current_path}")

        root = panel._column_view.rootIndex()
        proxy = panel._proxy
        deadline = time.time() + 3
        rc = 0
        while time.time() < deadline:
            app.processEvents()
            rc = proxy.rowCount(root)
            if rc > 0:
                break
            time.sleep(0.03)
        check(rc == 3, "symlink: 中身(3件)が列に表示される", f"rowCount={rc}")

    print("-" * 48)
    if _failures:
        print(f"SMOKE TEST FAILED: {len(_failures)} 件 -> {_failures}")
        sys.stdout.flush()
        os._exit(1)
    print("SMOKE TEST PASSED")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
