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
# r146: 絞り込み（python tests/run_offscreen.py 語 語 ...）。
#   全部で 7 分かかるので、落ちた数本だけ回したい時に使う。語はファイル名の
#   部分一致。結果の書き出し先は絞り込み時だけ mfm_tests_sel.log に分ける
#   （全体の結果 mfm_tests.log を部分実行で上書きしないため）。
_ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
_ENV_ARGS = {}
# 「KEY=VALUE」の語は «子プロセスの環境変数» として渡す（r146）。
# 切り分け用の殺しスイッチを、watcher 経由でも入れられるようにするため。
for _a in list(_ARGS):
    if "=" in _a and not os.path.exists(_a):
        k, v = _a.split("=", 1)
        if k and k.replace("_", "").isalnum():
            _ENV_ARGS[k] = v
            _ARGS.remove(_a)
# 「x5」のような語は «選んだものを 5 回ずつ回す» の指定（断続クラッシュの
# 再現率を上げるため。r146）
REPEAT = 1
for _a in list(_ARGS):
    if len(_a) > 1 and _a[0] in "xX" and _a[1:].isdigit():
        REPEAT = max(1, int(_a[1:]))
        _ARGS.remove(_a)
PICK = _ARGS
if PICK:
    tests = [t for t in tests
             if any(k.lower() in os.path.basename(t).lower() for k in PICK)]
    LOG = os.path.join(ROOT, "mfm_tests_sel.log")
    if REPEAT > 1:
        tests = [t for t in tests for _ in range(REPEAT)]
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
# r146: C++ 側で落ちた時（Windows の ACCESS_VIOLATION 3221225477）に、
# せめて Python のスタックを出させる。子の «起動時» から有効にしたいので
# 環境変数で入れる（テストの中で faulthandler.enable() を呼ぶのでは、
# Maya が先に入れた例外ハンドラに負けて何も出ないことがある）。
env.setdefault("PYTHONFAULTHANDLER", "1")
env.update(_ENV_ARGS)
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


say("=== MayaFileManager 回帰スイート %s ===%s" % (
    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ("  [絞り込み: %s / %d 本 / x%d]"
     % (" ".join(PICK), len(tests), REPEAT)) if PICK else ""))
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
    rc = None
    try:
        # r128: -u （非バッファ）を忘れないこと。パイプ出力は既定で
        # ブロックバッファなので、子がクラッシュ（segfault/abort）すると
        # それまでの print が丸ごと消え、「FAIL なのに何も出ていない」になる。
        p = subprocess.run([sys.executable, "-u", t], capture_output=True, timeout=180,
                           env=env, cwd=os.path.dirname(t))
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
        rc = p.returncode
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
        # r128: «出力が空っぽの FAIL» の正体を見分けられるよう、
        # 終了コードを必ず残す（負値＝シグナル、大きな正値＝クラッシュ）。
        if rc is not None:
            say("    exit code: %d%s" % (rc, "" if "ALL OK" in out else "  (ALL OK 未出力)"))
        # r145: «末尾 40 行» だけだと、Qt のオフスクリーン警告が大量に出た時に
        # 肝心の FAIL 行が押し出されて «出力が空の FAIL» に見える
        # （実機 2026-10-10: test_always_on_top_and_compact がまさにこれ）。
        # 無関係な警告を落としてから、多めに残す。
        _NOISE = (
            "does not support raise()",
            "does not support propagateSizeHints()",
            "does not support grabbing the keyboard",
            "QFontDatabase: Cannot find font directory",
            "Note that Qt no longer ships fonts",
            "QFont::setPointSizeF: Point size",
            # r146: これが数十行続くと肝心の FAIL 行が押し出される
            "index from wrong model passed to mapToSource",
        )
        shown = [l for l in out.strip().splitlines()
                 if not any(n in l for n in _NOISE)]
        say("\n".join("    " + l for l in shown[-120:]) or "    (出力なし)")
say("=== %d/%d passed" % (len(tests) - bad, len(tests)))
try:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
except OSError:
    pass
sys.exit(1 if bad else 0)
