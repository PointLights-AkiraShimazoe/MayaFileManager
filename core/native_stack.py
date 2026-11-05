# -*- coding: utf-8 -*-
"""止まっているスレッドの «C++ 側の» 呼び出し履歴を取る（Windows 専用、r146）。

ユーザー報告 2026-11-02「P4V で GetLatest をしていると Manager がフリーズ
します」: mfm_freeze.log では、33 秒間メインスレッドが app.exec()（Qt の
C++）の中にいて、Python 側には何も残っていなかった。Qt の中のどこで
止まっているかが分からないと直せない。

やり方:
  * フリーズ見張りが «長い停止» を見つけたら、**別のプロセス**（この exe /
    この Python 自身を引数付きで起動）に止まっているスレッドを調べさせる。
    同じプロセスの中で自分のメインスレッドを止めて調べると、止めた先が
    ヒープのロックを握っていた時に自分も止まる（＝アプリが永久に固まる）。
    別プロセスならその心配が無い。
  * 調べる側: スレッドを一瞬止め（SuspendThread）、レジスタを読み、
    dbghelp の StackWalk64 で呼び出し履歴をたどって、各アドレスを
    «モジュール名!関数名+ずれ» にする（Qt の DLL は公開関数の名前を
    持っているので、Qt の中のどこかはこれで分かる）。すぐ再開する。
  * 結果は mfm_freeze.log に追記する。

製品の動作には影響しない（記録だけ）。MFM_DIAG=no_native_stack で止められる。
"""
import os
import sys

HELPER_FLAG = "--mfm-native-stack"


# ── 調べる側（別プロセスで動く）────────────────────────────────────────
def walk(pid: int, tid: int, max_frames: int = 64):
    """pid の tid の呼び出し履歴を «モジュール!関数+ずれ» の行で返す。"""
    import ctypes
    from ctypes import wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dbg = ctypes.WinDLL("dbghelp", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    PROCESS_ALL = 0x0400 | 0x0010 | 0x0008          # QUERY_INFORMATION | VM_READ | VM_OPERATION
    THREAD_ACC = 0x0002 | 0x0008 | 0x0040            # SUSPEND_RESUME | GET_CONTEXT | QUERY_INFORMATION
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.OpenThread.restype = wt.HANDLE
    k32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.SuspendThread.argtypes = [wt.HANDLE]
    k32.SuspendThread.restype = wt.DWORD
    k32.ResumeThread.argtypes = [wt.HANDLE]
    k32.ResumeThread.restype = wt.DWORD
    k32.GetThreadContext.argtypes = [wt.HANDLE, ctypes.c_void_p]
    k32.GetThreadContext.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]

    hp = k32.OpenProcess(PROCESS_ALL, False, pid)
    if not hp:
        return ["OpenProcess failed: %d" % ctypes.get_last_error()]
    ht = k32.OpenThread(THREAD_ACC, False, tid)
    if not ht:
        k32.CloseHandle(hp)
        return ["OpenThread failed: %d" % ctypes.get_last_error()]

    SYMOPT_UNDNAME, SYMOPT_DEFERRED_LOADS, SYMOPT_FAIL_CRITICAL = 0x2, 0x4, 0x200
    dbg.SymSetOptions.argtypes = [wt.DWORD]
    dbg.SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_FAIL_CRITICAL)
    dbg.SymInitializeW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.BOOL]
    dbg.SymInitializeW.restype = wt.BOOL
    dbg.SymInitializeW(hp, None, True)

    # CONTEXT（x64）は 1232 バイト、16 バイト境界に置く決まり
    CTX_SIZE = 1232
    raw = ctypes.create_string_buffer(CTX_SIZE + 16)
    base = ctypes.addressof(raw)
    ctx = base + ((16 - base % 16) % 16)
    CONTEXT_FULL = 0x0010000B
    ctypes.c_uint32.from_address(ctx + 0x30).value = CONTEXT_FULL

    class ADDRESS64(ctypes.Structure):
        _fields_ = [("Offset", ctypes.c_uint64), ("Segment", ctypes.c_uint16),
                    ("Mode", ctypes.c_uint32)]

    class STACKFRAME64(ctypes.Structure):
        _fields_ = [("AddrPC", ADDRESS64), ("AddrReturn", ADDRESS64),
                    ("AddrFrame", ADDRESS64), ("AddrStack", ADDRESS64),
                    ("AddrBStore", ADDRESS64), ("FuncTableEntry", ctypes.c_void_p),
                    ("Params", ctypes.c_uint64 * 4), ("Far", wt.BOOL),
                    ("Virtual", wt.BOOL), ("Reserved", ctypes.c_uint64 * 3),
                    ("KdHelp", ctypes.c_byte * 256)]

    AddrModeFlat = 3
    out = []
    suspended = False
    try:
        if k32.SuspendThread(ht) == 0xFFFFFFFF:
            return ["SuspendThread failed: %d" % ctypes.get_last_error()]
        suspended = True
        if not k32.GetThreadContext(ht, ctypes.c_void_p(ctx)):
            return ["GetThreadContext failed: %d" % ctypes.get_last_error()]
        rip = ctypes.c_uint64.from_address(ctx + 0xF8).value
        rsp = ctypes.c_uint64.from_address(ctx + 0x98).value
        rbp = ctypes.c_uint64.from_address(ctx + 0xA0).value
        frame = STACKFRAME64()
        frame.AddrPC.Offset, frame.AddrPC.Mode = rip, AddrModeFlat
        frame.AddrStack.Offset, frame.AddrStack.Mode = rsp, AddrModeFlat
        frame.AddrFrame.Offset, frame.AddrFrame.Mode = rbp, AddrModeFlat

        fta = ctypes.cast(dbg.SymFunctionTableAccess64, ctypes.c_void_p)
        gmb = ctypes.cast(dbg.SymGetModuleBase64, ctypes.c_void_p)
        dbg.StackWalk64.argtypes = [wt.DWORD, wt.HANDLE, wt.HANDLE,
                                    ctypes.POINTER(STACKFRAME64), ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p]
        dbg.StackWalk64.restype = wt.BOOL
        addrs = []
        for _i in range(max_frames):
            ok = dbg.StackWalk64(0x8664, hp, ht, ctypes.byref(frame), ctypes.c_void_p(ctx),
                                 None, fta, gmb, None)
            if not ok or frame.AddrPC.Offset == 0:
                break
            addrs.append(frame.AddrPC.Offset)
    finally:
        if suspended:
            k32.ResumeThread(ht)          # 何があっても必ず再開する

    # 名前を付けるのはスレッドを再開した後（相手を止めている時間を短く）
    dbg.SymGetModuleBase64.argtypes = [wt.HANDLE, ctypes.c_uint64]
    dbg.SymGetModuleBase64.restype = ctypes.c_uint64
    dbg.SymFromAddr.argtypes = [wt.HANDLE, ctypes.c_uint64,
                                ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
    dbg.SymFromAddr.restype = wt.BOOL
    psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.LPWSTR, wt.DWORD]
    SYM_SIZE, MAXNAME = 88, 1024
    sym = ctypes.create_string_buffer(SYM_SIZE + MAXNAME * 2)
    for a in addrs:
        mod = "?"
        mbase = dbg.SymGetModuleBase64(hp, a)
        if mbase:
            nb = ctypes.create_unicode_buffer(260)
            if psapi.GetModuleBaseNameW(hp, ctypes.c_void_p(mbase), nb, 260):
                mod = nb.value
        ctypes.memset(sym, 0, len(sym))
        ctypes.c_uint32.from_buffer(sym, 0).value = SYM_SIZE
        ctypes.c_uint32.from_buffer(sym, 80).value = MAXNAME
        disp = ctypes.c_uint64(0)
        name = ""
        if dbg.SymFromAddr(hp, a, ctypes.byref(disp), sym):
            nlen = ctypes.c_uint32.from_buffer(sym, 76).value
            name = sym.raw[84:84 + nlen].decode("ascii", "replace")
        if name:
            out.append("  %s!%s+0x%x" % (mod, name, disp.value))
        elif mbase:
            out.append("  %s+0x%x" % (mod, a - mbase))
        else:
            out.append("  0x%x" % a)
    dbg.SymCleanup.argtypes = [wt.HANDLE]
    dbg.SymCleanup(hp)
    k32.CloseHandle(ht)
    k32.CloseHandle(hp)
    return out


def helper_main(argv):
    """HELPER_FLAG pid tid out_path [label] で呼ばれた時の入口。"""
    try:
        i = argv.index(HELPER_FLAG)
        pid, tid, out_path = int(argv[i + 1]), int(argv[i + 2]), argv[i + 3]
        label = argv[i + 4] if len(argv) > i + 4 else ""
    except Exception:
        return 2
    try:
        lines = walk(pid, tid)
    except Exception as e:
        lines = ["walk failed: %r" % (e,)]
    try:
        import datetime
        with open(out_path, "a", encoding="utf-8") as f:
            f.write("[%s] === C++ の呼び出し履歴（メインスレッド %s）===\n%s\n"
                    % (datetime.datetime.now().strftime("%H:%M:%S"), label,
                       "\n".join(lines)))
    except OSError:
        pass
    return 0


# ── 頼む側（フリーズ見張りから呼ぶ）──────────────────────────────────
def helper_command(pid: int, tid: int, out_path: str, label: str = ""):
    """調べる側を起動するコマンド。exe（PyInstaller）なら自分自身、
    ソースから動かしている時は同じ Python で main.py を。"""
    args = [HELPER_FLAG, str(pid), str(tid), out_path, label]
    exe = sys.executable or ""
    if getattr(sys, "frozen", False):
        return [exe] + args
    # Maya の中では sys.executable が maya.exe。そのまま起動すると Maya が
    # もう 1 本立ち上がってしまうので、同じ場所の mayapy.exe を使う（無ければ諦める）
    if os.path.basename(exe).lower() in ("maya.exe", "maya"):
        py = os.path.join(os.path.dirname(exe),
                          "mayapy.exe" if exe.lower().endswith(".exe") else "mayapy")
        if not os.path.isfile(py):
            return None
        exe = py
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "main.py")
    return [exe, main_py] + args


def capture_async(tid: int, out_path: str, label: str = ""):
    """止まっているスレッド tid の C++ 側の履歴を別プロセスで取って out_path へ。
    待たない。Windows 以外・切り分けで止めた時は何もしない。"""
    if os.name != "nt":
        return None
    try:
        from core.diag import diag_off
        if diag_off("no_native_stack"):
            return None
    except Exception:
        pass
    import subprocess
    cmd = helper_command(os.getpid(), tid, out_path, label)
    if not cmd:
        return None
    try:
        return subprocess.Popen(
            cmd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True)
    except Exception:
        return None
