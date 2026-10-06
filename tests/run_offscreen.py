# -*- coding: utf-8 -*-
"""オフスクリーン回帰スイートの実行: python tests/run_offscreen.py
（Linux: LD_LIBRARY_PATH に Qt の依存 .so、QT_QPA_PLATFORM=offscreen）

結果は標準出力に加えて <repo>/mfm_tests.log にも書く（起動ごとに上書き）。
Claude のサンドボックスが使えない時は run_tests.bat（mayapy で実行）を
ダブルクリック → Claude が mfm_tests.log を読んで判定する運用（r60）。"""
import datetime, glob, os, platform, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(ROOT, "mfm_tests.log")
tests = sorted(glob.glob(os.path.join(HERE, "offscreen", "test_*.py")))
env = dict(os.environ); env.setdefault("QT_QPA_PLATFORM", "offscreen")
# r124: mayapy は起動時に «ユーザーの» userSetup.py を全部実行する。第三者ツール
# （Bonjolt 等）がそこで例外を出すと、その Traceback がテストの出力に混ざって
# «合格なのに FAIL» になる（実機 2026-10-06）。テストには要らないので止める。
env.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
# r124: 子プロセスの出力は必ず UTF-8 で受け取る。Windows の mayapy は
# パイプ出力を既定で cp932 にするため、こちらが utf-8 で復号すると日本語が
# «◆◆◆» に化け、cp932 に無い文字（絵文字）では子側が UnicodeEncodeError で
# 落ちていた（実機 2026-10-06）。
env.setdefault("PYTHONIOENCODING", "utf-8")
lines = []


def say(s=""):
    # ログ（mfm_tests.log）は UTF-8 なので全部残す。コンソールが cp932 で
    # 出せない文字（絵文字など）は画面でだけ置き換える（print で落とさない）。
    try:
        print(s)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(s.encode(enc, "replace").decode(enc, "replace"))
    lines.append(s)


say("=== MayaFileManager 回帰スイート %s ===" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
say("python=%s exe=%s os=%s" % (platform.python_version(), sys.executable, platform.platform()))
try:
    import PySide6  # noqa
    say("PySide6=%s" % PySide6.__version__)
except Exception:
    try:
        import PySide2  # noqa
        say("PySide2=%s" % PySide2.__version__)
    except Exception:
        say("PySide: なし")
bad = 0
for t in tests:
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, t], capture_output=True, timeout=180, env=env,
                           cwd=os.path.dirname(t))
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        out = "TIMEOUT"
    # r124: 判定は «テスト本体が ALL OK を出すまで» の範囲で行う。
    # ALL OK の後ろに出る第三者 userSetup の Traceback 等は無関係なので数えない。
    head = out.split("ALL OK")[0] if "ALL OK" in out else out
    ok = ("ALL OK" in out and "FAILED" not in out
          and "TIMEOUT" not in out and "Traceback" not in head)
    bad += 0 if ok else 1
    say("%-40s %s (%.1fs)" % (os.path.basename(t), "PASS" if ok else "FAIL", time.time() - t0))
    if not ok:
        say("\n".join("    " + l for l in out.strip().splitlines()[-40:]))
say("=== %d/%d passed" % (len(tests) - bad, len(tests)))
try:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
except OSError:
    pass
sys.exit(1 if bad else 0)
