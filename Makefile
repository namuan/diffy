.DEFAULT_GOAL := help

.PHONY: help sync run build open check test-gui clean

help:
	@printf '%s\n' \
		'make sync                 Install project dependencies with uv' \
		'make run                  Run diffy (optionally: PR=owner/repo#123)' \
		'make build                Build dist/diffy.app' \
		'make open                 Open the built macOS application' \
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

check:
	uv run python -m compileall -q diffy scripts tests

test-gui:
	QT_QPA_PLATFORM=offscreen uv run python -m unittest tests.test_gui -v

clean:
	rm -rf build dist diffy.spec
