from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette, QPen, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
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
    escape_pressed = Signal()

    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._anchor_clicked)
        self.setFont(QFont("SF Mono", 12))
        self.setStyleSheet("QTextBrowser { background: #ffffff; color: #111827; border: 0; }")
        self.lines = []
        self.selected_index: int | None = None

    def show_file(self, file: ChangedFile, drafts: list[DraftComment], threads: list[ReviewThread]) -> None:
        logger.debug("Rendering focused diff path=%s lines=%d drafts=%d threads=%d", file.path, len(file.lines), len(drafts), len(threads))
        draft_keys = {(draft.side, draft.line) for draft in drafts if draft.path == file.path}
        thread_keys = {(thread.side, thread.line) for thread in threads if thread.path == file.path}
        rendered = []
        for index, line in enumerate(file.lines):
            old = str(line.old_line) if line.old_line is not None else ""
            new = str(line.new_line) if line.new_line is not None else ""
            marker = "+" if line.kind == "added" else "-" if line.kind == "deleted" else " "
            prefix = f"{old:>6} {new:>6} {marker} "
            value = html.escape(line.content)
            background = "#e8f5e9" if line.kind == "added" else "#ffebee" if line.kind == "deleted" else "#ffffff"
            if (line.side, line.line) in draft_keys:
                value = "● " + value
            elif (line.side, line.line) in thread_keys:
                value = "◆ " + value
            rendered.append(
                f'<a href="line:{index}" style="text-decoration:none;color:#374151;background:{background};">'
                f"{html.escape(prefix)}{value}</a>"
            )
        if not rendered:
            rendered.append('<span style="color:#6b7280;">No textual patch is available for this file.</span>')
        self.lines = file.lines
        self.selected_index = None
        self.setHtml(
            "<style>pre { font-family: 'SF Mono'; font-size: 12pt; white-space: pre-wrap; } a { display: block; padding: 2px 6px; }</style>"
            "<pre>" + "\n".join(rendered) + "</pre>"
        )

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    @Slot(QUrl)
    def _anchor_clicked(self, url: QUrl) -> None:
        value = url.toString()
        if not value.startswith("line:"):
            return
        index = int(value.removeprefix("line:"))
        if 0 <= index < len(self.lines):
            self.selected_index = index
            self.line_selected.emit(index)


class SpatialCanvas(QGraphicsView):
    file_selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setMinimumHeight(180)
        self.setStyleSheet("QGraphicsView { border: 0; background: #f8fafc; }")
        self.setRenderHints(self.renderHints())
        self.zoom = 1.0
        self.collapsed_folders: set[str] = set()
        self.tree: dict = {"folders": {}, "files": []}
        self.viewed: set[str] = set()
        self.draft_counts: dict[str, int] = {}
        self.comment_counts: dict[str, int] = {}

    def set_files(
        self,
        files: list[ChangedFile],
        viewed: set[str],
        draft_counts: dict[str, int],
        comment_counts: dict[str, int] | None = None,
    ) -> None:
        self.comment_counts = comment_counts or {}
        logger.debug("Rendering changed-file tree files=%d viewed=%d drafts=%d comments=%d", len(files), len(viewed), sum(draft_counts.values()), sum(self.comment_counts.values()))
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

    def _draft_count(self, node: dict) -> int:
        return sum(self.draft_counts.get(file.path, 0) for file in node["files"]) + sum(
            self._draft_count(child) for child in node["folders"].values()
        )

    def _comment_count(self, node: dict) -> int:
        return sum(self.comment_counts.get(file.path, 0) for file in node["files"]) + sum(
            self._comment_count(child) for child in node["folders"].values()
        )

    def _render_tree(self) -> None:
        self.scene.clear()
        row_height = 88
        node_width = 285
        node_height = 64
        column_gap = 90
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

        def add_connector(parent_x: float, parent_y: float, child_x: float, child_y: float) -> None:
            parent_right = parent_x + node_width
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
            changed_count: int,
            comment_count: int,
            x: float,
            y: float,
            style: str,
            tooltip: str,
            callback=None,
        ) -> None:
            node = TreeNodeWidget(icon, name, status, changed_count, comment_count, node_width, node_height, style, tooltip)
            if callback:
                node.clicked.connect(callback)
            proxy = QGraphicsProxyWidget()
            proxy.setWidget(node)
            proxy.setPos(x, y)
            self.scene.addItem(proxy)

        folder_style = "#treeNode { border: 1px solid #fdba74; border-radius: 9px; background: #fff7ed; } #treeNode:hover { background: #ffedd5; }"
        file_style = "#treeNode { border: 1px solid #cbd5e1; border-radius: 9px; background: #ffffff; } #treeNode:hover { background: #eff6ff; }"

        def place_entry(entry: tuple[str, object], depth: int, top_row: int, parent_position: tuple[float, float] | None) -> tuple[float, float, int]:
            nonlocal max_depth
            kind, value = entry
            max_depth = max(max_depth, depth)
            span = rows_for_entry(entry)
            x = left_margin + depth * (node_width + column_gap)
            y = top_margin + (top_row + (span - 1) / 2) * row_height
            if parent_position is not None:
                add_connector(parent_position[0], parent_position[1], x, y)
            if kind == "folder":
                folder = value
                folder_path = folder["path"]
                collapsed = folder_path in self.collapsed_folders
                marker = "▸" if collapsed else "⌄"
                comment_count = self._comment_count(folder)
                add_node(
                    f"{marker}  📁",
                    folder_path.rsplit("/", 1)[-1],
                    None,
                    self._file_count(folder),
                    comment_count,
                    x,
                    y,
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
                add_node(
                    "📄",
                    filename,
                    status,
                    file.change_count,
                    self.comment_counts.get(file.path, 0),
                    x,
                    y,
                    file_style,
                    file.path,
                    lambda path=file.path: self.file_selected.emit(path),
                )
            children = [] if kind == "file" or (kind == "folder" and value["path"] in self.collapsed_folders) else child_entries(value)
            cursor = top_row
            for child in children:
                child_span = rows_for_entry(child)
                place_entry(child, depth + 1, cursor, (x, y))
                cursor += child_span
            return x, y, span

        root_entry = ("folder", {"path": "Root", "folders": self.tree["folders"], "files": self.tree["files"]})
        root_span = rows_for_entry(root_entry)
        def render_without_root() -> None:
            root_x = left_margin
            root_y = top_margin + (root_span - 1) / 2 * row_height
            add_node(
                "⌄  📁",
                "Root",
                None,
                self._file_count(self.tree),
                self._comment_count(self.tree),
                root_x,
                root_y,
                folder_style,
                "Root",
            )
            cursor = 0
            for child in child_entries(self.tree):
                child_span = rows_for_entry(child)
                place_entry(child, 1, cursor, (root_x, root_y))
                cursor += child_span
        render_without_root()
        self.scene.setSceneRect(0, 0, left_margin + (max_depth + 1) * (node_width + column_gap), top_margin * 2 + max(root_span, 1) * row_height)

    def _toggle_folder(self, path: str) -> None:
        if path in self.collapsed_folders:
            self.collapsed_folders.remove(path)
            logger.info("Expanded folder path=%s", path)
        else:
            self.collapsed_folders.add(path)
            logger.info("Collapsed folder path=%s", path)
        self._render_tree()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            factor = 1.15 if event.angleDelta().y() > 0 else 0.87
            self.zoom = max(0.5, min(2.5, self.zoom * factor))
            self.resetTransform()
            self.scale(self.zoom, self.zoom)
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
        self._build_ui()
        if initial_ref:
            self.ref_input.setText(initial_ref)
            self.open_reference()

    def _build_ui(self) -> None:
        self.setWindowTitle("diffy")
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
        root_layout.addLayout(toolbar)

        self.title_label = QLabel("Open a pull request to begin")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600; padding: 4px;")
        self.status_label = QLabel("Ready")
        root_layout.addWidget(self.title_label)
        root_layout.addWidget(self.status_label)

        self.view_stack = QStackedWidget()
        canvas_page = QWidget()
        canvas_layout = QVBoxLayout(canvas_page)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = SpatialCanvas()
        self.canvas.file_selected.connect(self.open_diff)
        canvas_layout.addWidget(self.canvas)
        self.view_stack.addWidget(canvas_page)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 8, 0)
        file_group = QGroupBox("Files")
        file_layout = QVBoxLayout(file_group)
        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._file_item_changed)
        file_layout.addWidget(self.file_list)
        sidebar_layout.addWidget(file_group, 3)
        thread_group = QGroupBox("Threads")
        thread_layout = QVBoxLayout(thread_group)
        self.thread_list = QListWidget()
        self.thread_list.currentItemChanged.connect(self._thread_item_changed)
        thread_layout.addWidget(self.thread_list)
        thread_actions = QHBoxLayout()
        self.reply_button = QPushButton("Reply")
        self.resolve_button = QPushButton("Resolve")
        self.reply_button.clicked.connect(self.reply_to_selected_thread)
        self.resolve_button.clicked.connect(self.toggle_selected_thread)
        thread_actions.addWidget(self.reply_button)
        thread_actions.addWidget(self.resolve_button)
        thread_layout.addLayout(thread_actions)
        sidebar_layout.addWidget(thread_group, 2)
        sidebar.setMinimumWidth(270)
        main_splitter.addWidget(sidebar)

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
        self.diff_viewer.escape_pressed.connect(self.show_canvas)
        content_layout.addWidget(self.diff_viewer, 1)

        review_group = QGroupBox("Review")
        review_layout = QVBoxLayout(review_group)
        draft_row = QHBoxLayout()
        self.add_comment_button = QPushButton("Add comment on selected line")
        self.delete_draft_button = QPushButton("Delete selected draft")
        self.add_comment_button.clicked.connect(self.add_comment)
        self.delete_draft_button.clicked.connect(self.delete_selected_draft)
        draft_row.addWidget(self.add_comment_button)
        draft_row.addWidget(self.delete_draft_button)
        self.draft_list = QListWidget()
        self.draft_list.setMaximumHeight(100)
        draft_row.addWidget(self.draft_list, 1)
        review_layout.addLayout(draft_row)
        form_row = QHBoxLayout()
        self.review_event = QComboBox()
        self.review_event.addItems(["COMMENT", "APPROVE", "REQUEST_CHANGES"])
        self.review_body = QTextEdit()
        self.review_body.setPlaceholderText("Review summary")
        self.review_body.setMaximumHeight(68)
        self.submit_button = QPushButton("Submit review")
        self.submit_button.clicked.connect(self.submit_review)
        form_row.addWidget(self.review_event)
        form_row.addWidget(self.review_body, 1)
        form_row.addWidget(self.submit_button)
        review_layout.addLayout(form_row)
        content_layout.addWidget(review_group)
        main_splitter.addWidget(content)
        main_splitter.setSizes([290, 1100])
        diff_page = QWidget()
        diff_page_layout = QVBoxLayout(diff_page)
        diff_page_layout.setContentsMargins(0, 0, 0, 0)
        diff_page_layout.addWidget(main_splitter)
        self.view_stack.addWidget(diff_page)
        root_layout.addWidget(self.view_stack, 1)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.activated.connect(self.show_canvas)
        self.setCentralWidget(root)
        self._set_busy(False)

    def _build_actions(self) -> None:
        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self.refresh)
        self.addAction(refresh_action)

    def _set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.submit_button.setEnabled(not busy)
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
        self.file_list.clear()
        draft_counts: dict[str, int] = {}
        for draft in self.drafts:
            draft_counts[draft.path] = draft_counts.get(draft.path, 0) + 1
        for file in self.files:
            item = QListWidgetItem(f"{file.path}   +{file.additions} -{file.deletions}")
            item.setData(Qt.ItemDataRole.UserRole, file.path)
            if file.path in self.viewed:
                item.setForeground(Qt.GlobalColor.gray)
            if draft_counts.get(file.path):
                item.setToolTip(f"{draft_counts[file.path]} draft comment(s)")
            self.file_list.addItem(item)
        self.thread_list.clear()
        for thread in self.threads:
            state = "resolved" if thread.resolved else "open"
            line = thread.line or thread.start_line or 0
            item = QListWidgetItem(f"{thread.path}:{line} · {state} · {len(thread.comments)}")
            item.setData(Qt.ItemDataRole.UserRole, thread.thread_id)
            self.thread_list.addItem(item)
        self.draft_list.clear()
        for draft in self.drafts:
            state = "orphaned" if draft.orphaned else f"{draft.side.lower()}:{draft.line}"
            item = QListWidgetItem(f"{draft.path}:{state} · {draft.body[:70]}")
            item.setData(Qt.ItemDataRole.UserRole, draft.id)
            self.draft_list.addItem(item)
        comment_counts: dict[str, int] = {}
        for thread in self.threads:
            comment_counts[thread.path] = comment_counts.get(thread.path, 0) + len(thread.comments)
        self.canvas.set_files(self.files, self.viewed, draft_counts, comment_counts)
        if self.files:
            self.file_list.setCurrentRow(0)
        self.show_canvas()

    def open_diff(self, path: str) -> None:
        logger.info("Opening focused diff from canvas path=%s", path)
        self.select_file(path)
        self.view_stack.setCurrentIndex(1)
        self.diff_viewer.setFocus()

    def show_canvas(self) -> None:
        logger.info("Showing canvas view")
        self.view_stack.setCurrentIndex(0)
        self.canvas.setFocus()

    @Slot(QListWidgetItem, QListWidgetItem)
    def _file_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current:
            self.select_file(current.data(Qt.ItemDataRole.UserRole))

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
        self.diff_viewer.show_file(file, self.drafts, self.threads)
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                self.file_list.setCurrentItem(item)
                break

    @Slot(int)
    def _line_selected(self, index: int) -> None:
        self.selected_line_index = index
        if self.selected_file and 0 <= index < len(self.selected_file.lines):
            line = self.selected_file.lines[index]
            self.status_label.setText(f"Selected {self.selected_file.path}:{line.line} ({line.side.lower()})")

    @Slot(QListWidgetItem, QListWidgetItem)
    def _thread_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self.selected_thread = None
        if current:
            thread_id = current.data(Qt.ItemDataRole.UserRole)
            self.selected_thread = next((thread for thread in self.threads if thread.thread_id == thread_id), None)
            if self.selected_thread:
                self.select_file(self.selected_thread.path)

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

    def delete_selected_draft(self) -> None:
        logger.info("Delete draft requested")
        item = self.draft_list.currentItem()
        if not item:
            return
        draft_id = item.data(Qt.ItemDataRole.UserRole)
        self.persistence.delete_draft(draft_id)
        self.drafts = [draft for draft in self.drafts if draft.id != draft_id]
        self._render_loaded_state()

    def submit_review(self) -> None:
        logger.info("Submit review requested has_pull_request=%s draft_count=%d", bool(self.pull_request), len(self.drafts))
        if not self.pull_request:
            return
        active = [draft for draft in self.drafts if not draft.orphaned]
        if any(draft.orphaned for draft in self.drafts):
            QMessageBox.information(self, "Orphaned drafts", "Orphaned drafts are excluded from submission.")
        event = self.review_event.currentText()
        body = self.review_body.toPlainText().strip()
        if not active and not body:
            QMessageBox.information(self, "Nothing to submit", "Add a comment or review summary first.")
            return
        self._set_busy(True)
        worker = Worker(self.client.submit_review, self.pull_request.ref, self.pull_request.head_sha, active, event, body)
        worker.signals.result.connect(self._submit_finished)
        worker.signals.error.connect(self._load_failed)
        self.thread_pool.start(worker)

    @Slot(object)
    def _submit_finished(self, result: object) -> None:
        logger.info("Review submission completed")
        for draft in list(self.drafts):
            if not draft.orphaned:
                self.persistence.delete_draft(draft.id)
        self.drafts = [draft for draft in self.drafts if draft.orphaned]
        self.review_body.clear()
        self._set_busy(False)
        self.status_label.setText("Review submitted")
        self.refresh()

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
    apply_light_palette(application)
    application.setApplicationName("diffy")
    application.setOrganizationName("diffy")
    initial_ref = arguments[1] if len(arguments) > 1 else None
    window = MainWindow(initial_ref)
    window.show()
    return application, window
