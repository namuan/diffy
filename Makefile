.DEFAULT_GOAL := help

.PHONY: help sync run build open doctor check test-gui clean

help:
	@printf '%s\n' \
		'make sync                 Install project dependencies with uv' \
		'make run                  Run diffy (optionally: PR=owner/repo#123)' \
		'make build                Build dist/diffy.app' \
		'make open                 Open the built macOS application' \
		'make doctor               Verify system and project prerequisites' \
		'make check                Compile the Python sources' \
		'make test-gui            Run the GUI integration test' \
		'make clean                Remove generated build artifacts'

sync:
	uv sync --extra build

run:
	uv run diffy $(PR)

build:
	uv sync --extra build
	uv run python scripts/build_app.py

open:
	open dist/diffy.app

doctor:
	@set -u; \
	failed=0; \
	pass() { printf '  ✓ %s\n' "$$1"; }; \
	fail() { printf '  ✗ %s\n' "$$1"; failed=1; }; \
	check_command() { if command -v "$$1" >/dev/null 2>&1; then pass "$$1"; else fail "$$1"; fi; }; \
	printf '%s\n' 'Checking diffy prerequisites…'; \
	if [ "$$(uname -s)" = "Darwin" ]; then pass 'macOS'; else fail 'macOS'; fi; \
	check_command uv; \
	check_command gh; \
	check_command make; \
	check_command open; \
	check_command osascript; \
	check_command codesign; \
	check_command xcode-select; \
	if xcode-select -p >/dev/null 2>&1; then pass 'Xcode Command Line Tools'; else fail 'Xcode Command Line Tools'; fi; \
	if gh auth status >/dev/null 2>&1; then pass 'GitHub CLI authentication'; else fail 'GitHub CLI authentication'; fi; \
	if uv run --no-sync python -c 'import PyInstaller, PySide6' >/dev/null 2>&1; then pass 'Python build dependencies'; else fail 'Python build dependencies'; fi; \
	for asset in assets/logo.png assets/AppIcon.icns assets/filter.svg assets/copy.svg assets/external-link.svg; do \
		if [ -f "$$asset" ]; then pass "$$asset"; else fail "$$asset"; fi; \
	done; \
	if [ "$$failed" -ne 0 ]; then printf '%s\n' 'Prerequisite check failed.'; exit 1; fi; \
	printf '%s\n' 'All prerequisites are present.'

check:
	uv run python -m compileall -q diffy scripts tests

test-gui:
	QT_QPA_PLATFORM=offscreen uv run python -m unittest tests.test_gui -v

clean:
	rm -rf build dist diffy.spec
