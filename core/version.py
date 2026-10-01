# -*- coding: utf-8 -*-
"""MayaFileManager のバージョン定義（単一の真実）。

exe のバージョン資源（version_info.txt）と Inno Setup の AppVersion は、
GitHub Actions がタグ名から生成して上書きする（.github/workflows/build.yml）。
ここはアプリ内表示とログ用。**タグを打つ時はここも合わせて更新する。**
"""

__version__ = "0.9.3"

# 開発中であることを示す（1.0 未満 = 仕様が変わり得る）
IS_PRERELEASE = True


def version_string() -> str:
    return "v%s%s" % (__version__, "（開発版）" if IS_PRERELEASE else "")
