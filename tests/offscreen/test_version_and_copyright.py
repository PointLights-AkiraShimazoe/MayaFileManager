# -*- coding: utf-8 -*-
"""r119: バージョンと著作権表示は «1 箇所» から出ること。

about ダイアログに "v1.0" と手書きされており、0.9.x になっても取り残されて
いた（ユーザー指摘 2026-10-01）。著作権年も根拠のない 2025 のままだった。
同じ取り残しを繰り返さないよう、«手書きが無いこと» を機械的に確かめる。
"""
import io
import os
import re
from _common import *  # noqa: F401,F403
from _common import finish, ROOT

from core.version import (__version__, version_string, COPYRIGHT,
                          COPYRIGHT_YEAR, COMPANY)

# 1) 体裁
assert re.match(r"^\d+\.\d+\.\d+$", __version__), __version__
assert version_string().startswith("v" + __version__), version_string()
assert COPYRIGHT == "© %s %s" % (COPYRIGHT_YEAR, COMPANY), COPYRIGHT
assert COMPANY.endswith("Inc."), COMPANY
print("version / copyright have a single source and a sane shape: OK")

# 2) UI・ビルド資源にバージョン番号や著作権を «手書き» していないこと
SKIP_DIRS = {".git", "__pycache__", "docs", "tests", "dist", "build"}
BAD_VERSION = re.compile(r"v\d+\.\d+(\.\d+)?")
BAD_COPY = re.compile(r"©\s*\d{4}|\(c\)\s*\d{4}|\\xa9\s*\d{4}", re.I)
offenders = []
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fn in files:
        if not fn.endswith((".py", ".txt", ".iss")):
            continue
        path = os.path.join(base, fn)
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        if rel in ("core/version.py",):
            continue              # ここが唯一の出所
        try:
            text = io.open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#") or "CHANGELOG" in line:
                continue
            m = BAD_COPY.search(line)
            if m:
                # ビルド資源は Actions がタグから生成するので年だけ許す。
                # ただし «現在の年と会社名» に一致していること。
                if rel.endswith("version_info.txt"):
                    assert COPYRIGHT_YEAR in line and "Inc." in line, (rel, ln, line)
                    continue
                offenders.append("%s:%d %s" % (rel, ln, line.strip()))
            if rel.startswith("ui/") and BAD_VERSION.search(line) \
                    and "version_string" not in line:
                offenders.append("%s:%d %s" % (rel, ln, line.strip()))
assert not offenders, "バージョン/著作権が手書きされている:\n" + "\n".join(offenders)
print("no hand-written version or copyright outside core/version.py: OK")

finish()
