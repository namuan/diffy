from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from diffy.core.models import DraftComment, PullRequest, PullRequestRef, ReviewComment, ReviewThread
from diffy.services.gh_client import LoadedPullRequest
from diffy.services.persistence import Persistence
from diffy.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "assets" / "product-tour"
DOC_PATH = ROOT / "docs" / "product-tour.md"

DIFF_TEXT = """diff --git a/src/review.py b/src/review.py
index 1234567..89abcde 100644
--- a/src/review.py
+++ b/src/review.py
@@ -1,5 +1,11 @@
 from pathlib import Path
 
 
 def review(files):
-    return files
+    return sorted(files)
+
+
+def summarize(files):
+    return len(files)
+
+
+def format_summary(files):
+    return f\"{len(files)} files\"
diff --git a/src/search.py b/src/search.py
index 1234567..89abcde 100644
--- a/src/search.py
+++ b/src/search.py
@@ -1,4 +1,7 @@
 def find_files(paths, query):
-    return [path for path in paths if query in path]
+    normalized = query.lower()
+    return [path for path in paths if normalized in path.lower()]
+
+
+def recent(paths):
+    return paths[:10]
diff --git a/docs/guide.md b/docs/guide.md
index 1234567..89abcde 100644
--- a/docs/guide.md
+++ b/docs/guide.md
@@ -1,3 +1,6 @@
 # Review guide
 
-Review changes carefully.
+Review changes in the canvas first.
+
+Use Cmd+Shift+F to find a file quickly.
+
+Submit a review when finished.
diff --git a/tests/test_review.py b/tests/test_review.py
index 1234567..89abcde 100644
--- a/tests/test_review.py
+++ b/tests/test_review.py
@@ -1,4 +1,7 @@
 def test_review():
-    assert True
+    files = [\"src/review.py\"]
+    assert review(files) == files
+
+
+def test_summary():
+    assert summarize([]) == 0
"""


def fixture_pull_request() -> LoadedPullRequest:
    ref = PullRequestRef.parse("https://github.com/diffy-demo/review/pull/42")
    pull_request = PullRequest(
        ref=ref,
        title="Improve review navigation and submission",
        body="A representative pull request for the diffy product tour.",
        author="maintainer",
        base_ref="main",
        head_ref="feature/review-navigation",
        base_sha="base-sha",
        head_sha="head-sha",
        url=ref.url,
        state="OPEN",
    )
    threads = [
        ReviewThread(
            thread_id="thread-1",
            path="src/review.py",
            line=5,
            start_line=None,
            side="RIGHT",
            start_side=None,
            resolved=False,
            comments=[
                ReviewComment(
                    database_id=101,
                    body="Could this sorting behavior be covered by a focused test?",
                    author="alex",
                    created_at="2025-01-10T10:00:00Z",
                    url="https://github.com/diffy-demo/review/pull/42#discussion_r101",
                )
            ],
        ),
        ReviewThread(
            thread_id="thread-4",
            path="src/review.py",
            line=9,
            start_line=None,
            side="RIGHT",
            start_side=None,
            resolved=True,
            comments=[
                ReviewComment(
                    database_id=104,
                    body="This summary behavior is covered by the existing tests.",
                    author="sam",
                    created_at="2025-01-10T10:30:00Z",
                    url="https://github.com/diffy-demo/review/pull/42#discussion_r104",
                )
            ],
        ),
        ReviewThread(
            thread_id="thread-2",
            path="src/search.py",
            line=3,
            start_line=None,
            side="RIGHT",
            start_side=None,
            resolved=True,
            comments=[
                ReviewComment(
                    database_id=102,
                    body="Nice improvement to case-insensitive matching.",
                    author="sam",
                    created_at="2025-01-10T11:00:00Z",
                    url="https://github.com/diffy-demo/review/pull/42#discussion_r102",
                )
            ],
        ),
        ReviewThread(
            thread_id="thread-3",
            path="docs/guide.md",
            line=None,
            start_line=None,
            side=None,
            start_side=None,
            resolved=False,
            comments=[
                ReviewComment(
                    database_id=103,
                    body="The guide should mention the keyboard shortcut.",
                    author="alex",
                    created_at="2025-01-10T12:00:00Z",
                    url="https://github.com/diffy-demo/review/pull/42#discussion_r103",
                )
            ],
        ),
    ]
    return LoadedPullRequest(pull_request, DIFF_TEXT, threads)


def fixture_draft(pull_request: PullRequest) -> DraftComment:
    return DraftComment(
        id="tour-draft-1",
        ref_key=pull_request.ref.key,
        path="src/review.py",
        head_sha=pull_request.head_sha,
        side="RIGHT",
        line=9,
        start_line=None,
        body="Please add a test for the empty list case.",
        context="def summarize(files):",
    )


def process_events(application: QApplication) -> None:
    application.processEvents()
    QTest.qWait(40)
    application.processEvents()


def capture_window(window: MainWindow, name: str, overlay: QWidget | None = None) -> None:
    image = window.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    if overlay and overlay.isVisible():
        overlay_image = overlay.grab().toImage()
        global_position = overlay.mapToGlobal(QPoint(0, 0))
        position = window.mapFromGlobal(global_position)
        painter = QPainter(image)
        painter.drawImage(position, overlay_image)
        painter.end()
    image.save(str(OUTPUT_DIR / f"{name}.png"))


def visible_dialog() -> QDialog | None:
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QDialog) and widget.isVisible():
            return widget
    return None


def generate() -> list[tuple[str, str, str]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for previous in OUTPUT_DIR.glob("*.png"):
        previous.unlink()
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    entries: list[tuple[str, str, str]] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        persistence = Persistence(Path(temporary_directory) / "tour.sqlite3")
        window = MainWindow(persistence=persistence)
        window.resize(1450, 950)
        window.show()
        loaded = fixture_pull_request()
        window.ref_input.setText(loaded.pull_request.url)
        window._load_finished(loaded)
        window.drafts = [fixture_draft(loaded.pull_request)]
        window._render_loaded_state()
        window.status_label.setText(f"{len(window.files)} files · {len(window.threads)} threads · 1 active draft")
        process_events(application)

        capture_window(window, "01-canvas-overview")
        entries.append(("01-canvas-overview.png", "Canvas overview", "Navigate the pull request through a compact file tree with change and comment badges; use the visible −, +, Fit, mouse-wheel, or trackpad pinch controls to manage large trees."))

        window.canvas.focus_first_node()
        QTest.keyClick(window.canvas.focused_node, Qt.Key.Key_Right)
        QTest.keyClick(window.canvas.focused_node, Qt.Key.Key_Space)
        process_events(application)
        capture_window(window, "03-keyboard-navigation")
        entries.append(("03-keyboard-navigation.png", "Keyboard navigation", "Move through the tree with the arrow keys, use Space for folders, and use Cmd+Right or Cmd+Left to expand or collapse one level."))
        window.canvas.focus_node_by_target("src/review.py")
        process_events(application)
        capture_window(window, "02-comment-status")
        entries.append(("02-comment-status.png", "Open and resolved comments", "A file with both states shows purple open comments and gray resolved comments beside its change badges."))

        window.show_quick_search()
        process_events(application)
        search = window.quick_search_dialog
        search.search_input.setText("review")
        process_events(application)
        capture_window(window, "04-quick-search", search)
        entries.append(("04-quick-search.png", "Quick search", "Press Cmd+Shift+F to search files and highlight the selected result in the canvas."))
        search.reject()
        process_events(application)

        window.open_diff("src/review.py")
        process_events(application)
        capture_window(window, "05-diff-comments")
        entries.append(("05-diff-comments.png", "Diff and inline comments", "Review changed lines with inline threads, draft comments, and Reply or Resolve actions."))

        menu_position = window.reviewer_filter_button.mapToGlobal(QPoint(0, window.reviewer_filter_button.height()))
        window.reviewer_filter_menu.popup(menu_position)
        process_events(application)
        capture_window(window, "06-reviewer-filter", window.reviewer_filter_menu)
        entries.append(("06-reviewer-filter.png", "Reviewer filtering", "Hide comments from selected reviewers while keeping the diff and canvas counts in sync."))
        window.reviewer_filter_menu.close()
        window.show_canvas()
        process_events(application)

        def capture_submit_dialog() -> None:
            dialog = visible_dialog()
            if dialog:
                capture_window(window, "07-submit-review", dialog)
                dialog.reject()
            else:
                QTimer.singleShot(20, capture_submit_dialog)

        QTimer.singleShot(0, capture_submit_dialog)
        window.submit_review()
        process_events(application)
        entries.append(("07-submit-review.png", "Submit review", "Submit a review summary and draft line comments as a comment, approval, or change request."))
        window.close()
    return entries


def write_document(entries: list[tuple[str, str, str]]) -> None:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# diffy product tour", "", "Generated by `make tour`. Do not edit the screenshots manually.", ""]
    for image, title, description in entries:
        lines.extend([f"## {title}", "", description, "", f"![{title}](../assets/product-tour/{image})", ""])
    DOC_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    entries = generate()
    write_document(entries)
    print(f"Generated {len(entries)} product tour screenshots in {OUTPUT_DIR}")
    print(f"Wrote {DOC_PATH}")


if __name__ == "__main__":
    main()
