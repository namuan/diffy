from __future__ import annotations

import re
from dataclasses import dataclass

from diffy.core.logging import get_logger
from diffy.core.models import ChangedFile, DiffLine


logger = get_logger("diff_parser")


_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class ParsedDiff:
    files: list[ChangedFile]


def _clean_path(value: str) -> str:
    path = value.strip()
    if path == "/dev/null":
        return path
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def parse_unified_diff(text: str) -> ParsedDiff:
    logger.debug("Parsing unified diff bytes=%d", len(text.encode("utf-8")))
    lines = text.splitlines()
    files: list[ChangedFile] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("diff --git "):
            index += 1
            continue
        header = lines[index]
        match = re.match(r"diff --git a/(.+) b/(.+)$", header)
        old_path = match.group(1) if match else ""
        new_path = match.group(2) if match else old_path
        section_start = index
        index += 1
        old_header = None
        new_header = None
        while index < len(lines) and not lines[index].startswith("diff --git "):
            current = lines[index]
            if current.startswith("--- "):
                old_header = current[4:]
            elif current.startswith("+++ "):
                new_header = current[4:]
            if current.startswith("@@ "):
                break
            index += 1
        if old_header:
            old_path = _clean_path(old_header.split("\t", 1)[0])
        if new_header:
            new_path = _clean_path(new_header.split("\t", 1)[0])
        path = new_path if new_path != "/dev/null" else old_path
        status = "modified"
        if old_path == "/dev/null":
            status = "added"
        elif new_path == "/dev/null":
            status = "deleted"
        elif old_path != new_path:
            status = "renamed"
        section_end = index
        while section_end < len(lines) and not lines[section_end].startswith("diff --git "):
            section_end += 1
        section = lines[section_start:section_end]
        file_lines: list[DiffLine] = []
        additions = 0
        deletions = 0
        old_line = 0
        new_line = 0
        cursor = index
        while cursor < section_end:
            hunk = _HUNK_RE.match(lines[cursor])
            if not hunk:
                cursor += 1
                continue
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(3))
            cursor += 1
            while cursor < section_end and not lines[cursor].startswith("@@ ") and not lines[cursor].startswith("diff --git "):
                value = lines[cursor]
                if value.startswith("\\"):
                    cursor += 1
                    continue
                marker = value[:1]
                content = value[1:] if marker in {"+", "-", " "} else value
                if marker == "+":
                    file_lines.append(DiffLine(content, "added", None, new_line, "RIGHT"))
                    additions += 1
                    new_line += 1
                elif marker == "-":
                    file_lines.append(DiffLine(content, "deleted", old_line, None, "LEFT"))
                    deletions += 1
                    old_line += 1
                else:
                    file_lines.append(DiffLine(content, "context", old_line, new_line, "RIGHT"))
                    old_line += 1
                    new_line += 1
                cursor += 1
        files.append(ChangedFile(path, status, additions, deletions, "\n".join(section), file_lines))
        logger.debug("Parsed file path=%s status=%s additions=%d deletions=%d lines=%d", path, status, additions, deletions, len(file_lines))
        index = section_end
    logger.info("Parsed unified diff files=%d", len(files))
    return ParsedDiff(files)
