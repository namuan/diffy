from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class TreeNodeWidget(QWidget):
    clicked = Signal()
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
    ):
        super().__init__()
        self.setObjectName("treeNode")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.node_icon = icon
        self.node_name = name
        self.node_open_comment_count = open_comment_count
        self.node_resolved_comment_count = resolved_comment_count
        self.node_comment_count = open_comment_count + resolved_comment_count
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
        name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name_label.setMinimumWidth(0)
        name_label.setStyleSheet("background: transparent; border: 0; color: #111827;")
        layout.addWidget(name_label, 1)

        additions_label = QLabel(f"+{additions}")
        additions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        additions_label.setStyleSheet("background: #dcfce7; color: #15803d; border: 0; border-radius: 12px; padding: 4px 7px; font-weight: 600;")
        layout.addWidget(additions_label)

        deletions_label = QLabel(f"-{deletions}")
        deletions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        deletions_label.setStyleSheet("background: #fee2e2; color: #b91c1c; border: 0; border-radius: 12px; padding: 4px 7px; font-weight: 600;")
        layout.addWidget(deletions_label)

        if open_comment_count:
            open_comment_label = QLabel(f"● {open_comment_count}")
            open_comment_label.setToolTip("Open comments")
            open_comment_label.setStyleSheet("background: transparent; color: #c026d3; border: 0; font-weight: 600;")
            layout.addWidget(open_comment_label)
        if resolved_comment_count:
            resolved_comment_label = QLabel(f"● {resolved_comment_count}")
            resolved_comment_label.setToolTip("Resolved comments")
            resolved_comment_label.setStyleSheet("background: transparent; color: #94a3b8; border: 0; font-weight: 600;")
            layout.addWidget(resolved_comment_label)

        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.MetaModifier:
            if event.key() == Qt.Key.Key_Right:
                self.key_action.emit("expand_level")
                event.accept()
                return
            if event.key() == Qt.Key.Key_Left:
                self.key_action.emit("collapse_level")
                event.accept()
                return
        actions = {
            Qt.Key.Key_Up: "up",
            Qt.Key.Key_Down: "down",
            Qt.Key.Key_Left: "left",
            Qt.Key.Key_Right: "right",
            Qt.Key.Key_Home: "home",
            Qt.Key.Key_End: "end",
            Qt.Key.Key_Return: "activate",
            Qt.Key.Key_Enter: "activate",
            Qt.Key.Key_Space: "toggle",
        }
        action = actions.get(event.key())
        if action:
            self.key_action.emit(action)
            event.accept()
            return
        super().keyPressEvent(event)
