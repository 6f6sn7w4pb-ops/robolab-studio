#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
# Only the GUI environment under this project is created or changed.
if [ ! -d .venv ]; then "${ROBOLAB_GUI_PYTHON:-python3}" -m venv .venv; fi
.venv/bin/python -m pip install 'PySide6==6.8.3'
echo 'GUI environment ready. Existing simulation/model environments are untouched.'
