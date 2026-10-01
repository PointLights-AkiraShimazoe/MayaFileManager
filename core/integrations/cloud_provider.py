# -*- coding: utf-8 -*-
"""クラウドストレージ（OneDrive / Dropbox / Google Drive）プロバイダ。

- 検出（ルート特定）:
    OneDrive     … 環境変数 OneDrive / OneDriveConsumer / OneDriveCommercial
    Dropbox      … %APPDATA%\\Dropbox\\info.json（personal/business の path）
    Google Drive … Drive for desktop の仮想ドライブ（ローカル固定ドライブで
                   ボリューム名が "Google Drive"）または ~/Google Drive
- 状態: Windows のクラウドファイル属性（lstat の st_file_attributes のみ、
  外部ツール不要・軽量）
    RECALL_ON_DATA_ACCESS / OFFLINE → オンラインのみ
    PINNED                          → 常にこのデバイスに保持
    それ以外                        → ローカルにあり
  ※「同期中」は属性から判定できないため表示しない（正直に非対応）。
- 操作: 常に保持（attrib +P -U）/ オンラインのみ（attrib -P +U）/ Webで表示
"""
from core.diag import swallow as _swallow  # r112

import json
import os

from .base import (Provider, ST_ONLINE_ONLY, ST_LOCAL, ST_PINNED, norm, run)
# 属性定数の単一の真実は core/cloud_state.py（r83。サムネ抑止と共用）
from core.cloud_state import (ATTR_DEHYDRATED as _ATTR_DEHYDRATED,
                              ATTR_PINNED as _ATTR_PINNED)


def _gdrive_roots():
    roots = []
    if os.name != "nt":
        return roots
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        for c in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = c + ":\\"
            # DRIVE_FIXED(3) のみ照会。ネットワークドライブ(4)は絶対に触らない
            if k32.GetDriveTypeW(root) != 3:
                continue
            buf = ctypes.create_unicode_buffer(261)
            ok = k32.GetVolumeInformationW(root, buf, 261, None, None, None,
                                           None, 0)
            if ok and buf.value.lower().startswith("google drive"):
                roots.append(root.rstrip("\\"))
    except Exception as _e:
        _swallow(_e, "core/integrations/cloud_provider.py:44 _gdrive_roots")
    p = os.path.join(os.path.expanduser("~"), "Google Drive")
    try:
        if os.path.isdir(p):
            roots.append(p)
    except Exception as _e:
        _swallow(_e, "core/integrations/cloud_provider.py:50 _gdrive_roots")
    return roots


class CloudProvider(Provider):
    key = "cloud"
    label = "Cloud"

    def __init__(self):
        super().__init__()
        self.roots = []      # [(service, root_path), ...]

    def detect(self):
        try:
            roots = []
            for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
                v = os.environ.get(env)
                if v and os.path.isdir(v):
                    roots.append(("OneDrive", os.path.normpath(v)))
            # レジストリのアカウント設定（既定外の場所・複数アカウントに対応）
            if os.name == "nt":
                try:
                    import winreg
                    base = r"Software\Microsoft\OneDrive\Accounts"
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(k, i)
                            except OSError:
                                break
                            i += 1
                            try:
                                with winreg.OpenKey(k, sub) as sk:
                                    uf, _t = winreg.QueryValueEx(sk, "UserFolder")
                                    if uf and os.path.isdir(uf):
                                        roots.append(("OneDrive", os.path.normpath(uf)))
                            except OSError:
                                pass
                except Exception as _e:
                    _swallow(_e, "core/integrations/cloud_provider.py:90 detect")
            for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
                if not base:
                    continue
                info = os.path.join(base, "Dropbox", "info.json")
                try:
                    with open(info, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for acc in data.values():
                        p = acc.get("path") if isinstance(acc, dict) else None
                        if p and os.path.isdir(p):
                            roots.append(("Dropbox", os.path.normpath(p)))
                except Exception as _e:
                    _swallow(_e, "core/integrations/cloud_provider.py:103 detect")
            for r in _gdrive_roots():
                roots.append(("Google Drive", os.path.normpath(r)))
            # 重複除去
            seen = set()
            self.roots = []
            for svc, r in roots:
                k = norm(r)
                if k not in seen:
                    seen.add(k)
                    self.roots.append((svc, r))
            self.available = bool(self.roots) and os.name == "nt"
            self.info = {"roots": self.roots}
        except Exception:
            self.available = False

    def service_for(self, path: str):
        d = norm(path)
        best = None
        for svc, r in self.roots:
            k = norm(r)
            if d == k or d.startswith(k + os.sep):
                if best is None or len(k) > len(norm(best[1])):
                    best = (svc, r)
        return best

    def find_root(self, directory: str):
        s = self.service_for(directory)
        return s[1] if s else None

    def fetch_status(self, root: str, directory: str):
        out = {}
        try:
            names = os.listdir(directory)
        except OSError:
            return {}
        for n in names:
            p = os.path.join(directory, n)
            try:
                st = os.lstat(p)
                attrs = getattr(st, "st_file_attributes", 0)
            except OSError:
                continue
            if attrs & _ATTR_DEHYDRATED:
                state = ST_ONLINE_ONLY
            elif attrs & _ATTR_PINNED:
                state = ST_PINNED
            else:
                state = ST_LOCAL
            out[norm(p)] = state
        return out

    # --- 操作 -------------------------------------------------------------
    def _attrib(self, flags, paths):
        def _go():
            import threading

            def _run():
                for p in paths:
                    args = ["attrib"] + flags + [p]
                    try:
                        if os.path.isdir(p):
                            args += ["/S", "/D"]
                    except Exception as _e:
                        _swallow(_e, "core/integrations/cloud_provider.py:167 _run")
                    run(args, timeout=60.0)
            threading.Thread(target=_run, daemon=True).start()
        return _go

    def _web_url(self, svc, root, path):
        rel = os.path.relpath(path, root).replace("\\", "/")
        if rel == ".":
            rel = ""
        if svc == "Dropbox":
            from urllib.parse import quote
            return "https://www.dropbox.com/home/" + quote(rel)
        if svc == "OneDrive":
            return "https://onedrive.live.com/"
        return "https://drive.google.com/drive/my-drive"

    def actions(self, root: str, paths):
        from core.i18n import tr
        paths = list(paths)
        svc = self.service_for(root)
        name = svc[0] if svc else "Cloud"
        acts = [
            (tr("常にこのデバイスに保持", "Always keep on this device"),
             self._attrib(["+P", "-U"], paths)),
            (tr("空き領域を増やす（オンラインのみ）", "Free up space (online-only)"),
             self._attrib(["-P", "+U"], paths)),
            (None, None),
        ]
        if paths:
            url = self._web_url(name, root, paths[0])

            def _open(u=url):
                import webbrowser
                try:
                    webbrowser.open(u)
                except Exception as _e:
                    _swallow(_e, "core/integrations/cloud_provider.py:203 _open")
            acts.append((tr("%s の Web で表示", "View on %s web") % name, _open))
        return acts
