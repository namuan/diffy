from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class TreeNodeWidget(QWidget):
    clicked = Signal()

    def __init__(
        self,
        icon: str,
        name: str,
        status: str | None,
        changed_count: int,
        comment_count: int,
        width: int,
        height: int,
        style: str,
        tooltip: str,
    ):
        super().__init__()
        self.setObjectName("treeNode")
        self.node_icon = icon
        self.node_name = name
        self.node_comment_count = comment_count
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

        count_label = QLabel(str(changed_count))
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count_label.setMinimumWidth(24)
        count_label.setStyleSheet("background: #f1f5f9; color: #475569; border: 0; border-radius: 9px; padding: 2px 6px; font-weight: 600;")
        layout.addWidget(count_label)

        comment_label = QLabel(f"● {comment_count}" if comment_count else "")
        comment_label.setVisible(comment_count > 0)
        comment_label.setStyleSheet("background: transparent; color: #c026d3; border: 0; font-weight: 600;")
        layout.addWidget(comment_label)

        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)
