# Development Instructions

- Use PySide6 and Python 3.12 for the application.
- Manage Python versions, environments, dependencies, and commands exclusively with `uv`.
- Never invoke `python` or `python3` directly. Use `uv run python ...`.
- Use `uv sync` to install dependencies and `uv add` to change dependencies.
- Keep project dependencies in `pyproject.toml` and commit `uv.lock`.
- Use the authenticated `gh` CLI for GitHub operations; do not store GitHub credentials in the project.
- Follow `PLAN.txt` for architecture and `QUESTIONS.md` for accepted MVP decisions.
- Do not add testing or release/signing work to the MVP unless explicitly requested.
- Use the application logger for operational diagnostics; logs are written to `~/Library/Logs/diffy/diffy.log` with rotation.
- Never log GitHub credentials, tokens, or review/comment bodies.
