"""
Settings Manager
================
Persists all application state as JSON under:
    ~/.maya_file_manager/
        settings.json          ← global settings
        state_global.json      ← global UI state (shared across all Maya versions)
        state_maya_<ver>.json  ← per-Maya-version UI state

Design goals
------------
* All reads return safe defaults so callers never get KeyError.
* Global state is the source of truth unless a Maya-version override exists.
* Observers (callbacks) can be registered to react to specific key changes.
"""

import json
import os
import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: Dict[str, Any] = {
    # --- General ---
    "app_version": "1.0.0",
    "theme": "dark",                       # "dark" | "light"
    "language": "ja",

    # --- File Browser ---
    "column_max_depth": 4,                 # Max columns in column view
    "column_auto_width": True,             # Auto-fit column width to content
    "single_click_action": "preview",      # "preview" | "open" | "import" | "reference"
    "double_click_action": "open",
    "show_hidden_files": False,
    "thumbnail_size": 128,                 # px
    "thumbnail_cache_size": 256,           # number of cached thumbnails
    "sort_by": "name",                     # "name" | "type" | "timestamp"
    "sort_order": "asc",                   # "asc" | "desc"
    "filter_string": "",
    "file_extensions_visible": [".ma", ".mb", ".fbx", ".obj", ".abc", ".usd"],

    # --- History ---
    "history_max_count": 50,
    "history_per_maya": False,             # False = shared across all Maya versions

    # --- Bookmarks ---
    "bookmarks_per_maya": False,           # False = shared across all Maya versions

    # --- Quick-nav presets ---
    "quick_nav_preset": "default",         # active preset name

    # --- Auto-naming ---
    "auto_naming_enabled": True,
    "auto_naming_config_path": "",

    # --- Maya launch ---
    "last_maya_version": "",
    "maya_launch_args": [],

    # --- Reference presets ---
    "reference_preset_active": "",

    # --- Window geometry ---
    "window_geometry": None,
    "window_state": None,
    "splitter_sizes": {},
}

DEFAULT_GLOBAL_STATE: Dict[str, Any] = {
    "current_directory": "",
    "history": [],
    "bookmarks": [],
    "quick_nav_presets": {
        "default": []
    },
    "reference_presets": {},
    "auto_naming_rules": {},
    "drive_history": [],
}


# ---------------------------------------------------------------------------
# SettingsManager
# ---------------------------------------------------------------------------

class SettingsManager:
    """
    Central settings hub. Instantiate once and share across the application.

    Usage
    -----
        mgr = SettingsManager()
        mgr.set("column_max_depth", 5)
        depth = mgr.get("column_max_depth")      # → 5
        mgr.set_state("history", [...])           # writes to active state file
    """

    def __init__(self, maya_version: Optional[str] = None):
        self._maya_version: Optional[str] = maya_version
        self._root = self._resolve_root()
        self._root.mkdir(parents=True, exist_ok=True)

        self._settings: Dict[str, Any] = {}
        self._global_state: Dict[str, Any] = {}
        self._version_state: Dict[str, Any] = {}

        self._observers: Dict[str, List[Callable]] = {}

        self.load()

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_root() -> Path:
        home = Path.home()
        return home / ".maya_file_manager"

    def _settings_path(self) -> Path:
        return self._root / "settings.json"

    def _global_state_path(self) -> Path:
        return self._root / "state_global.json"

    def _version_state_path(self, version: str) -> Path:
        return self._root / f"state_maya_{version}.json"

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self):
        """Load all JSON files. Missing keys get safe defaults."""
        self._settings = self._load_json(self._settings_path(), DEFAULT_SETTINGS)
        self._global_state = self._load_json(self._global_state_path(), DEFAULT_GLOBAL_STATE)
        if self._maya_version:
            self._version_state = self._load_json(
                self._version_state_path(self._maya_version), {}
            )

    def save(self):
        """Persist all state to disk."""
        self._save_json(self._settings_path(), self._settings)
        self._save_json(self._global_state_path(), self._global_state)
        if self._maya_version:
            self._save_json(
                self._version_state_path(self._maya_version),
                self._version_state,
            )

    def save_settings_only(self):
        self._save_json(self._settings_path(), self._settings)

    # ------------------------------------------------------------------
    # Settings (preferences)
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Read a setting value. Falls back to DEFAULT_SETTINGS, then `default`."""
        if key in self._settings:
            return self._settings[key]
        if key in DEFAULT_SETTINGS:
            return DEFAULT_SETTINGS[key]
        return default

    def set(self, key: str, value: Any, save: bool = True):
        """Write a setting value and optionally persist immediately."""
        old_value = self._settings.get(key)
        self._settings[key] = value
        if save:
            self.save_settings_only()
        if old_value != value:
            self._notify(key, value)

    # ------------------------------------------------------------------
    # State (runtime state, per-feature keys)
    # ------------------------------------------------------------------

    def get_state(self, key: str, default: Any = None) -> Any:
        """
        Read state from the active state store.
        If a Maya-version override exists, it takes priority over global state.
        """
        # Per-version override?
        if self._maya_version and key in self._version_state:
            return self._version_state[key]
        # Global
        if key in self._global_state:
            return self._global_state[key]
        # Default schema
        if key in DEFAULT_GLOBAL_STATE:
            return copy.deepcopy(DEFAULT_GLOBAL_STATE[key])
        return default

    def set_state(self, key: str, value: Any,
                  maya_version_only: bool = False,
                  save: bool = True):
        """
        Write state.

        Parameters
        ----------
        maya_version_only : bool
            If True and a Maya version is active, write only to the version
            state (not global), so other versions are unaffected.
        """
        if maya_version_only and self._maya_version:
            self._version_state[key] = value
        else:
            self._global_state[key] = value
            # Clear the per-version override so global takes effect everywhere
            if self._maya_version and key in self._version_state:
                del self._version_state[key]

        if save:
            self.save()
        self._notify(key, value)

    def set_maya_version(self, version: Optional[str]):
        """Switch active Maya version context (reloads version state)."""
        self._maya_version = version
        if version:
            self._version_state = self._load_json(
                self._version_state_path(version), {}
            )
        else:
            self._version_state = {}

    def get_active_maya_version(self) -> Optional[str]:
        return self._maya_version

    # ------------------------------------------------------------------
    # Convenience state accessors
    # ------------------------------------------------------------------

    # ── History ─────────────────────────────────────────────────────────

    def get_history(self) -> List[str]:
        per_maya = self.get("history_per_maya")
        key = "history"
        if per_maya and self._maya_version:
            return self._version_state.get(key, [])
        return self._global_state.get(key, [])

    def add_to_history(self, path: str):
        per_maya = self.get("history_per_maya")
        key = "history"
        max_count = self.get("history_max_count", 50)

        history = self.get_history()
        if path in history:
            history.remove(path)
        history.insert(0, path)
        history = history[:max_count]

        if per_maya and self._maya_version:
            self._version_state[key] = history
        else:
            self._global_state[key] = history
        self.save()

    def clear_history(self):
        per_maya = self.get("history_per_maya")
        key = "history"
        if per_maya and self._maya_version:
            self._version_state[key] = []
        else:
            self._global_state[key] = []
        self.save()

    # ── Bookmarks ────────────────────────────────────────────────────────

    def get_bookmarks(self) -> List[Dict]:
        per_maya = self.get("bookmarks_per_maya")
        key = "bookmarks"
        if per_maya and self._maya_version:
            return self._version_state.get(key, [])
        return self._global_state.get(key, [])

    def save_bookmarks(self, bookmarks: List[Dict]):
        per_maya = self.get("bookmarks_per_maya")
        key = "bookmarks"
        if per_maya and self._maya_version:
            self._version_state[key] = bookmarks
        else:
            self._global_state[key] = bookmarks
        self.save()

    # ── Quick-nav presets ────────────────────────────────────────────────

    def get_quick_nav_presets(self) -> Dict[str, List[Dict]]:
        return self._global_state.get("quick_nav_presets", {"default": []})

    def get_active_quick_nav(self) -> List[Dict]:
        preset_name = self.get("quick_nav_preset", "default")
        presets = self.get_quick_nav_presets()
        return presets.get(preset_name, [])

    def save_quick_nav_presets(self, presets: Dict[str, List[Dict]]):
        self._global_state["quick_nav_presets"] = presets
        self.save()

    # ── Reference presets ────────────────────────────────────────────────

    def get_reference_presets(self) -> Dict[str, Any]:
        return self._global_state.get("reference_presets", {})

    def save_reference_presets(self, presets: Dict[str, Any]):
        self._global_state["reference_presets"] = presets
        self.save()

    # ── Auto-naming rules ────────────────────────────────────────────────

    def get_auto_naming_rules(self) -> Dict[str, Any]:
        return self._global_state.get("auto_naming_rules", {})

    def save_auto_naming_rules(self, rules: Dict[str, Any]):
        self._global_state["auto_naming_rules"] = rules
        self.save()

    # ------------------------------------------------------------------
    # Observer / reactive pattern
    # ------------------------------------------------------------------

    def register_observer(self, key: str, callback: Callable[[Any], None]):
        """Register a callback invoked when `key` changes value."""
        self._observers.setdefault(key, []).append(callback)

    def unregister_observer(self, key: str, callback: Callable[[Any], None]):
        if key in self._observers:
            try:
                self._observers[key].remove(callback)
            except ValueError:
                pass

    def _notify(self, key: str, value: Any):
        for cb in self._observers.get(key, []):
            try:
                cb(value)
            except Exception as e:
                print(f"[SettingsManager] Observer error for '{key}': {e}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path, defaults: Dict) -> Dict:
        result = copy.deepcopy(defaults)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                result.update(loaded)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[SettingsManager] Could not load {path}: {e}")
        return result

    @staticmethod
    def _save_json(path: Path, data: Dict):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[SettingsManager] Could not save {path}: {e}")

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump(self):
        """Print current state summary (debug)."""
        print("=== SettingsManager dump ===")
        print(f"  Maya version : {self._maya_version}")
        print(f"  Root         : {self._root}")
        print(f"  Settings keys: {list(self._settings.keys())}")
        print(f"  Global state keys: {list(self._global_state.keys())}")
        print(f"  Version state keys: {list(self._version_state.keys())}")
