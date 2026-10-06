# -*- coding: utf-8 -*-
"""MayaFileManager のバージョン定義（単一の真実）。

exe のバージョン資源（version_info.txt）と Inno Setup の AppVersion は、
GitHub Actions がタグ名から生成して上書きする（.github/workflows/build.yml）。
ここはアプリ内表示とログ用。**タグを打つ時はここも合わせて更新する。**
"""

__version__ = "0.9.10"

# 著作権表示の唯一の出所（r119）。
# 年は «最初に公表した年»。このツールの公開は 2026 年なので 2026。
# 以前 UI に 2025 と書かれていたが、根拠のない固定値だった。
COPYRIGHT_YEAR = "2026"
COMPANY = "PointLights for entertainment Inc."
COPYRIGHT = "\u00a9 %s %s" % (COPYRIGHT_YEAR, COMPANY)

# 開発中であることを示す（1.0 未満 = 仕様が変わり得る）
IS_PRERELEASE = True


def version_string() -> str:
    return "v%s%s" % (__version__, "（開発版）" if IS_PRERELEASE else "")
