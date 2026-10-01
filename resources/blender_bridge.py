# -*- coding: utf-8 -*-
"""
MayaFileManager ⇔ Blender 連携ブリッジ（Blender 内で動く側、r65）
================================================================

Maya の commandPort(python) 相当を Blender に用意する。
- localhost の TCP ポートで «1行の Python コード» を受け取り、Blender の
  メインスレッド（bpy.app.timers）で eval/exec し、結果を文字列で返す。
- 起動方法:
    * マネージャーの「起動」から:  blender --python blender_bridge.py -- --mfm-port 20271
    * 自動起動（ツールメニューでインストール）: このファイルを
      <Blender設定>/<X.Y>/scripts/startup/mfm_bridge.py に置くと register() が
      呼ばれ、レンジ 20271〜20279 の空きポートで待ち受ける。
- 識別: マネージャーは 'MFMID<version><filepath><pid>MFMID' を返す式を送る。
"""

import os
import queue
import socket
import sys
import threading
import traceback

try:
    import bpy
except ImportError:  # Blender 外で import された場合（テスト等）
    bpy = None

PORT_RANGE = range(20271, 20280)
_queue = queue.Queue()
_server_sock = None
_port = None


# ---------------------------------------------------------------------------
# ヘルパ（マネージャーから送られるコードが使う）
# ---------------------------------------------------------------------------

def mfm_log(message: str, level: str = "info"):
    """マネージャーからの操作ログを Blender 側に残す（r88）。

    - Info エディタ＋ステータスバー: 下の MFM_OT_report（モーダル）経由
    - システムコンソール: print（常に）

    Python から呼んだオペレータの report は «呼び出し側の持ち物» 扱いで
    Info エディタへ届かない（bpy_operator_function.cc: "Own so these don't move
    into global reports"）。モーダルで走らせると WM が報告を引き取る
    （wm_event_system.cc: "Take ownership of reports"）ので、それを利用する。"""
    msg = str(message)
    print(msg)
    lv = {"error": "WARNING", "warning": "WARNING"}.get(level, "INFO")
    # 失敗も WARNING で出す: ERROR だと WM が報告ポップアップも出し、
    # mfm_popup のエラー表示と二重になるため（色は黄になる）。
    try:
        if bpy is None or bpy.app.background:
            return
        mfm_run(lambda: bpy.ops.mfm.report("INVOKE_DEFAULT", message=msg, level=lv))
    except Exception:
        pass


def mfm_popup(message: str, title: str = "Maya File Manager", icon: str = "ERROR"):
    """メッセージをポップアップで表示（エラー通知用）。"""
    lines = [l for l in str(message).splitlines() if l.strip()][:8]

    def _draw(self, _context):
        for l in lines:
            self.layout.label(text=l)
    try:
        bpy.context.window_manager.popup_menu(_draw, title=title, icon=icon)
    except Exception:
        print("[MFM]", title, message)


def _ctx_override_kwargs():
    """timers からの呼び出しにはウィンドウ/エリアが無いので、最初のウィンドウの
    3D ビューを文脈として与える（インポート系オペレータに必要）。"""
    try:
        wm = bpy.context.window_manager
        win = wm.windows[0] if wm and len(wm.windows) else None
        if win is None:
            return {}
        kw = {"window": win, "screen": win.screen}
        area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None)
        if area is not None:
            kw["area"] = area
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                kw["region"] = region
        return kw
    except Exception:
        return {}


def mfm_run(fn):
    """fn() を適切なコンテキストで実行する。"""
    kw = _ctx_override_kwargs()
    if kw and hasattr(bpy.context, "temp_override"):
        with bpy.context.temp_override(**kw):
            return fn()
    return fn()


def mfm_open(filepath: str):
    """.blend を開く。未保存の変更があれば «Blender 側で» 確認する。"""
    def _do_open():
        bpy.ops.wm.open_mainfile(filepath=filepath, load_ui=True)

    if bpy.data.is_dirty:
        def _draw(self, _context):
            self.layout.label(text="現在のファイルに未保存の変更があります。")
            self.layout.label(text=os.path.basename(filepath))
            op = self.layout.operator("mfm.open_discard", text="破棄して開く", icon="FILE_FOLDER")
            op.filepath = filepath
        try:
            bpy.context.window_manager.popup_menu(_draw, title="Maya File Manager", icon="QUESTION")
        except Exception:
            mfm_run(_do_open)
        return "confirm"
    mfm_run(_do_open)
    return "opened"


def mfm_import(filepath: str):
    """形式別インポート。.blend は Append（全オブジェクトをシーンへ複製）。"""
    ext = os.path.splitext(filepath)[1].lower()

    def _do():
        if ext == ".blend":
            return _append_or_link(filepath, link=False)
        if ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=filepath)
        elif ext == ".obj":
            if hasattr(bpy.ops.wm, "obj_import"):
                bpy.ops.wm.obj_import(filepath=filepath)
            else:
                bpy.ops.import_scene.obj(filepath=filepath)
        elif ext == ".abc":
            bpy.ops.wm.alembic_import(filepath=filepath)
        elif ext in (".usd", ".usda", ".usdc", ".usdz"):
            bpy.ops.wm.usd_import(filepath=filepath)
        elif ext in (".gltf", ".glb"):
            bpy.ops.import_scene.gltf(filepath=filepath)
        elif ext == ".stl":
            if hasattr(bpy.ops.wm, "stl_import"):
                bpy.ops.wm.stl_import(filepath=filepath)
            else:
                bpy.ops.import_mesh.stl(filepath=filepath)
        elif ext == ".ply":
            if hasattr(bpy.ops.wm, "ply_import"):
                bpy.ops.wm.ply_import(filepath=filepath)
            else:
                bpy.ops.import_mesh.ply(filepath=filepath)
        else:
            raise RuntimeError("未対応の形式です: %s" % ext)
        return "imported"
    try:
        return mfm_run(_do)
    except Exception as e:
        mfm_popup("インポートに失敗しました:\n%s\n%s" % (os.path.basename(filepath), e))
        return "error: %s" % e


def mfm_link(filepath: str):
    """.blend をライブラリリンク（Maya のリファレンス相当）。他形式はインポートへ退避。"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext != ".blend":
        return mfm_import(filepath)
    try:
        return mfm_run(lambda: _append_or_link(filepath, link=True))
    except Exception as e:
        mfm_popup("リンクに失敗しました:\n%s\n%s" % (os.path.basename(filepath), e))
        return "error: %s" % e


def _append_or_link(filepath: str, link: bool):
    """.blend 内の全オブジェクトを現在のシーンのコレクションへ追加する。
    link=True ならライブラリリンク（編集不可・元ファイルに追従）。"""
    with bpy.data.libraries.load(filepath, link=link) as (data_from, data_to):
        data_to.objects = list(data_from.objects)
    coll = bpy.context.scene.collection
    n = 0
    for obj in data_to.objects:
        if obj is None:
            continue
        try:
            coll.objects.link(obj)
            n += 1
        except RuntimeError:
            pass   # 既にリンク済み
    return "%s %d objects" % ("linked" if link else "appended", n)


# ---------------------------------------------------------------------------
# オペレータ（未保存確認のポップアップから呼ぶ）
# ---------------------------------------------------------------------------

if bpy is not None:
    class MFM_OT_open_discard(bpy.types.Operator):
        bl_idname = "mfm.open_discard"
        bl_label = "Discard changes and open"
        filepath: bpy.props.StringProperty()

        def execute(self, context):
            mfm_log("[MayaFileManager] 破棄して開きます: %s" % self.filepath)
            bpy.ops.wm.open_mainfile(filepath=self.filepath, load_ui=True)
            mfm_log("[MayaFileManager] 開きました: %s" % os.path.basename(self.filepath))
            return {"FINISHED"}

    class MFM_OT_report(bpy.types.Operator):
        """マネージャーのログを Info エディタへ出すためだけのオペレータ（r88）。
        invoke でタイマーを付けてモーダルに入り、最初の TIMER で report して終わる。"""
        bl_idname = "mfm.report"
        bl_label = "Maya File Manager Log"
        bl_options = {"INTERNAL"}
        message: bpy.props.StringProperty()
        level: bpy.props.StringProperty(default="INFO")

        def invoke(self, context, _event):
            wm = context.window_manager
            if context.window is None:
                return {"CANCELLED"}
            self._timer = wm.event_timer_add(0.0, window=context.window)
            wm.modal_handler_add(self)
            return {"RUNNING_MODAL"}

        def modal(self, context, event):
            if event.type != "TIMER":
                return {"PASS_THROUGH"}
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            lv = self.level if self.level in ("INFO", "WARNING", "ERROR") else "INFO"
            self.report({lv}, self.message)
            return {"FINISHED"}

        def execute(self, context):
            # INVOKE 以外で呼ばれた場合（UI 無し等）はコンソールだけ
            print(self.message)
            return {"FINISHED"}


# ---------------------------------------------------------------------------
# TCP サーバ（受信はスレッド、実行はメインスレッドの timers）
# ---------------------------------------------------------------------------

def _handle(conn):
    try:
        conn.settimeout(5.0)
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        code = data.decode("utf-8", "replace").strip()
        if not code:
            return
        ev = threading.Event()
        box = {}
        _queue.put((code, ev, box))
        ev.wait(120.0)
        reply = box.get("reply", "")
        try:
            conn.sendall((reply + "\n").encode("utf-8"))
        except OSError:
            pass
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _serve(sock):
    while True:
        try:
            conn, _addr = sock.accept()
        except OSError:
            return
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


def _pump():
    """メインスレッド: キューのコードを実行して結果を返す（0.1秒毎）。"""
    while True:
        try:
            code, ev, box = _queue.get_nowait()
        except queue.Empty:
            break
        try:
            ns = {"bpy": bpy, "os": os, "mfm_open": mfm_open, "mfm_import": mfm_import,
                  "mfm_link": mfm_link, "mfm_popup": mfm_popup, "mfm_run": mfm_run,
                  "mfm_log": mfm_log, "__name__": "__mfm__"}
            try:
                result = eval(code, ns)
            except SyntaxError:
                exec(code, ns)
                result = None
            box["reply"] = "" if result is None else str(result)
        except Exception:
            box["reply"] = "Error: " + traceback.format_exc(limit=3)
        finally:
            ev.set()
    return 0.1


def _pick_port():
    argv = sys.argv
    if "--mfm-port" in argv:
        try:
            return int(argv[argv.index("--mfm-port") + 1])
        except (ValueError, IndexError):
            pass
    return None


def _bind(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    s.bind(("127.0.0.1", port))
    s.listen(8)
    return s


def register():
    global _server_sock, _port
    if bpy is None or _server_sock is not None:
        return
    for _cls in (MFM_OT_open_discard, MFM_OT_report):
        try:
            bpy.utils.register_class(_cls)
        except Exception:
            pass
    want = _pick_port()
    ports = [want] if want else list(PORT_RANGE)
    for p in ports:
        try:
            _server_sock = _bind(p)
            _port = p
            break
        except OSError:
            continue
    if _server_sock is None:
        print("[MFM] bridge: no free port in", list(ports))
        return
    threading.Thread(target=_serve, args=(_server_sock,), daemon=True,
                     name="mfm-blender-bridge").start()
    bpy.app.timers.register(_pump, first_interval=0.5, persistent=True)
    print("[MFM] bridge listening on 127.0.0.1:%d" % _port)


def unregister():
    global _server_sock
    try:
        if _server_sock is not None:
            _server_sock.close()
    except OSError:
        pass
    _server_sock = None
    for _cls in (MFM_OT_report, MFM_OT_open_discard):
        try:
            bpy.utils.unregister_class(_cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
