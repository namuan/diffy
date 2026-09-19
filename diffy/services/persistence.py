from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from diffy.core.logging import get_logger
from diffy.core.models import DraftComment, PullRequest, ReviewThread


logger = get_logger("persistence")


class Persistence:
    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path or Path.home() / "Library" / "Application Support" / "diffy" / "diffy.sqlite3"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Initializing persistence database path=%s", self.database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        logger.debug("Ensuring persistence schema exists")
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    ref_key TEXT NOT NULL,
                    path TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    side TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    start_line INTEGER,
                    body TEXT NOT NULL,
                    context TEXT NOT NULL,
                    orphaned INTEGER NOT NULL DEFAULT 0,
                    candidates TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS drafts_ref_key ON drafts(ref_key);
                CREATE TABLE IF NOT EXISTS viewed_files (
                    ref_key TEXT NOT NULL,
                    path TEXT NOT NULL,
                    viewed_at TEXT NOT NULL,
                    PRIMARY KEY (ref_key, path)
                );
                CREATE TABLE IF NOT EXISTS pull_request_cache (
                    ref_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    diff_text TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thread_cache (
                    ref_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def save_draft(self, draft: DraftComment) -> None:
        logger.info("Saving draft id=%s ref=%s path=%s side=%s line=%s orphaned=%s", draft.id, draft.ref_key, draft.path, draft.side, draft.line, draft.orphaned)
        record = draft.to_record()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO drafts (id, ref_key, path, head_sha, side, line, start_line, body, context, orphaned, candidates, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    path=excluded.path, head_sha=excluded.head_sha, side=excluded.side,
                    line=excluded.line, start_line=excluded.start_line, body=excluded.body,
                    context=excluded.context, orphaned=excluded.orphaned, candidates=excluded.candidates,
                    updated_at=excluded.updated_at
                """,
                (
                    record["id"], record["ref_key"], record["path"], record["head_sha"], record["side"],
                    record["line"], record["start_line"], record["body"], record["context"],
                    int(record["orphaned"]), json.dumps(record["candidates"]), record["created_at"], record["updated_at"],
                ),
            )

    def drafts_for(self, ref_key: str) -> list[DraftComment]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM drafts WHERE ref_key = ? ORDER BY created_at", (ref_key,)).fetchall()
        drafts = [
            DraftComment.from_record({**dict(row), "candidates": json.loads(row["candidates"])})
            for row in rows
        ]
        logger.debug("Loaded drafts ref=%s count=%d", ref_key, len(drafts))
        return drafts

    def mark_viewed(self, ref_key: str, path: str) -> None:
        logger.debug("Marking file viewed ref=%s path=%s", ref_key, path)
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO viewed_files (ref_key, path, viewed_at) VALUES (?, ?, ?)
                ON CONFLICT(ref_key, path) DO UPDATE SET viewed_at=excluded.viewed_at
                """,
                (ref_key, path, timestamp),
            )

    def viewed_files(self, ref_key: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT path FROM viewed_files WHERE ref_key = ?", (ref_key,)).fetchall()
        viewed = {row["path"] for row in rows}
        logger.debug("Loaded viewed files ref=%s count=%d", ref_key, len(viewed))
        return viewed

    def cache_pull_request(self, pull_request: PullRequest, diff_text: str) -> None:
        logger.debug("Caching pull request ref=%s diff_bytes=%d", pull_request.ref.key, len(diff_text.encode("utf-8")))
        payload = {
            "ref": {
                "host": pull_request.ref.host,
                "owner": pull_request.ref.owner,
                "repository": pull_request.ref.repository,
                "number": pull_request.ref.number,
            },
            "title": pull_request.title,
            "body": pull_request.body,
            "author": pull_request.author,
            "base_ref": pull_request.base_ref,
            "head_ref": pull_request.head_ref,
            "base_sha": pull_request.base_sha,
            "head_sha": pull_request.head_sha,
            "url": pull_request.url,
            "state": pull_request.state,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pull_request_cache (ref_key, payload, diff_text, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(ref_key) DO UPDATE SET payload=excluded.payload, diff_text=excluded.diff_text, updated_at=excluded.updated_at
                """,
                (pull_request.ref.key, json.dumps(payload), diff_text, datetime.now(timezone.utc).isoformat()),
            )

    def cache_threads(self, ref_key: str, threads: Iterable[ReviewThread]) -> None:
        threads = list(threads)
        logger.debug("Caching review threads ref=%s count=%d", ref_key, len(threads))
        payload = []
        for thread in threads:
            payload.append({
                "thread_id": thread.thread_id,
                "path": thread.path,
                "line": thread.line,
                "start_line": thread.start_line,
                "side": thread.side,
                "start_side": thread.start_side,
                "resolved": thread.resolved,
                "comments": [comment.__dict__ for comment in thread.comments],
            })
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thread_cache (ref_key, payload, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(ref_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (ref_key, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
            )

    def prune_cached_data(self, days: int = 30) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            pull_requests = connection.execute("DELETE FROM pull_request_cache WHERE updated_at < ?", (cutoff,))
            threads = connection.execute("DELETE FROM thread_cache WHERE updated_at < ?", (cutoff,))
        logger.info("Pruned cached data older_than_days=%d pull_requests=%d threads=%d", days, pull_requests.rowcount, threads.rowcount)
