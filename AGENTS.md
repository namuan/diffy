# Development Instructions

- Use PySide6 and Python 3.12 for the application.
- Manage Python versions, environments, dependencies, and commands exclusively with `uv`.
- Never invoke `python` or `python3` directly. Use `uv run python ...`.
- Use `uv sync` to install dependencies and `uv add` to change dependencies.
- Keep project dependencies in `pyproject.toml` and commit `uv.lock`.
- Use the authenticated `gh` CLI for GitHub operations; do not store GitHub credentials in the project.
- Prefer an explicitly configured `gh` executable; otherwise use `gh` from the application `PATH`. When GitHub CLI verification fails, offer the user an optional generated wrapper that invokes `gh` through a macOS zsh login shell. Do not switch to the wrapper without the user's choice or import the full interactive shell environment by default.
- Do not add testing or release/signing work to the MVP unless explicitly requested.
- Use the application logger for operational diagnostics; logs are written to `~/Library/Logs/diffy/diffy.log` with rotation.
- Never log GitHub credentials, tokens, or review/comment bodies.
