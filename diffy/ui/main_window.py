from __future__ import annotations

import html
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

from PySide6.QtCore import QEvent, QObject, QPoint, QRunnable, QThreadPool, QTimer, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QInputDevice, QKeySequence, QNativeGestureEvent, QPalette, QPen, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diffy.core.logging import get_logger
from diffy.core.models import ChangedFile, DraftComment, PullRequest, PullRequestRef, ReviewThread
from diffy.services.anchoring import new_draft, reattach_draft
from diffy.services.diff_parser import parse_unified_diff
from diffy.services.gh_client import GHClient, GHClientError, LoadedPullRequest
from diffy.services.persistence import Persistence
from diffy.ui.tree_node import TreeNodeWidget


logger = get_logger("main_window")


def _asset_path(name: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return bundle_root / "assets" / name


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    def __init__(self, function, *arguments):
        super().__init__()
        self.function = function
        self.arguments = arguments
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        task_name = getattr(self.function, "__qualname__", repr(self.function))
        logger.debug("Background task started task=%s", task_name)
        try:
            result = self.function(*self.arguments)
            logger.debug("Background task completed task=%s result_type=%s", task_name, type(result).__name__)
            self.signals.result.emit(result)
        except Exception as error:
            logger.exception("Background task failed task=%s", task_name)
            self.signals.error.emit(str(error))


class DiffViewer(QTextBrowser):
    line_selected = Signal(int)
    comment_requested = Signal(int)
    escape_pressed = Signal()
    thread_action = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._anchor_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu_requested)
        self.setFont(QFont("SF Mono", 12))
        self.setStyleSheet("QTextBrowser { background: #ffffff; color: #111827; border: 0; }")
        self.lines = []
        self.file_path = ""
        self.selected_index: int | None = None

    def _inline_comment(self, author: str, body: str, resolved: bool = False) -> str:
        body_html = html.escape(body).replace("\n", "<br>")
        state = " · Resolved" if resolved else ""
        class_name = "inline-comment resolved" if resolved else "inline-comment"
        return (
            f'<div class="{class_name}">'
            f'<div class="comment-meta"><span class="comment-dot">●</span> {html.escape(author)}{state}</div>'
            f'<div>{body_html}</div>'
            "</div>"
        )

    def _thread_actions(self, thread: ReviewThread) -> str:
        thread_id = quote(thread.thread_id, safe="")
        resolve_label = "Unresolve" if thread.resolved else "Resolve"
        button_style = "color:#374151; background-color:#f3f4f6;"
        return (
            '<div class="thread-actions">'
            f'<a href="action:reply:{thread_id}"><span style="{button_style}">&#160;&#160;Reply&#160;&#160;</span></a>&#160;&#160;'
            f'<a href="action:toggle:{thread_id}"><span style="{button_style}">&#160;&#160;{resolve_label}&#160;&#160;</span></a>'
            "</div>"
        )

    def show_file(
        self,
        file: ChangedFile,
        drafts: list[DraftComment],
        threads: list[ReviewThread],
        hidden_reviewers: set[str] | None = None,
    ) -> None:
        self.file_path = file.path
        hidden_reviewers = hidden_reviewers or set()
        threads = [thread for thread in threads if any(comment.author not in hidden_reviewers for comment in thread.comments)]
        logger.debug("Rendering focused diff path=%s lines=%d drafts=%d threads=%d", file.path, len(file.lines), len(drafts), len(threads))
        drafts_by_line: dict[tuple[str, int | None], list[DraftComment]] = {}
        for draft in drafts:
            if draft.path == file.path and not draft.orphaned:
                drafts_by_line.setdefault((draft.side, draft.line), []).append(draft)
        threads_by_line: dict[tuple[str, int | None], list[ReviewThread]] = {}
        unanchored_threads: list[ReviewThread] = []
        available_keys = {(line.side, line.line) for line in file.lines}
        for thread in threads:
            if thread.path != file.path:
                continue
            line = thread.line if thread.line is not None else thread.start_line
            key = (thread.side or "RIGHT", line)
            if line is None or key not in available_keys:
                unanchored_threads.append(thread)
            else:
                threads_by_line.setdefault(key, []).append(thread)
        rendered = []
        if unanchored_threads:
            rendered.append('<div class="file-comments-header">File comments</div>')
            for thread in unanchored_threads:
                for comment in thread.comments:
                    if comment.author not in hidden_reviewers:
                        rendered.append(self._inline_comment(comment.author, comment.body, thread.resolved))
                rendered.append(self._thread_actions(thread))
        for index, line in enumerate(file.lines):
            old = str(line.old_line) if line.old_line is not None else ""
            new = str(line.new_line) if line.new_line is not None else ""
            marker = "+" if line.kind == "added" else "-" if line.kind == "deleted" else " "
            prefix = f"{old:>6} {new:>6} {marker} "
            value = html.escape(line.content)
            background = "#e8f5e9" if line.kind == "added" else "#ffebee" if line.kind == "deleted" else "#ffffff"
            key = (line.side, line.line)
            if key in drafts_by_line or key in threads_by_line:
                value = "● " + value
            rendered.append(
                f'<div class="diff-line"><a href="line:{index}" style="color:#374151;background:{background};">'
                f"{html.escape(prefix)}{value}</a></div>"
            )
            for draft in drafts_by_line.get(key, []):
                rendered.append(self._inline_comment("Draft", draft.body))
            for thread in threads_by_line.get(key, []):
                for comment in thread.comments:
                    if comment.author not in hidden_reviewers:
                        rendered.append(self._inline_comment(comment.author, comment.body, thread.resolved))
                rendered.append(self._thread_actions(thread))
        if not rendered:
            rendered.append('<div class="empty-diff">No textual patch is available for this file.</div>')
        self.lines = file.lines
        self.selected_index = None
        self.setHtml(
            "<style>"
            "body { background: #ffffff; color: #111827; margin: 0; }"
            ".diff-line { font-family: 'SF Mono'; font-size: 12pt; white-space: pre; }"
            ".diff-line a { display: block; padding: 3px 8px; text-decoration: none; }"
            ".file-comments-header { margin: 8px 14px 4px 14px; color: #86198f; font-family: -apple-system; font-size: 11pt; font-weight: 700; }"
            ".inline-comment { margin: 4px 14px 10px 78px; padding: 9px 12px; border-left: 3px solid #c026d3; border-radius: 4px; background: #faf5ff; color: #312e81; font-family: -apple-system; font-size: 11pt; white-space: normal; }"
            ".inline-comment.resolved { border-left-color: #94a3b8; background: #f8fafc; color: #475569; }"
            ".comment-meta { font-weight: 600; margin-bottom: 3px; }"
            ".comment-dot { color: #c026d3; }"
            ".thread-actions { margin: -4px 14px 10px 78px; white-space: normal; }"
            ".thread-actions a { display: inline-block; margin-right: 8px; padding: 4px 12px; border: 1px solid #d1d5db; border-radius: 6px; background: #f3f4f6; color: #374151; font-family: -apple-system; font-size: 11pt; text-decoration: none; }"
            ".thread-actions a:hover { background: #e5e7eb; border-color: #9ca3af; }"
            ".empty-diff { color: #6b7280; padding: 8px; }"
            "</style>"
            + "".join(rendered)
        )

    def _context_menu_requested(self, position) -> None:
        value = self.anchorAt(position)
        if not value.startswith("line:"):
            return
        index = int(value.removeprefix("line:"))
        if not 0 <= index < len(self.lines):
            return
        line = self.lines[index]
        self.selected_index = index
        self.line_selected.emit(index)
        line_number = line.line or line.old_line or 0
        menu = QMenu(self)
        comment_action = menu.addAction("Comment")
        copy_action = menu.addAction(f"Copy {self.file_path}:{line_number}")
        chosen = menu.exec(self.viewport().mapToGlobal(position))
        if chosen == comment_action:
            self.comment_requested.emit(index)
        elif chosen == copy_action:
            QApplication.clipboard().setText(f"{self.file_path}:{line_number}")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    @Slot(QUrl)
    def _anchor_clicked(self, url: QUrl) -> None:
        value = url.toString()
        if value.startswith("action:"):
            _, action, thread_id = value.split(":", 2)
            self.thread_action.emit(unquote(thread_id), action)
            return
        if not value.startswith("line:"):
            return
        index = int(value.removeprefix("line:"))
        if 0 <= index < len(self.lines):
            self.selected_index = index
            self.line_selected.emit(index)


class QuickSearchDialog(QDialog):
    selected = Signal(str)

    def __init__(self, files: list[ChangedFile], parent: QWidget | None = None):
        super().__init__(parent)
        self.files = files
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(420)
        self.setStyleSheet(
            "QDialog { background: transparent; }"
            "QFrame#searchPopup { background: #f8f9fc; border: 1px solid #cbd5e1; border-radius: 12px; }"
            "QLineEdit { background: transparent; border: 0; color: #1f2937; font-size: 15px; padding: 6px 0; }"
            "QLabel#matchCount, QLabel#position { color: #6b7280; font-size: 11px; }"
            "QListWidget { background: transparent; border: 0; outline: 0; padding: 4px 8px 8px; }"
            "QListWidget::item { border-radius: 6px; padding: 0; }"
            "QListWidget::item:selected { background: #bfd7f5; }"
        )
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("searchPopup")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(10, 8, 10, 8)
        frame_layout.setSpacing(2)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_icon = QLabel("⌕")
        search_icon.setStyleSheet("color: #6b7280; font-size: 18px; padding-right: 4px;")
        search_row.addWidget(search_icon)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.installEventFilter(self)
        self.search_input.textChanged.connect(self._search)
        self.search_input.returnPressed.connect(self._activate_current)
        search_row.addWidget(self.search_input, 1)
        frame_layout.addLayout(search_row)
        meta_row = QHBoxLayout()
        self.match_count = QLabel()
        self.match_count.setObjectName("matchCount")
        self.position = QLabel()
        self.position.setObjectName("position")
        meta_row.addWidget(self.match_count)
        meta_row.addStretch()
        meta_row.addWidget(self.position)
        frame_layout.addLayout(meta_row)
        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemActivated.connect(self._activate_item)
        self.results.currentRowChanged.connect(self._update_position)
        frame_layout.addWidget(self.results)
        outer_layout.addWidget(frame)
        self._search("")

    def _search(self, query: str) -> None:
        normalized = query.strip().lower()
        matches = [file for file in self.files if not normalized or normalized in file.path.lower()]
        self.results.clear()
        for file in matches:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, file.path)
            item.setSizeHint(QSize(0, 48))
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)
            icon = QLabel("📄")
            row_layout.addWidget(icon)
            text_layout = QVBoxLayout()
            text_layout.setContentsMargins(0, 0, 0, 0)
            name = QLabel(file.path.rsplit("/", 1)[-1])
            name.setStyleSheet("color: #1f2937; font-size: 13px;")
            path = QLabel(file.path)
            path.setStyleSheet("color: #6b7280; font-size: 10px;")
            text_layout.addWidget(name)
            text_layout.addWidget(path)
            row_layout.addLayout(text_layout, 1)
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.results.addItem(item)
            self.results.setItemWidget(item, row)
        self.match_count.setText(f"{len(matches)} match{'es' if len(matches) != 1 else ''}")
        if matches:
            self.results.setCurrentRow(0)
        else:
            self.position.clear()
        self.adjustSize()

    def _update_position(self, row: int) -> None:
        if row >= 0:
            self.position.setText(f"{row + 1} of {self.results.count()}")
        else:
            self.position.clear()

    def _activate_current(self) -> None:
        item = self.results.currentItem()
        if item:
            self._activate_item(item)

    def _activate_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        self.selected.emit(path)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Down and self.results.count():
                self.results.setCurrentRow(min(self.results.currentRow() + 1, self.results.count() - 1))
                return True
            if event.key() == Qt.Key.Key_Up and self.results.count():
                self.results.setCurrentRow(max(self.results.currentRow() - 1, 0))
                return True
        return super().eventFilter(watched, event)


class SpatialCanvas(QGraphicsView):
    file_selected = Signal(str)
    zoom_changed = Signal(float, bool)

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setMinimumHeight(180)
        self.setStyleSheet("QGraphicsView { border: 0; background: #f8fafc; }")
        self.setRenderHints(self.renderHints())
        self.zoom = 1.0
        self.fit_scale = 1.0
        self.auto_fit = True
        self.collapsed_folders: set[str] = set()
        self.tree: dict = {"folders": {}, "files": []}
        self.viewed: set[str] = set()
        self.draft_counts: dict[str, int] = {}
        self.comment_counts: dict[str, tuple[int, int]] = {}
        self.files: list[ChangedFile] = []
        self.node_widgets: list[TreeNodeWidget] = []
        self.node_proxies: dict[TreeNodeWidget, QGraphicsProxyWidget] = {}
        self.focused_node: TreeNodeWidget | None = None

    def set_files(
        self,
        files: list[ChangedFile],
        viewed: set[str],
        draft_counts: dict[str, int],
        comment_counts: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        self.comment_counts = comment_counts or {}
        self.auto_fit = True
        self.zoom = 1.0
        self.fit_scale = 1.0
        total_comments = sum(open_count + resolved_count for open_count, resolved_count in self.comment_counts.values())
        logger.debug("Rendering changed-file tree files=%d viewed=%d drafts=%d comments=%d", len(files), len(viewed), sum(draft_counts.values()), total_comments)
        self.files = files
        self.viewed = viewed
        self.draft_counts = draft_counts
        self.tree = {"folders": {}, "files": []}
        for file in files:
            parts = [part for part in file.path.split("/") if part]
            current = self.tree
            for index, part in enumerate(parts[:-1]):
                folder_path = "/".join(parts[: index + 1])
                current = current["folders"].setdefault(part, {"path": folder_path, "folders": {}, "files": []})
            current["files"].append(file)
        self._render_tree()

    def _file_count(self, node: dict) -> int:
        return len(node["files"]) + sum(self._file_count(child) for child in node["folders"].values())

    def _change_totals(self, node: dict) -> tuple[int, int]:
        additions = sum(file.additions for file in node["files"])
        deletions = sum(file.deletions for file in node["files"])
        for child in node["folders"].values():
            child_additions, child_deletions = self._change_totals(child)
            additions += child_additions
            deletions += child_deletions
        return additions, deletions

    def _folder_names(self, node: dict) -> list[str]:
        names = []
        for name, child in node["folders"].items():
            names.append(name)
            names.extend(self._folder_names(child))
        return names

    def _draft_count(self, node: dict) -> int:
        return sum(self.draft_counts.get(file.path, 0) for file in node["files"]) + sum(
            self._draft_count(child) for child in node["folders"].values()
        )

    def _comment_counts_for_node(self, node: dict) -> tuple[int, int]:
        open_count = sum(self.comment_counts.get(file.path, (0, 0))[0] for file in node["files"])
        resolved_count = sum(self.comment_counts.get(file.path, (0, 0))[1] for file in node["files"])
        for child in node["folders"].values():
            child_open, child_resolved = self._comment_counts_for_node(child)
            open_count += child_open
            resolved_count += child_resolved
        return open_count, resolved_count

    def _render_tree(self) -> None:
        self.scene.clear()
        self.node_widgets = []
        self.node_proxies = {}
        self.focused_node = None
        row_height = 88
        node_height = 64
        column_gap = 90
        metrics = QFontMetrics(QFont("Helvetica", 15))

        def estimated_width(icon: str, name: str, status: str | None, additions: int, deletions: int, comment_counts: tuple[int, int]) -> int:
            icon_width = metrics.horizontalAdvance(icon)
            name_width = metrics.horizontalAdvance(name)
            status_width = 34 if status else 0
            additions_width = metrics.horizontalAdvance(f"+{additions}") + 16
            deletions_width = metrics.horizontalAdvance(f"-{deletions}") + 16
            open_count, resolved_count = comment_counts
            comment_width = 0
            if open_count:
                comment_width += metrics.horizontalAdvance(f"● {open_count}") + 8
            if resolved_count:
                comment_width += metrics.horizontalAdvance(f"● {resolved_count}") + 8
            return max(285, 28 + icon_width + name_width + status_width + additions_width + deletions_width + comment_width + 28)

        root_additions, root_deletions = self._change_totals(self.tree)
        max_node_width = estimated_width("⌄  📁", "Root", None, root_additions, root_deletions, self._comment_counts_for_node(self.tree))
        for file in self.files:
            filename = file.path.rsplit("/", 1)[-1]
            max_node_width = max(
                max_node_width,
                estimated_width("📄", filename, "M", file.additions, file.deletions, self.comment_counts.get(file.path, (0, 0))),
            )
        for folder in self.tree["folders"].values():
            for name in self._folder_names(folder):
                folder_additions, folder_deletions = self._change_totals(folder)
                max_node_width = max(
                    max_node_width,
                    estimated_width("⌄  📁", name, None, folder_additions, folder_deletions, self._comment_counts_for_node(folder)),
                )
        column_width = max_node_width + 30
        left_margin = 30
        top_margin = 30
        pen = QPen(QColor("#cbd5e1"), 2)
        max_depth = 0

        def child_entries(node: dict) -> list[tuple[str, object]]:
            folders = [("folder", folder) for _, folder in sorted(node["folders"].items())]
            files = [("file", file) for file in sorted(node["files"], key=lambda item: item.path)]
            return folders + files

        def rows_for_entry(entry: tuple[str, object]) -> int:
            if entry[0] == "file":
                return 1
            folder = entry[1]
            if folder["path"] in self.collapsed_folders:
                return 1
            return max(1, sum(rows_for_entry(child) for child in child_entries(folder)))

        def add_connector(parent_x: float, parent_y: float, parent_width: int, child_x: float, child_y: float) -> None:
            parent_right = parent_x + parent_width
            child_left = child_x
            parent_center = parent_y + node_height / 2
            child_center = child_y + node_height / 2
            elbow_x = (parent_right + child_left) / 2
            self.scene.addLine(parent_right, parent_center, elbow_x, parent_center, pen)
            self.scene.addLine(elbow_x, parent_center, elbow_x, child_center, pen)
            self.scene.addLine(elbow_x, child_center, child_left, child_center, pen)

        def add_node(
            icon: str,
            name: str,
            status: str | None,
            additions: int,
            deletions: int,
            comment_counts: tuple[int, int],
            x: float,
            y: float,
            width: int,
            style: str,
            tooltip: str,
            callback=None,
        ) -> None:
            node = TreeNodeWidget(icon, name, status, additions, deletions, comment_counts[0], comment_counts[1], width, node_height, style, tooltip)
            if callback:
                node.clicked.connect(callback)
            node.key_action.connect(
                lambda action, node=node, is_folder="📁" in icon, target=tooltip: self._handle_node_key(
                    node, action, is_folder, target
                )
            )
            proxy = QGraphicsProxyWidget()
            proxy.setWidget(node)
            proxy.setPos(x, y)
            self.scene.addItem(proxy)
            self.node_widgets.append(node)
            self.node_proxies[node] = proxy

        folder_style = "#treeNode { border: 1px solid #fdba74; border-radius: 12px; background: #fff7ed; } #treeNode:hover { border-color: #f59e0b; background: #ffedd5; } #treeNode:focus { border: 2px solid #c2410c; }"
        file_style = "#treeNode { border: 1px solid #cbd5e1; border-radius: 12px; background: #ffffff; } #treeNode:hover { border-color: #93c5fd; background: #eff6ff; } #treeNode:focus { border: 2px solid #2563eb; }"

        def place_entry(entry: tuple[str, object], depth: int, top_row: int, parent_position: tuple[float, float, int] | None) -> tuple[float, float, int]:
            nonlocal max_depth
            kind, value = entry
            max_depth = max(max_depth, depth)
            span = rows_for_entry(entry)
            x = left_margin + depth * column_width
            y = top_margin + (top_row + (span - 1) / 2) * row_height
            if kind == "folder":
                folder = value
                folder_path = folder["path"]
                collapsed = folder_path in self.collapsed_folders
                marker = "▸" if collapsed else "⌄"
                comment_counts = self._comment_counts_for_node(folder)
                additions, deletions = self._change_totals(folder)
                name = folder_path.rsplit("/", 1)[-1]
                width = estimated_width(f"{marker}  📁", name, None, additions, deletions, comment_counts)
                if parent_position is not None:
                    add_connector(parent_position[0], parent_position[1], parent_position[2], x, y)
                add_node(
                    f"{marker}  📁",
                    name,
                    None,
                    additions,
                    deletions,
                    comment_counts,
                    x,
                    y,
                    width,
                    folder_style,
                    folder_path,
                    lambda path=folder_path: QTimer.singleShot(0, lambda: self._toggle_folder(path)),
                )
            else:
                file = value
                status = {"modified": "M", "added": "A", "deleted": "D", "renamed": "R"}.get(file.status, "M")
                filename = file.path.rsplit("/", 1)[-1]
                if file.path in self.viewed:
                    filename = f"✓  {filename}"
                comment_counts = self.comment_counts.get(file.path, (0, 0))
                width = estimated_width("📄", filename, status, file.additions, file.deletions, comment_counts)
                if parent_position is not None:
                    add_connector(parent_position[0], parent_position[1], parent_position[2], x, y)
                add_node(
                    "📄",
                    filename,
                    status,
                    file.additions,
                    file.deletions,
                    comment_counts,
                    x,
                    y,
                    width,
                    file_style,
                    file.path,
                    lambda path=file.path: self.file_selected.emit(path),
                )
            children = [] if kind == "file" or (kind == "folder" and value["path"] in self.collapsed_folders) else child_entries(value)
            cursor = top_row
            for child in children:
                child_span = rows_for_entry(child)
                place_entry(child, depth + 1, cursor, (x, y, width))
                cursor += child_span
            return x, y, span

        root_entry = ("folder", {"path": "Root", "folders": self.tree["folders"], "files": self.tree["files"]})
        root_span = rows_for_entry(root_entry)
        def render_without_root() -> None:
            root_x = left_margin
            root_y = top_margin + (root_span - 1) / 2 * row_height
            root_comment_counts = self._comment_counts_for_node(self.tree)
            root_width = estimated_width("⌄  📁", "Root", None, root_additions, root_deletions, root_comment_counts)
            add_node(
                "⌄  📁",
                "Root",
                None,
                root_additions,
                root_deletions,
                root_comment_counts,
                root_x,
                root_y,
                root_width,
                folder_style,
                "Root",
            )
            cursor = 0
            for child in child_entries(self.tree):
                child_span = rows_for_entry(child)
                place_entry(child, 1, cursor, (root_x, root_y, root_width))
                cursor += child_span
        render_without_root()
        self.scene.setSceneRect(0, 0, left_margin + (max_depth + 1) * column_width, top_margin * 2 + max(root_span, 1) * row_height)
        QTimer.singleShot(0, self._fit_tree)

    def _fit_tree(self) -> None:
        if not self.auto_fit or not self.scene.items():
            return
        rect = self.scene.sceneRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        self.fitInView(rect.adjusted(-16, -16, 16, 16), Qt.AspectRatioMode.KeepAspectRatio)
        self.fit_scale = max(self.transform().m11(), 0.001)
        self.zoom = 1.0
        self.zoom_changed.emit(self.zoom, True)

    def set_zoom(self, zoom: float) -> None:
        self.auto_fit = False
        self.zoom = max(0.5, min(2.5, zoom))
        self.resetTransform()
        self.scale(self.fit_scale * self.zoom, self.fit_scale * self.zoom)
        self.zoom_changed.emit(self.zoom, False)

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * 1.15)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom * 0.87)

    def fit_canvas(self) -> None:
        self.auto_fit = True
        self._fit_tree()

    def focus_first_node(self) -> None:
        if self.node_widgets:
            self._focus_node(self.node_widgets[0])
        else:
            self.setFocus()

    def _focus_node(self, node: TreeNodeWidget) -> None:
        if node not in self.node_widgets:
            return
        self.focused_node = node
        node.setFocus(Qt.FocusReason.OtherFocusReason)
        proxy = self.node_proxies.get(node)
        if proxy:
            self.ensureVisible(proxy)

    def focus_node_by_target(self, target: str) -> None:
        parts = [part for part in target.split("/") if part]
        ancestors = ["/".join(parts[:index]) for index in range(1, len(parts))]
        collapsed_ancestor = next((path for path in ancestors if path in self.collapsed_folders), None)
        if collapsed_ancestor:
            self.collapsed_folders.remove(collapsed_ancestor)
            self._render_tree()
            QTimer.singleShot(0, lambda: self.focus_node_by_target(target))
            return
        node = next((item for item in self.node_widgets if item.toolTip() == target), None)
        if node:
            self._focus_node(node)

    def _directional_node(self, node: TreeNodeWidget, action: str) -> TreeNodeWidget | None:
        proxy = self.node_proxies.get(node)
        if not proxy:
            return None
        current_x = proxy.pos().x() + node.width() / 2
        current_y = proxy.pos().y() + node.height() / 2
        candidates = []
        for candidate in self.node_widgets:
            if candidate is node:
                continue
            candidate_proxy = self.node_proxies[candidate]
            candidate_x = candidate_proxy.pos().x() + candidate.width() / 2
            candidate_y = candidate_proxy.pos().y() + candidate.height() / 2
            if action == "up" and candidate_y < current_y:
                candidates.append((abs(current_x - candidate_x), current_y - candidate_y, candidate))
            elif action == "down" and candidate_y > current_y:
                candidates.append((abs(current_x - candidate_x), candidate_y - current_y, candidate))
            elif action == "left" and candidate_x < current_x:
                candidates.append((current_x - candidate_x, abs(current_y - candidate_y), candidate))
            elif action == "right" and candidate_x > current_x:
                candidates.append((candidate_x - current_x, abs(current_y - candidate_y), candidate))
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[2]

    def _handle_node_key(self, node: TreeNodeWidget, action: str, is_folder: bool, target: str) -> None:
        if action in {"up", "down", "left", "right"}:
            destination = self._directional_node(node, action)
            if destination:
                self._focus_node(destination)
        elif action in {"home", "end"} and self.node_widgets:
            self._focus_node(self.node_widgets[0 if action == "home" else -1])
        elif action == "activate" and not is_folder:
            self.file_selected.emit(target)
        elif action == "toggle" and is_folder and target != "Root":
            self._toggle_folder(target)

    def keyPressEvent(self, event) -> None:
        actions = {
            Qt.Key.Key_Up: "up",
            Qt.Key.Key_Down: "down",
            Qt.Key.Key_Left: "left",
            Qt.Key.Key_Right: "right",
            Qt.Key.Key_Home: "home",
            Qt.Key.Key_End: "end",
        }
        action = actions.get(event.key())
        if action and self.node_widgets:
            node = self.focused_node
            if node is None:
                node = self.node_widgets[0 if action in {"down", "right", "home"} else -1]
                self._focus_node(node)
            else:
                self._handle_node_key(node, action, "📁" in node.node_icon, node.toolTip())
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_tree)

    def _toggle_folder(self, path: str) -> None:
        if path in self.collapsed_folders:
            self.collapsed_folders.remove(path)
            logger.info("Expanded folder path=%s", path)
        else:
            self.collapsed_folders.add(path)
            logger.info("Collapsed folder path=%s", path)
        self._render_tree()
        QTimer.singleShot(0, lambda: self.focus_node_by_target(path))

    def _handle_native_gesture(self, event: QNativeGestureEvent) -> bool:
        if event.gestureType() != Qt.NativeGestureType.ZoomNativeGesture:
            return False
        factor = max(0.8, min(1.2, 1.0 + event.value()))
        self.set_zoom(self.zoom * factor)
        event.accept()
        return True

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.NativeGesture and isinstance(event, QNativeGestureEvent):
            if self._handle_native_gesture(event):
                return True
        return super().event(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.NativeGesture and isinstance(event, QNativeGestureEvent):
            if self._handle_native_gesture(event):
                return True
        return super().viewportEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        device = event.device()
        is_mouse = device is not None and device.type() == QInputDevice.DeviceType.Mouse
        if is_mouse or event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            factor = 1.15 if event.angleDelta().y() > 0 else 0.87
            self.set_zoom(self.zoom * factor)
            event.accept()
            return
        super().wheelEvent(event)


@dataclass
class LoadedState:
    pull_request: PullRequest
    files: list[ChangedFile]
    threads: list[ReviewThread]


class MainWindow(QMainWindow):
    def __init__(
        self,
        initial_ref: str | None = None,
        client: GHClient | None = None,
        persistence: Persistence | None = None,
    ):
        super().__init__()
        logger.info("Creating main window initial_ref_present=%s", bool(initial_ref))
        self.client = client or GHClient()
        self.persistence = persistence or Persistence()
        self.thread_pool = QThreadPool.globalInstance()
        self.pull_request: PullRequest | None = None
        self.files: list[ChangedFile] = []
        self.threads: list[ReviewThread] = []
        self.drafts: list[DraftComment] = []
        self.viewed: set[str] = set()
        self.selected_file: ChangedFile | None = None
        self.selected_line_index: int | None = None
        self.selected_thread: ReviewThread | None = None
        self.hidden_reviewers: set[str] = set()
        self._build_ui()
        if initial_ref:
            self.ref_input.setText(initial_ref)
            self.open_reference()

    def _build_ui(self) -> None:
        self.setWindowTitle("diffy")
        self.setWindowIcon(QIcon(str(_asset_path("logo.png"))))
        self.resize(1450, 950)
        self.setMinimumSize(1050, 700)
        self._build_actions()

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 8, 10, 8)

        toolbar = QHBoxLayout()
        self.ref_input = QLineEdit()
        self.ref_input.setPlaceholderText("https://github.com/owner/repository/pull/123 or owner/repository#123")
        self.open_button = QPushButton("Open")
        self.refresh_button = QPushButton("Refresh")
        self.open_button.clicked.connect(self.open_reference)
        self.refresh_button.clicked.connect(self.refresh)
        self.ref_input.returnPressed.connect(self.open_reference)
        toolbar.addWidget(QLabel("Pull request:"))
        toolbar.addWidget(self.ref_input, 1)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.refresh_button)
        self.reviewer_filter_button = self._create_tool_button("filter.svg", "Hide review comments by reviewer")
        self.reviewer_filter_menu = QMenu(self)
        self.reviewer_filter_button.setMenu(self.reviewer_filter_menu)
        self.reviewer_filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.reviewer_filter_button.setEnabled(False)
        self.copy_pr_button = self._create_tool_button("copy.svg", "Copy pull request URL")
        self.copy_pr_button.clicked.connect(self.copy_pull_request)
        self.browser_button = self._create_tool_button("external-link.svg", "Open pull request in browser")
        self.browser_button.clicked.connect(self.open_pull_request_in_browser)
        toolbar.addWidget(self.reviewer_filter_button)
        toolbar.addWidget(self.copy_pr_button)
        toolbar.addWidget(self.browser_button)
        self.submit_review_button = QPushButton("Submit review")
        self.submit_review_button.clicked.connect(self.submit_review)
        toolbar.addWidget(self.submit_review_button)
        root_layout.addLayout(toolbar)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 0, 4, 0)
        self.title_label = QLabel("Open a pull request to begin")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600; padding: 4px;")
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setStyleSheet("color: #475569; padding: 4px;")
        header_layout.addWidget(self.title_label, 1)
        header_layout.addWidget(self.status_label)
        root_layout.addLayout(header_layout)

        self.view_stack = QStackedWidget()
        canvas_page = QWidget()
        canvas_layout = QVBoxLayout(canvas_page)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_toolbar = QHBoxLayout()
        canvas_toolbar.setContentsMargins(4, 0, 4, 4)
        canvas_toolbar.addWidget(QLabel("Canvas"))
        canvas_toolbar.addStretch()
        canvas_toolbar.addWidget(QLabel("Zoom"))
        self.canvas_zoom_out_button = QPushButton("−")
        self.canvas_zoom_out_button.setFixedWidth(30)
        self.canvas_zoom_out_button.setToolTip("Zoom out")
        self.canvas_zoom_in_button = QPushButton("+")
        self.canvas_zoom_in_button.setFixedWidth(30)
        self.canvas_zoom_in_button.setToolTip("Zoom in")
        self.canvas_fit_button = QPushButton("Fit")
        self.canvas_fit_button.setToolTip("Fit the canvas to the window")
        self.canvas_zoom_label = QLabel("Fit")
        self.canvas_zoom_label.setToolTip("Use the mouse wheel or trackpad pinch to zoom")
        self.canvas_zoom_label.setMinimumWidth(44)
        self.canvas_zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        canvas_toolbar.addWidget(self.canvas_zoom_out_button)
        canvas_toolbar.addWidget(self.canvas_zoom_label)
        canvas_toolbar.addWidget(self.canvas_zoom_in_button)
        canvas_toolbar.addWidget(self.canvas_fit_button)
        canvas_layout.addLayout(canvas_toolbar)
        self.canvas = SpatialCanvas()
        self.canvas.file_selected.connect(self.open_diff)
        self.canvas.zoom_changed.connect(self._update_canvas_zoom_label)
        self.canvas_zoom_out_button.clicked.connect(self.canvas.zoom_out)
        self.canvas_zoom_in_button.clicked.connect(self.canvas.zoom_in)
        self.canvas_fit_button.clicked.connect(self.canvas.fit_canvas)
        canvas_layout.addWidget(self.canvas)
        self.view_stack.addWidget(canvas_page)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        diff_toolbar = QHBoxLayout()
        self.back_button = QPushButton("Back to Canvas")
        self.back_button.clicked.connect(self.show_canvas)
        diff_toolbar.addWidget(self.back_button)
        diff_toolbar.addStretch()
        content_layout.addLayout(diff_toolbar)
        self.diff_viewer = DiffViewer()
        self.diff_viewer.line_selected.connect(self._line_selected)
        self.diff_viewer.comment_requested.connect(self._comment_requested)
        self.diff_viewer.thread_action.connect(self._thread_action_requested)
        self.diff_viewer.escape_pressed.connect(self.show_canvas)
        content_layout.addWidget(self.diff_viewer, 1)


        diff_page = QWidget()
        diff_page_layout = QVBoxLayout(diff_page)
        diff_page_layout.setContentsMargins(0, 0, 0, 0)
        diff_page_layout.addWidget(content)
        self.view_stack.addWidget(diff_page)
        root_layout.addWidget(self.view_stack, 1)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.activated.connect(self.show_canvas)
        self.quick_search_shortcut = QShortcut(QKeySequence("Meta+Shift+F"), self)
        self.quick_search_shortcut.activated.connect(self.show_quick_search)
        self.setCentralWidget(root)
        self._set_busy(False)

    @Slot(float, bool)
    def _update_canvas_zoom_label(self, zoom: float, fitting: bool) -> None:
        self.canvas_zoom_label.setText("Fit" if fitting else f"{round(zoom * 100)}%")

    def _create_tool_button(self, asset_name: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setIcon(QIcon(str(_asset_path(asset_name))))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(32, 28)
        button.setAutoRaise(True)
        button.setToolTip(tooltip)
        return button

    def _build_actions(self) -> None:
        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self.refresh)
        self.addAction(refresh_action)

    def _set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.reviewer_filter_button.setEnabled(not busy and bool(self.threads))
        self.copy_pr_button.setEnabled(not busy and self.pull_request is not None)
        self.browser_button.setEnabled(not busy and self.pull_request is not None)
        self.submit_review_button.setEnabled(not busy and self.pull_request is not None)
        self.status_label.setText("Loading…" if busy else self.status_label.text())

    def open_reference(self) -> None:
        logger.info("Opening pull request input_present=%s", bool(self.ref_input.text().strip()))
        try:
            ref = PullRequestRef.parse(self.ref_input.text())
        except ValueError as error:
            logger.warning("Invalid pull request input error=%s", error)
            QMessageBox.warning(self, "Invalid pull request", str(error))
            return
        self._load(ref)

    def refresh(self) -> None:
        logger.info("Refreshing current pull request present=%s", bool(self.pull_request))
        if self.pull_request:
            self._load(self.pull_request.ref)
        elif self.ref_input.text().strip():
            self.open_reference()

    def copy_pull_request(self) -> None:
        if not self.pull_request:
            return
        QApplication.clipboard().setText(self.pull_request.url)
        self.status_label.setText("Pull request URL copied")

    def open_pull_request_in_browser(self) -> None:
        if self.pull_request:
            QDesktopServices.openUrl(QUrl(self.pull_request.url))

    def submit_review(self) -> None:
        if not self.pull_request:
            return
        active_drafts = [draft for draft in self.drafts if not draft.orphaned]
        dialog = QDialog(self)
        dialog.setWindowTitle("Submit review")
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(QLabel(f"{len(active_drafts)} draft comment(s) will be submitted"))
        event = QComboBox()
        event.addItems(["COMMENT", "APPROVE", "REQUEST_CHANGES"])
        dialog_layout.addWidget(event)
        body = QTextEdit()
        body.setPlaceholderText("Review summary")
        body.setMinimumSize(420, 120)
        dialog_layout.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        summary = body.toPlainText().strip()
        if not active_drafts and not summary:
            QMessageBox.information(self, "Nothing to submit", "Add a comment or review summary first.")
            return
        self._set_busy(True)
        worker = Worker(
            self.client.submit_review,
            self.pull_request.ref,
            self.pull_request.head_sha,
            active_drafts,
            event.currentText(),
            summary,
        )
        worker.signals.result.connect(self._submit_finished)
        worker.signals.error.connect(self._load_failed)
        self.thread_pool.start(worker)

    def _populate_reviewer_menu(self) -> None:
        self.reviewer_filter_menu.clear()
        reviewers = sorted({comment.author for thread in self.threads for comment in thread.comments})
        self.reviewer_filter_button.setEnabled(bool(reviewers))
        if not reviewers:
            action = self.reviewer_filter_menu.addAction("No reviewers")
            action.setEnabled(False)
            return
        self.reviewer_filter_menu.addSection("Hide comments by reviewer")
        for reviewer in reviewers:
            action = self.reviewer_filter_menu.addAction(reviewer)
            action.setCheckable(True)
            action.setChecked(reviewer in self.hidden_reviewers)
            action.toggled.connect(lambda hidden, reviewer=reviewer: self._set_reviewer_hidden(reviewer, hidden))

    def _set_reviewer_hidden(self, reviewer: str, hidden: bool) -> None:
        if hidden:
            self.hidden_reviewers.add(reviewer)
        else:
            self.hidden_reviewers.discard(reviewer)
        self._refresh_comment_views()

    def _comment_counts(self) -> dict[str, tuple[int, int]]:
        counts: dict[str, tuple[int, int]] = {}
        for thread in self.threads:
            visible_count = sum(comment.author not in self.hidden_reviewers for comment in thread.comments)
            if visible_count:
                open_count, resolved_count = counts.get(thread.path, (0, 0))
                if thread.resolved:
                    resolved_count += visible_count
                else:
                    open_count += visible_count
                counts[thread.path] = (open_count, resolved_count)
        return counts

    def _refresh_comment_views(self) -> None:
        if not self.pull_request:
            return
        showing_canvas = self.view_stack.currentIndex() == 0
        draft_counts: dict[str, int] = {}
        for draft in self.drafts:
            draft_counts[draft.path] = draft_counts.get(draft.path, 0) + 1
        self.canvas.set_files(self.files, self.viewed, draft_counts, self._comment_counts())
        if self.selected_file:
            self.diff_viewer.show_file(self.selected_file, self.drafts, self.threads, self.hidden_reviewers)
        if showing_canvas:
            self.canvas.focus_first_node()

    def _load(self, ref: PullRequestRef) -> None:
        logger.info("Starting pull request load ref=%s", ref.key)
        self._set_busy(True)
        self.status_label.setText(f"Loading {ref.key}…")
        worker = Worker(self.client.load, ref)
        worker.signals.result.connect(self._load_finished)
        worker.signals.error.connect(self._load_failed)
        self.thread_pool.start(worker)

    @Slot(object)
    def _load_finished(self, loaded: LoadedPullRequest) -> None:
        logger.info("Pull request load completed ref=%s", loaded.pull_request.ref.key)
        parsed = parse_unified_diff(loaded.diff_text)
        self.pull_request = loaded.pull_request
        self.files = parsed.files
        self.threads = loaded.threads
        self._populate_reviewer_menu()
        self.persistence.cache_pull_request(self.pull_request, loaded.diff_text)
        self.persistence.cache_threads(self.pull_request.ref.key, self.threads)
        self.persistence.prune_cached_data()
        self.drafts = self.persistence.drafts_for(self.pull_request.ref.key)
        for draft in self.drafts:
            reattach_draft(draft, self.pull_request, self.files)
            self.persistence.save_draft(draft)
        self.viewed = self.persistence.viewed_files(self.pull_request.ref.key)
        self._render_loaded_state()
        self._set_busy(False)
        self.status_label.setText(
            f"{len(self.files)} files · {len(self.threads)} threads · {len([draft for draft in self.drafts if not draft.orphaned])} active drafts"
        )

    @Slot(str)
    def _load_failed(self, message: str) -> None:
        logger.error("Application operation failed message=%s", message)
        self._set_busy(False)
        self.status_label.setText("Load failed")
        QMessageBox.critical(self, "Unable to load pull request", message)

    def _render_loaded_state(self) -> None:
        if not self.pull_request:
            return
        self.title_label.setText(f"#{self.pull_request.ref.number} {self.pull_request.title}")
        draft_counts: dict[str, int] = {}
        for draft in self.drafts:
            draft_counts[draft.path] = draft_counts.get(draft.path, 0) + 1
        self.canvas.set_files(self.files, self.viewed, draft_counts, self._comment_counts())
        self.show_canvas()

    def open_diff(self, path: str) -> None:
        logger.info("Opening focused diff from canvas path=%s", path)
        self.select_file(path)
        self.view_stack.setCurrentIndex(1)
        self.diff_viewer.setFocus()

    def show_canvas(self) -> None:
        logger.info("Showing canvas view")
        self.view_stack.setCurrentIndex(0)
        self.canvas.focus_first_node()

    def highlight_canvas_node(self, path: str) -> None:
        self.view_stack.setCurrentIndex(0)
        self.canvas.focus_node_by_target(path)
        QTimer.singleShot(0, lambda: self.canvas.focus_node_by_target(path))

    def show_quick_search(self) -> None:
        if not self.files:
            return
        dialog = QuickSearchDialog(self.files, self)
        self.quick_search_dialog = dialog
        dialog.selected.connect(self.highlight_canvas_node)
        dialog.finished.connect(lambda result: setattr(self, "quick_search_dialog", None))
        dialog.adjustSize()
        x = (self.width() - dialog.width()) // 2
        dialog.move(self.mapToGlobal(QPoint(x, 68)))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.search_input.setFocus()

    def select_file(self, path: str) -> None:
        logger.debug("Selecting file path=%s", path)
        file = next((item for item in self.files if item.path == path), None)
        if not file:
            logger.warning("Requested file is not loaded path=%s", path)
            return
        self.selected_file = file
        self.selected_line_index = None
        if self.pull_request:
            self.viewed.add(path)
            self.persistence.mark_viewed(self.pull_request.ref.key, path)
        self.diff_viewer.show_file(file, self.drafts, self.threads, self.hidden_reviewers)

    @Slot(int)
    def _comment_requested(self, index: int) -> None:
        self.selected_line_index = index
        self.add_comment()

    @Slot(int)
    def _line_selected(self, index: int) -> None:
        self.selected_line_index = index
        if self.selected_file and 0 <= index < len(self.selected_file.lines):
            line = self.selected_file.lines[index]
            self.status_label.setText(f"Selected {self.selected_file.path}:{line.line} ({line.side.lower()})")

    def add_comment(self) -> None:
        logger.info("Add comment requested file=%s line_index=%s", self.selected_file.path if self.selected_file else None, self.selected_line_index)
        if not self.pull_request or not self.selected_file or self.selected_line_index is None:
            QMessageBox.information(self, "Select a line", "Select a changed line before adding a comment.")
            return
        line = self.selected_file.lines[self.selected_line_index]
        body, accepted = QInputDialog.getMultiLineText(
            self,
            "Draft comment",
            f"Comment on {self.selected_file.path}:{line.line}",
        )
        if not accepted or not body.strip():
            return
        draft = new_draft(self.pull_request, self.selected_file, line.line or 0, body.strip())
        self.drafts.append(draft)
        self.persistence.save_draft(draft)
        logger.info("Draft comment added id=%s path=%s line=%s", draft.id, draft.path, draft.line)
        self._render_loaded_state()
        self.select_file(self.selected_file.path)

    @Slot(object)
    def _submit_finished(self, result: object) -> None:
        for draft in self.drafts:
            if not draft.orphaned:
                self.persistence.delete_draft(draft.id)
        self.drafts = [draft for draft in self.drafts if draft.orphaned]
        self.status_label.setText("Review submitted")
        self._set_busy(False)
        self.refresh()

    @Slot(str, str)
    def _thread_action_requested(self, thread_id: str, action: str) -> None:
        thread = next((item for item in self.threads if item.thread_id == thread_id), None)
        if not thread:
            logger.warning("Requested action for unknown thread thread_id=%s", thread_id)
            return
        self.selected_thread = thread
        if action == "reply":
            self.reply_to_selected_thread()
        elif action == "toggle":
            self.toggle_selected_thread()

    def reply_to_selected_thread(self) -> None:
        logger.info("Reply requested has_thread=%s", bool(self.selected_thread))
        if not self.pull_request or not self.selected_thread or not self.selected_thread.comments:
            return
        body, accepted = QInputDialog.getMultiLineText(self, "Reply to thread", "Reply")
        if not accepted or not body.strip():
            return
        comment_id = self.selected_thread.comments[-1].database_id
        self._set_busy(True)
        worker = Worker(self.client.reply_to_comment, self.pull_request.ref, comment_id, body.strip())
        worker.signals.result.connect(lambda result: self._thread_action_finished("Reply submitted"))
        worker.signals.error.connect(self._load_failed)
        self.thread_pool.start(worker)

    def toggle_selected_thread(self) -> None:
        logger.info("Thread resolution toggle requested has_thread=%s", bool(self.selected_thread))
        if not self.selected_thread:
            return
        desired = not self.selected_thread.resolved
        self._set_busy(True)
        worker = Worker(self.client.set_thread_resolved, self.selected_thread.thread_id, desired)
        worker.signals.result.connect(lambda result: self._thread_action_finished("Thread updated"))
        worker.signals.error.connect(self._load_failed)
        self.thread_pool.start(worker)

    def _thread_action_finished(self, message: str) -> None:
        logger.info("Thread action completed message=%s", message)
        self._set_busy(False)
        self.status_label.setText(message)
        self.refresh()


def apply_light_palette(application: QApplication) -> None:
    application.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#ffffff",
        QPalette.ColorRole.WindowText: "#111827",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f8fafc",
        QPalette.ColorRole.ToolTipBase: "#ffffff",
        QPalette.ColorRole.ToolTipText: "#111827",
        QPalette.ColorRole.Text: "#111827",
        QPalette.ColorRole.Button: "#f8fafc",
        QPalette.ColorRole.ButtonText: "#111827",
        QPalette.ColorRole.BrightText: "#b91c1c",
        QPalette.ColorRole.Highlight: "#2563eb",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: "#1d4ed8",
        QPalette.ColorRole.LinkVisited: "#6d28d9",
        QPalette.ColorRole.PlaceholderText: "#6b7280",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    application.setPalette(palette)
    logger.info("Applied forced light application palette")


def create_application(arguments: list[str]) -> tuple[QApplication, MainWindow]:
    logger.info("Creating QApplication argument_count=%d", len(arguments))
    application = QApplication(arguments)
    application.setWindowIcon(QIcon(str(_asset_path("logo.png"))))
    apply_light_palette(application)
    application.setApplicationName("diffy")
    application.setOrganizationName("diffy")
    initial_ref = arguments[1] if len(arguments) > 1 else None
    window = MainWindow(initial_ref)
    window.show()
    return application, window
