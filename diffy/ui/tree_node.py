from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class TreeNodeWidget(QWidget):
    clicked = Signal()
    focused = Signal()
    key_action = Signal(str)

    def __init__(
        self,
        icon: str,
        name: str,
        status: str | None,
        additions: int,
        deletions: int,
        open_comment_count: int,
        resolved_comment_count: int,
        width: int,
        height: int,
        style: str,
        tooltip: str,
        shortcuts: dict[str, QKeySequence] | None = None,
    ):
        super().__init__()
        self.setObjectName("treeNode")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.node_icon = icon
        self.node_name = name
        self.node_open_comment_count = open_comment_count
        self.node_resolved_comment_count = resolved_comment_count
        self.node_comment_count = open_comment_count + resolved_comment_count
        self.shortcuts = shortcuts or {}
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(width, height)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(style)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 17px; background: transparent; border: 0;")
        layout.addWidget(icon_label)

        if status:
            status_label = QLabel(status)
            status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            status_label.setFixedSize(22, 22)
            status_label.setStyleSheet("background: #f1f5f9; color: #475569; border: 0; border-radius: 5px; font-weight: 600;")
            layout.addWidget(status_label)

        name_label = QLabel(name)
        name_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        name_label.setMinimumWidth(0)
        name_label.setStyleSheet("background: transparent; border: 0; color: #111827;")
        layout.addWidget(name_label)

        if additions:
            additions_label = QLabel(f"+{additions}")
            additions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            additions_label.setStyleSheet("background: #dcfce7; color: #15803d; border: 0; border-radius: 9px; padding: 2px 6px; font-size: 13px; font-weight: 600;")
            layout.addWidget(additions_label)

        if deletions:
            deletions_label = QLabel(f"-{deletions}")
            deletions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            deletions_label.setStyleSheet("background: #fee2e2; color: #b91c1c; border: 0; border-radius: 9px; padding: 2px 6px; font-size: 13px; font-weight: 600;")
            layout.addWidget(deletions_label)

        if open_comment_count:
            open_comment_label = QLabel(f"● {open_comment_count}")
            open_comment_label.setToolTip("Open comments")
            open_comment_label.setStyleSheet("background: transparent; color: #c026d3; border: 0; font-size: 13px; font-weight: 600;")
            layout.addWidget(open_comment_label)
        if resolved_comment_count:
            resolved_comment_label = QLabel(f"● {resolved_comment_count}")
            resolved_comment_label.setToolTip("Resolved comments")
            resolved_comment_label.setStyleSheet("background: transparent; color: #94a3b8; border: 0; font-size: 13px; font-weight: 600;")
            layout.addWidget(resolved_comment_label)

        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focused.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def _shortcut_action(self, event) -> str | None:
        event_sequence = QKeySequence(event.key() | event.modifiers().value)
        actions = {
            "move_up": "up",
            "move_down": "down",
            "move_left": "left",
            "move_right": "right",
            "go_home": "home",
            "go_end": "end",
            "activate_node": "activate",
            "toggle_folder": "toggle",
            "next_node": "next",
            "previous_node": "previous",
            "expand_level": "expand_level",
            "collapse_level": "collapse_level",
        }
        for shortcut_id, shortcut in self.shortcuts.items():
            if shortcut.matches(event_sequence) == QKeySequence.SequenceMatch.ExactMatch:
                return actions.get(shortcut_id)
        return None

    def focusNextPrevChild(self, next: bool) -> bool:
        action = "next_node" if next else "previous_node"
        if action in self.shortcuts and not self.shortcuts[action].isEmpty():
            self.key_action.emit("next" if next else "previous")
            return True
        return super().focusNextPrevChild(next)

    def keyPressEvent(self, event) -> None:
        action = self._shortcut_action(event)
        if action:
            self.key_action.emit(action)
            event.accept()
            return
        super().keyPressEvent(event)
