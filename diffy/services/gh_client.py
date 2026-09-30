from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from diffy.core.logging import get_logger
from diffy.core.models import (
    DraftComment,
    PullRequest,
    PullRequestRef,
    ReviewComment,
    ReviewThread,
)


logger = get_logger("gh_client")


class GHClientError(RuntimeError):
    pass


@dataclass
class LoadedPullRequest:
    pull_request: PullRequest
    diff_text: str
    threads: list[ReviewThread]
    source_snapshots: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)


class GHClient:
    def __init__(self, executable: str = "gh"):
        self.executable = executable

    @staticmethod
    def create_login_shell_wrapper(directory: str | Path) -> Path:
        directory_path = Path(directory).expanduser()
        if not directory_path.is_dir():
            raise GHClientError("Select an existing directory for the wrapper script.")
        wrapper = directory_path / "diffy-gh"
        wrapper.write_text("#!/bin/sh\nexec /bin/zsh -lc 'exec gh \"$@\"' diffy-gh \"$@\"\n", encoding="utf-8")
        wrapper.chmod(0o755)
        logger.info("Created GitHub CLI login-shell wrapper path=%s", wrapper)
        return wrapper

    def _run(self, arguments: list[str], input_text: str | None = None, timeout: int = 60) -> str:
        environment = os.environ.copy()
        environment["GH_PROMPT_DISABLED"] = "1"
        command = " ".join(arguments)
        started = time.monotonic()
        logger.debug("Starting gh command timeout=%ss command=%s", timeout, command)
        try:
            result = subprocess.run(
                [self.executable, *arguments],
                input=input_text,
                capture_output=True,
                text=True,
                env=environment,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as error:
            logger.exception("GitHub CLI executable not found")
            raise GHClientError(
                "The GitHub CLI executable was not found. Open Settings and select the GitHub CLI executable using Browse."
            ) from error
        except PermissionError as error:
            logger.exception("GitHub CLI executable cannot be executed")
            raise GHClientError(
                "The GitHub CLI executable cannot be executed. Open Settings and select an executable GitHub CLI file."
            ) from error
        except subprocess.TimeoutExpired as error:
            logger.error("GitHub CLI command timed out command=%s", command)
            raise GHClientError("The GitHub CLI request timed out.") from error
        elapsed = time.monotonic() - started
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "The GitHub CLI request failed."
            logger.error("GitHub CLI command failed elapsed=%.3fs return_code=%d message=%s", elapsed, result.returncode, message)
            raise GHClientError(message)
        logger.debug("GitHub CLI command completed elapsed=%.3fs output_bytes=%d", elapsed, len(result.stdout.encode("utf-8")))
        return result.stdout

    def auth_status(self) -> str:
        logger.info("Checking GitHub CLI authentication status")
        return self._run(["auth", "status"], timeout=10)

    def load(self, ref: PullRequestRef) -> LoadedPullRequest:
        logger.info("Loading pull request ref=%s", ref.key)
        metadata = self._load_metadata(ref)
        diff_text = self._run(["pr", "diff", ref.url], timeout=120)
        from diffy.services.ast_parser import language_for_path
        from diffy.services.diff_parser import parse_unified_diff

        changed_files = parse_unified_diff(diff_text).files
        source_snapshots = {}
        for file in changed_files:
            if language_for_path(file.path) is None:
                source_snapshots[file.path] = (None, None)
                continue
            source_snapshots[file.path] = (
                None if file.status == "added" else self._load_file_source(ref, file.old_path or file.path, metadata.base_sha),
                None if file.status == "deleted" else self._load_file_source(ref, file.path, metadata.head_sha),
            )
        try:
            threads = self._load_threads(ref)
        except GHClientError as error:
            logger.warning("Review thread loading failed ref=%s error=%s", ref.key, error)
            threads = []
        logger.info("Loaded pull request ref=%s diff_bytes=%d threads=%d", ref.key, len(diff_text.encode("utf-8")), len(threads))
        return LoadedPullRequest(metadata, diff_text, threads, source_snapshots)

    def _load_file_source(self, ref: PullRequestRef, path: str, revision: str) -> str | None:
        encoded_path = quote(path, safe="/")
        try:
            output = self._run(
                ["api", f"repos/{ref.owner}/{ref.repository}/contents/{encoded_path}", "-X", "GET", "-f", f"ref={revision}"],
                timeout=30,
            )
            payload = json.loads(output)
            if payload.get("encoding") != "base64" or payload.get("size", 0) > 1_000_000:
                logger.info("Skipping unavailable or oversized source path=%s revision=%s", path, revision[:12])
                return None
            return base64.b64decode(payload.get("content", "")).decode("utf-8")
        except (GHClientError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            logger.info("Source snapshot unavailable path=%s revision=%s error=%s", path, revision[:12], error)
            return None

    def _load_metadata(self, ref: PullRequestRef) -> PullRequest:
        raw = self._run(
            [
                "pr",
                "view",
                ref.url,
                "--json",
                "number,title,body,author,baseRefName,headRefName,baseRefOid,headRefOid,url,state",
            ],
            timeout=30,
        )
        payload = json.loads(raw)
        author = payload.get("author") or {}
        logger.debug("Received pull request metadata ref=%s title_length=%d head_sha=%s", ref.key, len(payload.get("title") or ""), payload.get("headRefOid") or "")
        return PullRequest(
            ref=ref,
            title=payload.get("title") or "",
            body=payload.get("body") or "",
            author=author.get("login") or "",
            base_ref=payload.get("baseRefName") or "",
            head_ref=payload.get("headRefName") or "",
            base_sha=payload.get("baseRefOid") or "",
            head_sha=payload.get("headRefOid") or "",
            url=payload.get("url") or ref.url,
            state=payload.get("state") or "",
        )

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        arguments = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            if value is None:
                continue
            flag = "-F" if isinstance(value, int) else "-f"
            arguments.extend([flag, f"{key}={value}"])
        return json.loads(self._run(arguments, timeout=60))

    def _load_threads(self, ref: PullRequestRef) -> list[ReviewThread]:
        query = """
        query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviewThreads(first: 100, after: $cursor) {
                nodes {
                  id
                  path
                  line
                  startLine
                  diffSide
                  startDiffSide
                  isResolved
                  comments(first: 100) {
                    nodes {
                      databaseId
                      body
                      author { login }
                      createdAt
                      url
                    }
                  }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        threads: list[ReviewThread] = []
        cursor = None
        page = 0
        while True:
            page += 1
            logger.debug("Loading review thread page ref=%s page=%d", ref.key, page)
            response = self._graphql(
                query,
                {"owner": ref.owner, "name": ref.repository, "number": ref.number, "cursor": cursor},
            )
            container = response["data"]["repository"]["pullRequest"]["reviewThreads"]
            for item in container.get("nodes") or []:
                comments = []
                for comment in item.get("comments", {}).get("nodes") or []:
                    comments.append(
                        ReviewComment(
                            database_id=comment["databaseId"],
                            body=comment.get("body") or "",
                            author=(comment.get("author") or {}).get("login") or "unknown",
                            created_at=comment.get("createdAt") or "",
                            url=comment.get("url") or "",
                        )
                    )
                threads.append(
                    ReviewThread(
                        thread_id=item["id"],
                        path=item.get("path") or "",
                        line=item.get("line"),
                        start_line=item.get("startLine"),
                        side=item.get("diffSide"),
                        start_side=item.get("startDiffSide"),
                        resolved=bool(item.get("isResolved")),
                        comments=comments,
                    )
                )
            page_info = container["pageInfo"]
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        logger.info("Loaded review threads ref=%s count=%d pages=%d", ref.key, len(threads), page)
        return threads

    def submit_review(
        self,
        ref: PullRequestRef,
        head_sha: str,
        drafts: list[DraftComment],
        event: str,
        body: str,
    ) -> dict[str, Any]:
        logger.info("Submitting review ref=%s event=%s draft_count=%d summary=%s", ref.key, event, len(drafts), bool(body.strip()))
        comments = []
        for draft in drafts:
            comment: dict[str, Any] = {
                "body": draft.body,
                "path": draft.path,
                "line": draft.line,
                "side": draft.side,
            }
            if draft.start_line is not None and draft.start_line != draft.line:
                comment["start_line"] = draft.start_line
                comment["start_side"] = draft.side
            comments.append(comment)
        payload = {"body": body, "event": event, "commit_id": head_sha, "comments": comments}
        output = self._run(
            ["api", f"repos/{ref.owner}/{ref.repository}/pulls/{ref.number}/reviews", "--method", "POST", "--input", "-"],
            input_text=json.dumps(payload),
            timeout=60,
        )
        response = json.loads(output)
        logger.info("Review submitted ref=%s review_id=%s", ref.key, response.get("id"))
        return response

    def reply_to_comment(self, ref: PullRequestRef, comment_id: int, body: str) -> dict[str, Any]:
        logger.info("Submitting thread reply ref=%s comment_id=%d body_present=%s", ref.key, comment_id, bool(body.strip()))
        output = self._run(
            [
                "api",
                f"repos/{ref.owner}/{ref.repository}/pulls/comments/{comment_id}/replies",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input_text=json.dumps({"body": body}),
            timeout=60,
        )
        response = json.loads(output)
        logger.info("Thread reply submitted ref=%s comment_id=%d", ref.key, comment_id)
        return response

    def set_thread_resolved(self, thread_id: str, resolved: bool) -> bool:
        logger.info("Updating thread resolution thread_id=%s resolved=%s", thread_id, resolved)
        mutation_name = "resolveReviewThread" if resolved else "unresolveReviewThread"
        mutation = f"""
        mutation($threadId: ID!) {{
          result: {mutation_name}(input: {{threadId: $threadId}}) {{
            thread {{ isResolved }}
          }}
        }}
        """
        response = self._graphql(mutation, {"threadId": thread_id})
        is_resolved = bool(response["data"]["result"]["thread"]["isResolved"])
        logger.info("Thread resolution updated thread_id=%s resolved=%s", thread_id, is_resolved)
        return is_resolved
