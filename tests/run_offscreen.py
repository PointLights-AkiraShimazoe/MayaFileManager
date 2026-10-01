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
lines = []


def say(s=""):
    print(s)
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
    ok = "ALL OK" in out and "FAILED" not in out and "Traceback" not in out
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
