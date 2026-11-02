#!/usr/bin/env bash
set -eu
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
. scripts/runtime.sh
smoke_python="${ROBOLAB_BACKEND_PYTHON:-${BACKEND_PYTHON:-$HOME/venvs/openvla-oft/bin/python}}"
if [ ! -x "$smoke_python" ]; then
  echo '未找到现有后端 Python，请设置 ROBOLAB_BACKEND_PYTHON；本入口不会创建环境。' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
export MUJOCO_GL=egl
export PYOPENGL_PLATFORM=egl
unset DISPLAY WAYLAND_DISPLAY
exec "$smoke_python" "$project_root/scripts/smoke_test_libero.py" "$@"
