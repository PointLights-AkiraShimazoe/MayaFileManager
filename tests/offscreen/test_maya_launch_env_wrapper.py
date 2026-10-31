# -*- coding: utf-8 -*-
"""r146: Maya 起動プロファイルの «環境変数» と «ラッパー»。

ユーザーからの例（2026-10-13）— bat で組んでいた起動手順::

    SET MAYA_EXE="C:\\Program Files\\Autodesk\\Maya2025\\bin\\maya.exe"
    SET PATH_MAYA_BAT=%~DP0run.bat
    SET OPEN_FILE="%1"
    if %OPEN_FILE% == "" ( START %PATH_MAYA_BAT% %MAYA_EXE% en_EN )
    else ( START %PATH_MAYA_BAT% %MAYA_EXE% en_EN %OPEN_FILE% )

«追加引数» は maya.exe の引数しか渡せないので、これは再現できなかった。
en_EN（＝ UI 言語）は Maya が **起動時に環境変数として** 読むもので、
引数でも -command でも間に合わない。そこで 2 つ足した。

  環境変数 … KEY=VALUE を ; か改行で。bat の «SET KEY=VALUE» も通る。
  ラッパー … maya.exe の代わりに走らせる .bat / .exe。
             {exe} {file} {port} を書いた位置へ差し込む。
             書かなければ «ラッパー maya.exe» の順で繋ぐ（上の bat と同じ形）。

ここで固定すること:
  1) 環境変数の書式を取りこぼさない（引用符・空白・SET・注記・; 区切り）
  2) ラッパーの組み立てが bat と同じ並びになる
  3) 開くファイルが «二重に» 付かない（{file} を書いた時）
  4) .bat は cmd 経由で呼ぶ
  5) プロファイルに env / wrapper が保存される
  6) ユーザー報告 2026-10-31「ダブルクリックでは、Maya が開くバッチファイルを
     指定しても Maya は起動しませんでした」:
     * 空白を含む場所の bat ＋ 空白を含む maya.exe を cmd /c にリストで渡すと、
       cmd が «最初と最後の引用符» を消して «'D:\\my' は認識されていません»
       で止まる → /s と外側の引用符で組む
     * «D:/tools/run.bat» のスラッシュ書きは cmd がスイッチと読む → 円記号に
     * bat のフォルダで動かす（ダブルクリックと同じ）
     * 失敗の理由（bat の出力）を logs/mfm_launch.log に残す。早く失敗で
       終わったら知らせる。bat が無ければ起動前に言う
     Windows では本物の bat を起動して、引数とフォルダが届くことを確かめる
"""
import os

from _common import *  # noqa: F401,F403
from _common import finish, sm

from core.maya_version import parse_env_text, _expand_wrapper

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# ── 1) 環境変数の書式 ─────────────────────────────────────────────
got = parse_env_text('SET MAYA_UI_LANGUAGE=en_US\n'
                     'MAYA_MODULE_PATH = "D:/mods" ; FOO=bar  # memo\n'
                     '\n'
                     'BAD_LINE_WITHOUT_EQUALS')
check(got.get("MAYA_UI_LANGUAGE") == "en_US", "bat の SET 行をそのまま読める")
check(got.get("MAYA_MODULE_PATH") == "D:/mods", "引用符と前後の空白を落とす")
check(got.get("FOO") == "bar", "; 区切りと # 注記を扱える")
check(len(got) == 3, "= の無い行は捨てる（%r）" % (got,))
check(parse_env_text("") == {}, "空なら空")

# ── 2)(3)(4) ラッパーの組み立て ───────────────────────────────────
EXE = r"C:\Program Files\Autodesk\Maya2025\bin\maya.exe"
FILE = r"D:\work\scene.ma"

a = _expand_wrapper(r"D:\tools\run.bat", EXE, FILE, 20251)
check(a[:2] == [os.environ.get("COMSPEC") or "cmd.exe", "/c"],
      ".bat は cmd 経由で呼ぶ（%r）" % (a[:2],))
check(a[2:] == [r"D:\tools\run.bat", EXE],
      "プレースホルダ無しは «ラッパー maya.exe» の順（%r）" % (a[2:],))

b = _expand_wrapper(r"D:\tools\run.bat {exe} en_EN {file}", EXE, FILE, None)
check(b[2:] == [r"D:\tools\run.bat", EXE, "en_EN", FILE],
      "書いた位置へ差し込む（%r）" % (b[2:],))

c = _expand_wrapper(r"D:\tools\run.bat {exe} en_EN {file}", EXE, "", None)
check(c[2:] == [r"D:\tools\run.bat", EXE, "en_EN"],
      "開くファイルが無い時は {file} の分が消える（%r）" % (c[2:],))

d = _expand_wrapper(r"D:\tools\run.exe {exe} --port {port}", EXE, "", 20251)
check(d == [r"D:\tools\run.exe", EXE, "--port", "20251"],
      ".exe はそのまま／{port} も差し替わる（%r）" % (d,))

check(_expand_wrapper("", EXE, FILE, None) == [], "空なら何もしない")

e = _expand_wrapper(r'"D:\my tools\run.bat" {exe} en_EN {file}', EXE, FILE, None)
check(e[2] == r"D:\my tools\run.bat",
      "空白入りのパスを引用符で書ける（引用符は外れる）（%r）" % (e[2],))

# ── 5) プロファイルの保存 ────────────────────────────────────────
sm.save_maya_launch_profiles([{
    "id": "t1", "version": "2025", "label": "Maya 2025 (en)",
    "args": "-hideConsole", "env": "MAYA_UI_LANGUAGE=en_US",
    "wrapper": r"D:\tools\run.bat {exe} en_EN {file}",
}])
back = sm.get_maya_launch_profiles()
check(len(back) == 1, "1 行保存された")
check(back[0].get("env") == "MAYA_UI_LANGUAGE=en_US", "環境変数が残る")
check(back[0].get("wrapper") == r"D:\tools\run.bat {exe} en_EN {file}",
      "ラッパーが残る")
check(back[0].get("args") == "-hideConsole", "従来の追加引数も残る")

# 古い設定（env / wrapper の無い行）を読んでも壊れない
sm._global_state["maya_launch_profiles"] = [
    {"id": "old", "version": "2024", "label": "", "args": ""}]
old = sm.get_maya_launch_profiles()
check(old and old[0].get("env", "") == "",
      "古い設定を読んでも落ちない（env は空扱い）")

# ── ダイアログに欄が出ているか ───────────────────────────────────
from ui.maya_launch_dialog import MayaLaunchDialog
dlg = MayaLaunchDialog(sm, ["2027", "2026", "2025"])
row = dlg.add_row({"version": "2025", "env": "A=1", "wrapper": "w.bat"})
data = row.get_data()
check(data.get("env") == "A=1" and data.get("wrapper") == "w.bat",
      "行が env / wrapper を往復できる（%r）" % (data,))
check(hasattr(dlg, "_common_env"), "共通の環境変数の欄がある")
dlg.deleteLater()

# ── r146（ユーザー指示 2026-10-14）: ラッパーはファイル選択できること ──
def _wrapper_browse_check():
    """「この場合はファイル指定のはずなので、ファイル指定をするような
    アイコンをください。」"""
    from ui.maya_launch_dialog import _ProfileRow
    import ui.dialog_util as _du
    from core.compat import QToolButton

    row = _ProfileRow(["2027"],
                      {"version": "2027", "label": "x",
                       "wrapper": "D:/tools/run.bat {exe} en_EN {file}"})
    app.processEvents()
    assert isinstance(getattr(row, "wrapper_browse", None), QToolButton), \
        "ラッパー欄にファイル選択のボタンが無い"
    assert row.wrapper_browse.toolTip(), "ボタンの説明が無い"

    # 選んだら «場所だけ» 差し替わり、プレースホルダ付きの引数は残る
    orig = _du.pick_open_file
    try:
        _du.pick_open_file = lambda *a, **k: ("D:\\other\\go.bat", "")
        row._browse_wrapper()
    finally:
        _du.pick_open_file = orig
    got = row.wrapper.text()
    assert got.startswith("D:\\other\\go.bat"), got
    assert "{exe}" in got and "{file}" in got and "en_EN" in got, \
        ("引数が消えている", got)
    print("wrapper: ファイル選択ボタンが場所だけ差し替える: OK (%s)" % got)

    # 空欄から選んだ時はそのパスだけ
    row.wrapper.setText("")
    try:
        _du.pick_open_file = lambda *a, **k: ("D:\\x y\\run.bat", "")
        row._browse_wrapper()
    finally:
        _du.pick_open_file = orig
    assert row.wrapper.text() == '"D:\\x y\\run.bat"', row.wrapper.text()
    print("wrapper: 空白を含む場所は引用符で囲む: OK")
    row.close()


_wrapper_browse_check()


# ── 6) ラッパーの bat が本当に起動する（ユーザー報告 2026-10-31）──────
import sys
import time
import tempfile
import shutil
from core import maya_version as _mv

check(hasattr(_mv, "_windows_cmdline"), "bat 用のコマンドラインを自前で組む関数がある")
if hasattr(_mv, "_windows_cmdline"):
    f = _expand_wrapper('"D:/my tools/run.bat" {exe} en_EN {file}', EXE,
                        r"D:\work\scene (v2).ma", None)
    check(f[2] == r"D:\my tools\run.bat", "スラッシュ書きの場所は円記号にそろう（%r）" % (f[2],))
    line = _mv._windows_cmdline(f)
    check(" /s /c \"" in line and line.endswith('"'),
          "cmd には /s と外側の引用符で渡す（%s）" % line)
    inner = line.split(" /s /c ", 1)[1]
    check(inner.startswith('""D:\\my tools\\run.bat" ') and inner.endswith('.ma""'),
          "外側の引用符の中に bat と引数がそのまま並ぶ（%s）" % inner)
    check('"D:\\work\\scene (v2).ma"' in inner,
          "( ) を含む引数は囲む（cmd が区切らない）")
    g = _expand_wrapper("D:/tools/run.bat", EXE, "", None)
    check(g[2] == r"D:\tools\run.bat", "引用符無しのスラッシュ書きも円記号に（%r）" % (g[2],))


class _FakeInst:
    is_available = True
    version = "2099"

    def __init__(self, exe):
        from pathlib import Path
        self.executable = Path(exe)
        self.path = Path(exe).parent


# bat が無ければ起動前に分かる
try:
    _mv.launch_maya(_FakeInst(sys.executable), wrapper=r"Z:\no\such\dir\run.bat {exe}",
                    clean_env=False)
    check(False, "存在しない bat は起動前にエラー")
except FileNotFoundError as _e:
    check("run.bat" in str(_e), "存在しない bat は起動前にエラー（%s）" % _e)
except Exception as _e:
    check(False, "存在しない bat は起動前にエラー（別の例外 %r）" % (_e,))

if os.name == "nt":
    root = tempfile.mkdtemp(prefix="mfm wrap ")
    bat_dir = os.path.join(root, "my tools")
    os.makedirs(bat_dir)
    bat = os.path.join(bat_dir, "run.bat")
    with open(bat, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write('@echo off\n'
                 '> "%~dp0out.txt" echo A1=%~1\n'
                 '>> "%~dp0out.txt" echo A2=%~2\n'
                 '>> "%~dp0out.txt" echo A3=%~3\n'
                 '>> "%~dp0out.txt" echo CWD=%CD%\n'
                 '>> "%~dp0out.txt" echo ALL=%*\n'
                 '>> "%~dp0out.txt" echo END\n')
    out = os.path.join(bat_dir, "out.txt")
    fake_exe = sys.executable          # 実在する exe（bat は起動しない）

    def _run(wrapper, file_path="", port=None, timeout=15.0):
        if os.path.exists(out):
            os.remove(out)
        proc = _mv.launch_maya(_FakeInst(fake_exe), file_path=file_path or None,
                               command_port=port, clean_env=False, wrapper=wrapper)
        t_end = time.time() + timeout
        txt = ""
        while time.time() < t_end:
            try:
                with open(out, encoding="mbcs", errors="replace") as fh:
                    txt = fh.read()
            except OSError:
                txt = ""
            if "END" in txt:
                break
            time.sleep(0.2)
        try:
            proc.wait(5)
        except Exception:
            pass
        vals = {}
        for ln in txt.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                vals[k.strip()] = v.strip()
        return vals, proc

    fwd = bat.replace("\\", "/")
    scene = os.path.join(root, "scene (v2).ma")
    v, proc = _run('"%s" {exe} en_EN {file}' % fwd, file_path=scene)
    check(v.get("A1", "").lower() == fake_exe.lower(),
          "空白入り・スラッシュ書きの bat が起動し、1 番目に maya.exe が届く（%r）" % (v,))
    check(v.get("A2") == "en_EN" and v.get("A3", "").lower() == scene.lower(),
          "2・3 番目に言語と開くファイルが届く（%r）" % (v,))
    check(os.path.normcase(v.get("CWD", "")) == os.path.normcase(bat_dir),
          "bat のフォルダで動く（ダブルクリックと同じ）（%r）" % (v.get("CWD"),))

    # プレースホルダ無し＋連携ポート（ダブルクリックで «起動しますか» → Yes の道）
    v2, proc2 = _run('"%s"' % bat, port=20269)
    check(v2.get("A1", "").lower() == fake_exe.lower() and v2.get("A2") == "-command",
          "プレースホルダ無しは «bat maya.exe -command …» で届く（%r）" % (v2,))
    check("commandPort" in v2.get("ALL", "") and "20269" in v2.get("ALL", ""),
          "連携ポートの命令が bat まで届く（%r）" % (v2.get("ALL"),))

    # 失敗した時の理由が記録に残る
    bad = os.path.join(bat_dir, "fail.bat")
    with open(bad, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write("@echo off\necho MFM-WRAPPER-FAILED-ON-PURPOSE\nexit /b 3\n")
    p3 = _mv.launch_maya(_FakeInst(fake_exe), clean_env=False, wrapper='"%s"' % bad)
    try:
        rc = p3.wait(15)
    except Exception:
        rc = None
    time.sleep(0.3)
    check(rc == 3, "bat の終了コードが取れる（%r）" % (rc,))
    check("MFM-WRAPPER-FAILED-ON-PURPOSE" in _mv.launch_log_tail(40),
          "bat の出力が logs/mfm_launch.log に残る")
    shutil.rmtree(root, ignore_errors=True)
else:
    print("SKIP 本物の bat の起動（Windows のみ）", flush=True)

# 早く失敗で終わったら知らせる（UI）
from ui import main_window_dcc as _mwd
from core.compat import QMessageBox as _QMB


class _P:
    mfm_wrapper = r"D:\tools\run.bat"
    mfm_cmdline = "cmd /s /c ..."

    def __init__(self, rc):
        self.rc = rc

    def poll(self):
        return self.rc


class _Host:
    LAUNCH_EARLY_EXIT_MS = 10


shown = []
_orig_warn = _QMB.warning
_QMB.warning = staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
try:
    if hasattr(_mwd.MainWindowDccMixin, "_watch_launch"):
        _mwd.MainWindowDccMixin._watch_launch(_Host(), _P(1), "Maya 2099")
        _mwd.MainWindowDccMixin._watch_launch(_Host(), _P(0), "Maya 2099")
        _mwd.MainWindowDccMixin._watch_launch(_Host(), _P(None), "Maya 2099")
    t_end = time.time() + 2
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.02)
finally:
    _QMB.warning = _orig_warn
check(len(shown) == 1 and "run.bat" in shown[0],
      "起動直後に失敗で終わった時だけ知らせる（ラッパーの場所を添えて）（%d 件）" % len(shown))


finish(not fails)