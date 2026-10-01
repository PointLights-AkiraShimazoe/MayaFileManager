"""
i18n — UI表示言語（日本語 / English）
=====================================

方針:
- 文字列カタログは持たず、呼び出し側で `tr("日本語", "English")` と両言語を
  併記する（可読性と追加コストを最小にする軽量方式）。
- 言語モードは設定キー "ui_language":
    "auto" … Mayaの言語モードに追従（既定）。Maya外では
             MAYA_UI_LANGUAGE 環境変数 → OSロケール の順で判定。
    "ja"   … 常に日本語
    "en"   … 常に English
- 反映タイミングは起動時（init）。切替は再起動後に反映される。
"""

import os

_lang = None          # "ja" / "en"（解決済み）
_mode = "auto"


def _detect_auto() -> str:
    """Mayaの言語モード→環境変数→OSロケールの順で ja/en を判定する。"""
    # 1) Maya内: Maya自身のUI言語
    try:
        import maya.cmds as cmds
        if hasattr(cmds, "about"):
            ui = str(cmds.about(uiLanguage=True) or "")
            if ui.lower().startswith("ja"):
                return "ja"
            if ui:
                return "en"
    except Exception:
        pass
    # 2) スタンドアロン: Mayaの言語設定を引き継ぐ環境変数
    env = os.environ.get("MAYA_UI_LANGUAGE", "")
    if env.lower().startswith("ja"):
        return "ja"
    if env:
        return "en"
    # 3) OSロケール
    try:
        import locale
        loc = locale.getdefaultlocale()[0] or ""
        if loc.lower().startswith("ja"):
            return "ja"
    except Exception:
        pass
    try:
        from core.compat import QLocale
        if QLocale.system().name().lower().startswith("ja"):
            return "ja"
    except Exception:
        pass
    return "en"


def init(settings_manager=None):
    """起動時に一度呼ぶ。settings の "ui_language" で言語を確定する。"""
    global _lang, _mode
    mode = "auto"
    if settings_manager is not None:
        try:
            mode = str(settings_manager.get("ui_language", "auto") or "auto")
        except Exception:
            mode = "auto"
    _mode = mode
    if mode == "ja":
        _lang = "ja"
    elif mode == "en":
        _lang = "en"
    else:
        _lang = _detect_auto()


def current_lang() -> str:
    global _lang
    if _lang is None:
        _lang = _detect_auto()
    return _lang


def tr(ja: str, en: str) -> str:
    """現在の言語に応じて ja / en のどちらかを返す。"""
    return ja if current_lang() == "ja" else en
