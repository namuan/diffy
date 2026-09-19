from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
import re
from typing import Any


@dataclass(frozen=True)
class PullRequestRef:
    host: str
    owner: str
    repository: str
    number: int

    @property
    def key(self) -> str:
        return f"{self.host}/{self.owner}/{self.repository}#{self.number}"

    @property
    def path(self) -> str:
        return f"{self.owner}/{self.repository}"

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repository}/pull/{self.number}"

    @classmethod
    def parse(cls, value: str) -> PullRequestRef:
        text = value.strip()
        url = urlparse(text if "://" in text else "https://github.com/" + text)
        parts = [part for part in url.path.split("/") if part]
        if len(parts) >= 4 and parts[2] in {"pull", "pulls"} and parts[3].isdigit():
            return cls(url.netloc or "github.com", parts[0], parts[1], int(parts[3]))
        match = re.fullmatch(r"([^/\s]+)/([^#\s]+)#(\d+)", text)
        if match:
            return cls("github.com", match.group(1), match.group(2), int(match.group(3)))
        raise ValueError("Enter a pull request URL or owner/repository#number")


@dataclass
class PullRequest:
    ref: PullRequestRef
    title: str
    body: str
    author: str
    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str
    url: str
    state: str


@dataclass
class DiffLine:
    content: str
    kind: str
    old_line: int | None
    new_line: int | None
    side: str

    @property
    def line(self) -> int | None:
        return self.old_line if self.side == "LEFT" else self.new_line

    @property
    def identity(self) -> str:
        return f"{self.side}:{self.line}:{self.content}"


@dataclass
class ChangedFile:
    path: str
    status: str
    additions: int
    deletions: int
    patch: str
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def change_count(self) -> int:
        return self.additions + self.deletions


@dataclass
class ReviewComment:
    database_id: int
    body: str
    author: str
    created_at: str
    url: str


@dataclass
class ReviewThread:
    thread_id: str
    path: str
    line: int | None
    start_line: int | None
    side: str | None
    start_side: str | None
    resolved: bool
    comments: list[ReviewComment] = field(default_factory=list)


@dataclass
class DraftComment:
    id: str
    ref_key: str
    path: str
    head_sha: str
    side: str
    line: int
    start_line: int | None
    body: str
    context: str
    orphaned: bool = False
    candidates: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> DraftComment:
        return cls(
            id=record["id"],
            ref_key=record["ref_key"],
            path=record["path"],
            head_sha=record["head_sha"],
            side=record["side"],
            line=int(record["line"]),
            start_line=int(record["start_line"]) if record.get("start_line") is not None else None,
            body=record["body"],
            context=record["context"],
            orphaned=bool(record.get("orphaned", 0)),
            candidates=list(record.get("candidates", [])),
            created_at=record.get("created_at") or datetime.now(timezone.utc).isoformat(),
            updated_at=record.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        )
