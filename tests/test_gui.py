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
from diffy.ui.main_window import MainWindow, QuickSearchDialog
from diffy.ui.tree_node import TreeNodeWidget


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
            self.assertGreater(len(window.files), 0)
            self.assertGreater(len(window.canvas.scene.items()), 0)
            tree_nodes = [item.widget() for item in window.canvas.scene.items() if isinstance(item, QGraphicsProxyWidget) and isinstance(item.widget(), TreeNodeWidget)]
            self.assertTrue(any(node.node_name == "cmd" and "📁" in node.node_icon for node in tree_nodes))
            self.assertTrue(any(node.node_name == "provider_cmd.go" for node in tree_nodes))
            assurance_node = next(node for node in tree_nodes if node.node_name == "ASSURANCE_CASE.md")
            self.assertGreater(assurance_node.width(), 285)
            comment_paths = {thread.path for thread in window.threads if thread.comments}
            self.assertTrue(comment_paths)
            for path in comment_paths:
                filename = path.rsplit("/", 1)[-1]
                self.assertTrue(any(node.node_name == filename and node.node_comment_count > 0 for node in tree_nodes))
                folder_parts = path.split("/")[:-1]
                for index in range(1, len(folder_parts) + 1):
                    folder_name = folder_parts[index - 1]
                    self.assertTrue(any(node.node_name == folder_name and "📁" in node.node_icon and node.node_comment_count > 0 for node in tree_nodes))
            window.show_canvas()
            self.application.processEvents()
            self.assertEqual(window.canvas.focused_node.node_name, "Root")
            QTest.keyClick(window.canvas.focused_node, Qt.Key.Key_Right)
            self.application.processEvents()
            self.assertEqual(window.canvas.focused_node.node_name, "cmd")
            QTest.keyClick(window.canvas.focused_node, Qt.Key.Key_Space)
            self.application.processEvents()
            self.assertIn("cmd", window.canvas.collapsed_folders)
            QTest.keyClick(window.canvas.focused_node, Qt.Key.Key_Space)
            self.application.processEvents()
            self.assertNotIn("cmd", window.canvas.collapsed_folders)
            provider_node = next(node for node in window.canvas.node_widgets if node.node_name == "provider_cmd.go")
            window.canvas._focus_node(provider_node)
            QTest.keyClick(provider_node, Qt.Key.Key_Down)
            self.application.processEvents()
            self.assertEqual(window.canvas.focused_node.node_name, "provider_cmd_test.go")
            window.canvas._focus_node(provider_node)
            QTest.keyClick(provider_node, Qt.Key.Key_Return)
            self.application.processEvents()
            self.assertEqual(window.view_stack.currentIndex(), 1)
            QTest.keyClick(window.diff_viewer, Qt.Key.Key_Escape)
            self.application.processEvents()
            folder_proxy = next(
                item for item in window.canvas.scene.items()
                if isinstance(item, QGraphicsProxyWidget)
                and isinstance(item.widget(), TreeNodeWidget)
                and item.widget().node_name == "cmd"
            )
            QTest.mouseClick(folder_proxy.widget(), Qt.MouseButton.LeftButton)
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
            comment_thread = next(thread for thread in window.threads if thread.comments and thread.line)
            window.open_diff(comment_thread.path)
            rendered_diff = window.diff_viewer.toHtml()
            self.assertIn(comment_thread.comments[0].author, rendered_diff)
            self.assertIn("action:reply:", rendered_diff)
            self.assertTrue("Resolve" in rendered_diff or "Unresolve" in rendered_diff)
            self.assertIn("#c026d3", rendered_diff)
            file_comment_thread = next(thread for thread in window.threads if thread.comments and thread.line is None)
            window.open_diff(file_comment_thread.path)
            file_comment_html = window.diff_viewer.toHtml()
            self.assertIn("File comments", file_comment_html)
            self.assertIn(file_comment_thread.comments[0].author, file_comment_html)
            search = QuickSearchDialog(window.files, window)
            search.search_input.setText("provider")
            self.application.processEvents()
            self.assertEqual(search.results.count(), 2)
            selected_paths: list[str] = []
            search.selected.connect(selected_paths.append)
            search.selected.connect(window.highlight_canvas_node)
            QTest.keyClick(search.search_input, Qt.Key.Key_Return)
            self.application.processEvents()
            self.assertEqual(selected_paths, ["cmd/opencodereview/provider_cmd.go"])
            self.assertEqual(window.view_stack.currentIndex(), 0)
            self.assertTrue(window.canvas.focused_node.node_name.endswith("provider_cmd.go"))
            self.assertEqual(window.quick_search_shortcut.key().toString(), "Meta+Shift+F")
            window.close()


if __name__ == "__main__":
    unittest.main()
