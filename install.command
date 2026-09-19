#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="diffy.app"
DEST_DIR="$HOME/Applications"
DEST_APP="$DEST_DIR/$APP_NAME"
BUILT_APP="$ROOT/dist/$APP_NAME"

echo "Building diffy…"
make -C "$ROOT" build

if [ ! -d "$BUILT_APP" ]; then
  echo "Error: Build succeeded but app bundle not found at: $BUILT_APP"
  exit 1
fi

if osascript -e 'if application "diffy" is running then tell application "diffy" to quit' >/dev/null 2>&1; then
  sleep 0.5
fi

echo "Installing ${DEST_APP}…"
mkdir -p "$DEST_DIR"
rm -rf "$DEST_APP"
mv "$BUILT_APP" "$DEST_APP"

echo "Installed: $DEST_APP"
open -n "$DEST_APP"
