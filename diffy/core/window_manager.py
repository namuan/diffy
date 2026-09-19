from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QMainWindow


class WindowManager(QObject):
    def __init__(self):
        super().__init__()
        self._windows: dict[str, QMainWindow] = {}

    def get_window_key(self, repo: str, pr_number: int) -> str:
        return f"{repo.strip().lower()}#{pr_number}"

    def open_pr(self, repo: str, pr_number: int, factory) -> QMainWindow:
        key = self.get_window_key(repo, pr_number)
        existing = self._windows.get(key)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        window = factory(repo, pr_number)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(lambda: self._windows.pop(key, None))
        self._windows[key] = window
        window.show()
        return window
