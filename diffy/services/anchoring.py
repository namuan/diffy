from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from diffy.core.logging import get_logger
from diffy.core.models import ChangedFile, DraftComment, PullRequest


logger = get_logger("anchoring")


def _line_exists(file: ChangedFile, side: str, line_number: int) -> bool:
    return any(line.side == side and line.line == line_number for line in file.lines)


def reattach_draft(draft: DraftComment, pull_request: PullRequest, files: list[ChangedFile]) -> DraftComment:
    logger.debug("Reattaching draft id=%s ref=%s path=%s old_head=%s new_head=%s", draft.id, draft.ref_key, draft.path, draft.head_sha, pull_request.head_sha)
    file = next((item for item in files if item.path == draft.path), None)
    if file is None:
        draft.orphaned = True
        draft.candidates = []
        logger.warning("Draft orphaned because file is missing id=%s path=%s", draft.id, draft.path)
        return draft
    if draft.head_sha == pull_request.head_sha and _line_exists(file, draft.side, draft.line):
        draft.orphaned = False
        draft.candidates = []
        logger.debug("Draft anchor retained id=%s side=%s line=%s", draft.id, draft.side, draft.line)
        return draft
    matches = [line for line in file.lines if line.side == draft.side and line.content == draft.context and line.line is not None]
    if len(matches) == 1:
        draft.line = matches[0].line or draft.line
        draft.head_sha = pull_request.head_sha
        draft.orphaned = False
        draft.candidates = []
    else:
        draft.orphaned = True
        draft.candidates = [f"{line.side}:{line.line}" for line in matches]
    draft.updated_at = datetime.now(timezone.utc).isoformat()
    logger.info("Draft reattachment result id=%s orphaned=%s candidates=%d line=%s", draft.id, draft.orphaned, len(draft.candidates), draft.line)
    return draft


def new_draft(
    pull_request: PullRequest,
    file: ChangedFile,
    line_number: int,
    body: str,
    start_line: int | None = None,
) -> DraftComment:
    logger.debug("Creating draft ref=%s path=%s side_line=%s", pull_request.ref.key, file.path, line_number)
    selected = next(
        (line for line in file.lines if line.line == line_number),
        None,
    )
    if selected is None:
        raise ValueError("Select a changed line before adding a comment.")
    draft = DraftComment(
        id=str(uuid4()),
        ref_key=pull_request.ref.key,
        path=file.path,
        head_sha=pull_request.head_sha,
        side=selected.side,
        line=line_number,
        start_line=start_line,
        body=body,
        context=selected.content,
    )
    logger.info("Created draft id=%s ref=%s path=%s side=%s line=%s", draft.id, draft.ref_key, draft.path, draft.side, draft.line)
    return draft
