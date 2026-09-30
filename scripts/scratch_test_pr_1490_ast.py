from diffy.core.models import PullRequestRef
from diffy.services.ast_parser import language_for_path, parse_file_changes
from diffy.services.diff_parser import parse_unified_diff
from diffy.services.gh_client import GHClient


ref = PullRequestRef.parse("https://github.com/alibaba/open-code-review/pull/1490")
loaded = GHClient().load(ref)
files = parse_unified_diff(loaded.diff_text).files
assert files, "The pull request diff contained no changed files"

structural_change_count = 0
for file in files:
    language = language_for_path(file.path)
    if language is None:
        continue
    old_source, new_source = loaded.source_snapshots.get(file.path, (None, None))
    parse_file_changes(file, old_source, new_source)
    print(f"{file.path}: {file.ast_status}, {len(file.ast_changes)} structural changes")
    assert file.ast_status in {"parsed", "partial"}, f"{file.path} was not parsed: {file.ast_status}"
    structural_change_count += len(file.ast_changes)

assert structural_change_count > 0, "No structural changes were extracted from the PR"
print(f"PASS: extracted {structural_change_count} structural changes across PR #{ref.number}")
