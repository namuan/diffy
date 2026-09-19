from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsProxyWidget, QMessageBox

from diffy.core.logging import configure_logging
from diffy.core.models import PullRequestRef
from diffy.services.gh_client import GHClient
from diffy.services.persistence import Persistence
from diffy.ui.main_window import MainWindow


PULL_REQUEST_URL = "https://github.com/alibaba/open-code-review/pull/1449"


class GuiIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configure_logging()
        cls.application = QApplication.instance() or QApplication([])

    def test_open_pull_request_in_gui(self):
        ref = PullRequestRef.parse(PULL_REQUEST_URL)
        self.assertEqual(ref.owner, "alibaba")
        self.assertEqual(ref.repository, "open-code-review")
        self.assertEqual(ref.number, 1449)
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Persistence(Path(temporary_directory) / "diffy.sqlite3")
            window = MainWindow(client=GHClient(), persistence=database)
            window.ref_input.setText(PULL_REQUEST_URL)
            errors: list[str] = []
            with patch.object(QMessageBox, "critical", side_effect=lambda *args: errors.append(str(args[-1]))):
                window.open_reference()
                deadline = time.monotonic() + 180
                while window.pull_request is None and not errors and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.05)
            self.assertFalse(errors, errors[0] if errors else "The pull request did not load")
            self.assertIsNotNone(window.pull_request)
            self.assertEqual(window.pull_request.ref.key, "github.com/alibaba/open-code-review#1449")
            self.assertGreater(window.file_list.count(), 0)
            self.assertGreater(len(window.canvas.scene.items()), 0)
            tree_labels = [item.widget().text() for item in window.canvas.scene.items() if isinstance(item, QGraphicsProxyWidget)]
            self.assertTrue(any("cmd/" in label for label in tree_labels))
            self.assertTrue(any("provider_cmd.go" in label for label in tree_labels))
            folder_proxy = next(
                item for item in window.canvas.scene.items()
                if isinstance(item, QGraphicsProxyWidget) and item.widget().text().startswith("▾ cmd/")
            )
            folder_proxy.widget().click()
            self.application.processEvents()
            self.assertIn("cmd", window.canvas.collapsed_folders)
            self.assertIn("1449", window.title_label.text())
            self.assertEqual(window.view_stack.currentIndex(), 0)
            window.canvas.file_selected.emit(window.files[0].path)
            self.application.processEvents()
            self.assertEqual(window.view_stack.currentIndex(), 1)
            QTest.keyClick(window.diff_viewer, Qt.Key.Key_Escape)
            self.application.processEvents()
            self.assertEqual(window.view_stack.currentIndex(), 0)
            window.close()


if __name__ == "__main__":
    unittest.main()
