# -*- coding: utf-8 -*-
# ============================================================================
# 【重要】本モジュールは **再エクスポート専用のハブ**。
# ここの import は «このファイル内で使っていない» のが正常で、
# 他モジュールが `from core.compat import Qxxx` で受け取るためにある。
# pyflakes/flake8 の «imported but unused» を鵜呑みにして消さないこと
# （2026-10 に自動整理で 77 個消してアプリ全体が起動不能になった）。
# 整理ツールを掛ける時は本ファイルを除外する。
# ============================================================================
"""
PySide2 / PySide6 compatibility layer.
Maya 2023-2024 uses PySide2, Maya 2025-2027+ uses PySide6.
(Maya 2027 ships Python 3.13 - keep this module 3.9-3.13 compatible.)
Standalone mode auto-selects the available version.
"""

PYSIDE_VERSION = None

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import (
        Qt, Signal, Slot, QThread, QTimer, QModelIndex,
        QDir, QFileInfo, QUrl, QMimeData, QSortFilterProxyModel,
        QPoint, QSize, QRect, QObject, QRunnable, QThreadPool,
        QSettings, QStandardPaths
    )
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QDialog,
        QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
        QSplitter, QTreeView, QListView, QColumnView,
        QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit,
        QToolBar, QMenu, QMenuBar, QStatusBar,
        QDockWidget, QTabWidget, QStackedWidget,
        QSizePolicy, QFrame, QScrollArea,
        QAbstractItemView, QHeaderView,
        QMessageBox, QFileDialog, QInputDialog,
        QProgressDialog, QToolButton, QCheckBox, QRadioButton,
        QSpinBox, QGroupBox, QListWidget, QListWidgetItem,
        QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
        QStyledItemDelegate, QStyle, QStyleOption,
        QSizeGrip, QScrollBar, QSlider
    )
    from PySide6.QtGui import (
        QIcon, QPixmap, QImage, QFont, QColor, QPalette,
        QStandardItemModel, QStandardItem, QPainter, QPen, QBrush,
        QCursor, QDrag, QKeySequence, QAction,
        QFontMetrics, QMovie
    )
    from PySide6.QtCore import QFileSystemWatcher
    from PySide6.QtWidgets import QFileSystemModel
    PYSIDE_VERSION = 6

except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtCore import (
        Qt, Signal, Slot, QThread, QTimer, QModelIndex,
        QDir, QFileInfo, QUrl, QMimeData, QSortFilterProxyModel,
        QPoint, QSize, QRect, QObject, QRunnable, QThreadPool,
        QSettings, QStandardPaths
    )
    from PySide2.QtWidgets import (
        QApplication, QMainWindow, QWidget, QDialog,
        QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
        QSplitter, QTreeView, QListView, QColumnView,
        QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit,
        QToolBar, QMenu, QMenuBar, QStatusBar,
        QDockWidget, QTabWidget, QStackedWidget,
        QSizePolicy, QFrame, QScrollArea,
        QAbstractItemView, QHeaderView,
        QMessageBox, QFileDialog, QInputDialog,
        QProgressDialog, QToolButton, QCheckBox, QRadioButton,
        QSpinBox, QGroupBox, QListWidget, QListWidgetItem,
        QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
        QStyledItemDelegate, QStyle, QStyleOption,
        QSizeGrip, QScrollBar, QSlider, QAction,
        QFileSystemModel
    )
    from PySide2.QtGui import (
        QIcon, QPixmap, QImage, QFont, QColor, QPalette,
        QStandardItemModel, QStandardItem, QPainter, QPen, QBrush,
        QCursor, QDrag, QKeySequence,
        QFontMetrics, QMovie
    )
    from PySide2.QtCore import QFileSystemWatcher
    PYSIDE_VERSION = 2


def get_pyside_version() -> int:
    return PYSIDE_VERSION


def exec_app(app):
    """Cross-version QApplication.exec() call."""
    if PYSIDE_VERSION == 6:
        return app.exec()
    else:
        return app.exec_()
