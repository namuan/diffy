from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from tree_sitter_language_pack import get_parser

from diffy.core.logging import get_logger
from diffy.core.models import ASTChange, ChangedFile


logger = get_logger("ast_parser")


_LANGUAGE_BY_EXTENSION = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".dart": "dart",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".kt": "kotlin",
    ".lua": "lua",
    ".md": "markdown",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sh": "bash",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zig": "zig",
}

_REVIEW_UNIT = re.compile(
    r"declaration|definition|function|method|class|struct|interface|enum|trait|impl|module|namespace|import|statement|assignment|property|field|call(?:_expression)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _SyntaxUnit:
    node_type: str
    name: str
    start_line: int
    end_line: int
    text: bytes

    @property
    def identity(self) -> tuple[str, str]:
        return self.node_type, self.name


def language_for_path(path: str) -> str | None:
    filename = PurePosixPath(path).name.lower()
    if filename in {"dockerfile", "makefile", "gnumakefile"}:
        return "dockerfile" if filename == "dockerfile" else "make"
    return _LANGUAGE_BY_EXTENSION.get(PurePosixPath(filename).suffix)


def _line_hits(start_line: int, end_line: int, changed_lines: set[int]) -> bool:
    for line in changed_lines:
        if start_line <= line <= end_line:
            return True
    return False


def _node_name(node, source: bytes) -> str:
    named = node.child_by_field_name("name")
    if named is not None:
        return source[named.start_byte:named.end_byte].decode("utf-8", errors="replace").strip()
    text = source[node.start_byte:min(node.end_byte, node.start_byte + 180)].decode("utf-8", errors="replace")
    return " ".join(text.split())[:100]


def _collect_units(root, source: bytes, changed_lines: set[int]) -> list[_SyntaxUnit]:
    units: list[_SyntaxUnit] = []
    pending = [(root, 0)]
    while pending:
        node, depth = pending.pop()
        if depth and node.is_named and _REVIEW_UNIT.search(node.type) and _line_hits(node.start_point.row + 1, node.end_point.row + 1, changed_lines):
            units.append(
                _SyntaxUnit(
                    node.type,
                    _node_name(node, source),
                    node.start_point.row + 1,
                    node.end_point.row + 1,
                    source[node.start_byte:node.end_byte],
                )
            )
        pending.extend((child, depth + 1) for child in reversed(node.children))
    return units


def _group(units: list[_SyntaxUnit]) -> dict[tuple[str, str], list[_SyntaxUnit]]:
    grouped: dict[tuple[str, str], list[_SyntaxUnit]] = defaultdict(list)
    for unit in units:
        grouped[unit.identity].append(unit)
    return grouped


def parse_file_changes(file: ChangedFile, old_source: str | None, new_source: str | None) -> None:
    language = language_for_path(file.path)
    file.language = language
    file.ast_changes = []
    if language is None:
        file.ast_status = "unsupported"
        return
    if (file.status != "added" and old_source is None) or (file.status != "deleted" and new_source is None):
        file.ast_status = "unavailable"
        return
    try:
        parser = get_parser(language)
        old_bytes = old_source.encode("utf-8") if old_source is not None else b""
        new_bytes = new_source.encode("utf-8") if new_source is not None else b""
        old_changed_lines = {line.old_line for line in file.lines if line.kind == "deleted" and line.old_line is not None}
        new_changed_lines = {line.new_line for line in file.lines if line.kind == "added" and line.new_line is not None}
        context_lines = [line for line in file.lines if line.kind == "context"]
        for line in file.lines:
            if line.kind == "added" and line.new_line is not None:
                new_changed_lines.update(
                    context.new_line
                    for context in context_lines
                    if context.new_line is not None and abs(context.new_line - line.new_line) <= 1
                )
                old_changed_lines.update(
                    context.old_line
                    for context in context_lines
                    if context.old_line is not None and context.new_line is not None and abs(context.new_line - line.new_line) <= 1
                )
            elif line.kind == "deleted" and line.old_line is not None:
                old_changed_lines.update(
                    context.old_line
                    for context in context_lines
                    if context.old_line is not None and abs(context.old_line - line.old_line) <= 1
                )
                new_changed_lines.update(
                    context.new_line
                    for context in context_lines
                    if context.new_line is not None and context.old_line is not None and abs(context.old_line - line.old_line) <= 1
                )
        if file.status == "added":
            new_changed_lines.update(range(1, new_bytes.count(b"\n") + 2))
        if file.status == "deleted":
            old_changed_lines.update(range(1, old_bytes.count(b"\n") + 2))
        old_tree = parser.parse(old_bytes) if old_source is not None else None
        new_tree = parser.parse(new_bytes) if new_source is not None else None
        old_units = _collect_units(old_tree.root_node, old_bytes, old_changed_lines) if old_tree is not None else []
        new_units = _collect_units(new_tree.root_node, new_bytes, new_changed_lines) if new_tree is not None else []
        old_grouped = _group(old_units)
        new_grouped = _group(new_units)
        changes: list[ASTChange] = []
        for identity in sorted(old_grouped.keys() | new_grouped.keys()):
            old_items = old_grouped.get(identity, [])
            new_items = new_grouped.get(identity, [])
            paired = min(len(old_items), len(new_items))
            for index in range(paired):
                before = old_items[index]
                after = new_items[index]
                if before.text != after.text:
                    changes.append(ASTChange(before.node_type, before.name, "modified", before.start_line, before.end_line, after.start_line, after.end_line))
            for before in old_items[paired:]:
                changes.append(ASTChange(before.node_type, before.name, "deleted", before.start_line, before.end_line, None, None))
            for after in new_items[paired:]:
                changes.append(ASTChange(after.node_type, after.name, "added", None, None, after.start_line, after.end_line))
        file.ast_changes = sorted(changes, key=lambda item: (item.new_start_line or item.old_start_line or 0, item.name))
        file.ast_status = "partial" if (old_tree is not None and old_tree.root_node.has_error) or (new_tree is not None and new_tree.root_node.has_error) else "parsed"
    except Exception as error:
        file.ast_status = "error"
        logger.warning("AST parsing failed path=%s language=%s error=%s", file.path, language, error)
    logger.debug("AST file parsed path=%s language=%s status=%s changes=%d", file.path, language, file.ast_status, len(file.ast_changes))
