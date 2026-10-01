"""
Bookmark Manager
================
Manages a list of bookmarks stored as a tree in SettingsManager.

Bookmark schema
---------------
{
    "id"       : "uuid",
    "type"     : "folder" | "directory" | "file",
    "name"     : "表示名",
    "path"     : "/absolute/path",          # empty for folders
    "children" : [ <bookmark>, ... ],       # only for type="folder"
    "expanded" : true,                       # folder UI state
    "color"    : "#RRGGBB",                 # optional label colour
}

All bookmark mutations go through this class so callers don't have to
touch SettingsManager directly.
"""

import uuid
import copy
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

BookmarkNode = Dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return str(uuid.uuid4())


def _walk(nodes: List[BookmarkNode], func: Callable[[BookmarkNode, List[BookmarkNode], int], bool]):
    """
    Depth-first walk.  func(node, parent_list, index) → bool (True = stop).
    """
    for i, node in enumerate(nodes):
        if func(node, nodes, i):
            return True
        if node.get("type") == "folder":
            if _walk(node.get("children", []), func):
                return True
    return False


def _find_by_id(nodes: List[BookmarkNode], target_id: str) -> Optional[Tuple[BookmarkNode, List[BookmarkNode], int]]:
    result: List = []

    def visitor(node, parent_list, idx):
        if node["id"] == target_id:
            result.append((node, parent_list, idx))
            return True
        return False

    _walk(nodes, visitor)
    return result[0] if result else None


# ---------------------------------------------------------------------------
# BookmarkManager
# ---------------------------------------------------------------------------

class BookmarkManager:
    """
    CRUD + reorder operations for the bookmark tree.
    Call `save()` explicitly or pass save=True to mutation methods.

    Signals (pure Python callbacks, not Qt)
    ----------------------------------------
    register_on_change(callback) → called with the full bookmark list after any mutation.
    """

    def __init__(self, settings_manager):
        self._sm = settings_manager
        self._tree: List[BookmarkNode] = []
        self._on_change: List[Callable] = []
        self.reload()

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def reload(self):
        """Load bookmarks from settings (discards unsaved local changes)."""
        self._tree = self._sm.get_bookmarks()

    def save(self):
        """Persist current bookmark tree."""
        self._sm.save_bookmarks(self._tree)
        self._emit_change()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_tree(self) -> List[BookmarkNode]:
        """Return the raw tree (do not mutate directly)."""
        return copy.deepcopy(self._tree)

    def find(self, bookmark_id: str) -> Optional[BookmarkNode]:
        result = _find_by_id(self._tree, bookmark_id)
        if result:
            return copy.deepcopy(result[0])
        return None

    def all_paths(self) -> List[str]:
        """Flat list of all non-folder paths."""
        paths: List[str] = []

        def visitor(node, *_):
            if node.get("type") != "folder" and node.get("path"):
                paths.append(node["path"])
            return False

        _walk(self._tree, visitor)
        return paths

    def is_bookmarked(self, path: str) -> bool:
        found = [False]

        def visitor(node, *_):
            if node.get("path") == path:
                found[0] = True
                return True
            return False

        _walk(self._tree, visitor)
        return found[0]

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------

    def add_directory(self, path: str, name: Optional[str] = None,
                      parent_folder_id: Optional[str] = None,
                      save: bool = True) -> BookmarkNode:
        node: BookmarkNode = {
            "id": _new_id(),
            "type": "directory",
            "name": name or path.split("/")[-1] or path,
            "path": path,
            "color": None,
        }
        return self._insert(node, parent_folder_id, save)

    def add_file(self, path: str, name: Optional[str] = None,
                 parent_folder_id: Optional[str] = None,
                 save: bool = True) -> BookmarkNode:
        import os
        node: BookmarkNode = {
            "id": _new_id(),
            "type": "file",
            "name": name or os.path.basename(path),
            "path": path,
            "color": None,
        }
        return self._insert(node, parent_folder_id, save)

    def add_folder(self, name: str,
                   parent_folder_id: Optional[str] = None,
                   save: bool = True) -> BookmarkNode:
        node: BookmarkNode = {
            "id": _new_id(),
            "type": "folder",
            "name": name,
            "path": "",
            "children": [],
            "expanded": True,
            "color": None,
        }
        return self._insert(node, parent_folder_id, save)

    def _insert(self, node: BookmarkNode,
                parent_folder_id: Optional[str],
                save: bool) -> BookmarkNode:
        if parent_folder_id:
            result = _find_by_id(self._tree, parent_folder_id)
            if result:
                parent_node = result[0]
                if parent_node.get("type") == "folder":
                    parent_node.setdefault("children", []).append(node)
                    if save:
                        self.save()
                    return node
        # Top-level
        self._tree.append(node)
        if save:
            self.save()
        return node

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def rename(self, bookmark_id: str, new_name: str, save: bool = True):
        result = _find_by_id(self._tree, bookmark_id)
        if result:
            result[0]["name"] = new_name
            if save:
                self.save()

    def set_color(self, bookmark_id: str, color: Optional[str], save: bool = True):
        result = _find_by_id(self._tree, bookmark_id)
        if result:
            result[0]["color"] = color
            if save:
                self.save()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def remove(self, bookmark_id: str, save: bool = True) -> bool:
        result = _find_by_id(self._tree, bookmark_id)
        if not result:
            return False
        _, parent_list, idx = result
        parent_list.pop(idx)
        if save:
            self.save()
        return True

    # ------------------------------------------------------------------
    # Reorder (drag-and-drop support)
    # ------------------------------------------------------------------

    def move(self, bookmark_id: str,
             new_parent_id: Optional[str],
             insert_before_id: Optional[str],
             save: bool = True) -> bool:
        """
        Move a bookmark node to a new parent and position.

        Parameters
        ----------
        bookmark_id      : node to move
        new_parent_id    : target folder id (None = root)
        insert_before_id : sibling id to insert before (None = append)
        """
        # Prevent moving a folder into itself
        if new_parent_id and self._is_ancestor(bookmark_id, new_parent_id):
            return False

        # Extract node
        result = _find_by_id(self._tree, bookmark_id)
        if not result:
            return False
        node, old_parent_list, old_idx = result
        node_copy = old_parent_list.pop(old_idx)

        # Find target list
        if new_parent_id:
            target_result = _find_by_id(self._tree, new_parent_id)
            if not target_result:
                # Rollback
                old_parent_list.insert(old_idx, node_copy)
                return False
            target_list = target_result[0].setdefault("children", [])
        else:
            target_list = self._tree

        # Find insert position
        if insert_before_id:
            for i, n in enumerate(target_list):
                if n["id"] == insert_before_id:
                    target_list.insert(i, node_copy)
                    break
            else:
                target_list.append(node_copy)
        else:
            target_list.append(node_copy)

        if save:
            self.save()
        return True

    def reorder_in_place(self, parent_id: Optional[str],
                         ordered_ids: List[str],
                         save: bool = True):
        """Reorder children of a parent by providing the desired id order."""
        if parent_id:
            result = _find_by_id(self._tree, parent_id)
            if not result:
                return
            children = result[0].get("children", [])
        else:
            children = self._tree

        id_map = {n["id"]: n for n in children}
        reordered = [id_map[oid] for oid in ordered_ids if oid in id_map]
        # Append anything not in ordered_ids at the end
        known = set(ordered_ids)
        for n in children:
            if n["id"] not in known:
                reordered.append(n)

        if parent_id:
            result[0]["children"] = reordered
        else:
            self._tree = reordered

        if save:
            self.save()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_ancestor(self, ancestor_id: str, descendant_id: str) -> bool:
        """True if ancestor_id is an ancestor of descendant_id."""
        result = _find_by_id(self._tree, ancestor_id)
        if not result:
            return False
        subtree = [result[0]]
        found = [False]

        def visitor(node, *_):
            if node["id"] == descendant_id:
                found[0] = True
                return True
            return False

        _walk(subtree, visitor)
        return found[0]

    # ------------------------------------------------------------------
    # Observer
    # ------------------------------------------------------------------

    def register_on_change(self, callback: Callable):
        self._on_change.append(callback)

    def unregister_on_change(self, callback: Callable):
        try:
            self._on_change.remove(callback)
        except ValueError:
            pass

    def _emit_change(self):
        for cb in self._on_change:
            try:
                cb(self._tree)
            except Exception as e:
                print(f"[BookmarkManager] Observer error: {e}")
