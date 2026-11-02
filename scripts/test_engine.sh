#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
. scripts/runtime.sh
export PYTHONDONTWRITEBYTECODE=1
export MUJOCO_GL=glfw
unset PYOPENGL_PLATFORM
exec "$ROBOLAB_BACKEND_PYTHON" -m unittest discover -s tests -v
