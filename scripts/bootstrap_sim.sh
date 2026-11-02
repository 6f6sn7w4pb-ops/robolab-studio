#!/usr/bin/env bash
# Explicit opt-in for a NEW machine. Never installs into an existing backend.
set -eu
cd "$(dirname "$0")/.."
if [ "${1:-}" != '--create-isolated-env' ]; then
  echo 'New machine only: bash scripts/bootstrap_sim.sh --create-isolated-env'
  echo 'Downloads simulation packages (including CPU PyTorch) and LIBERO assets; no VLA weights.'
  exit 2
fi
sim_python="${ROBOLAB_BOOTSTRAP_PYTHON:-python3.10}"
"$sim_python" -c 'import sys; assert sys.version_info[:2] == (3,10), "Python 3.10 required"'
if [ -e .venv-sim ] || [ -e third_party/LIBERO ] || [ -e settings/runtime.json ]; then
  echo 'A simulation environment, LIBERO clone or runtime config already exists. Reuse/check it; this installer will not overwrite it.' >&2
  exit 1
fi
mkdir -p third_party
git clone https://github.com/Lifelong-Robot-Learning/LIBERO.git third_party/LIBERO
git -C third_party/LIBERO checkout --detach 8f1084e3132a39270c3a13ebe37270a43ece2a01
"$sim_python" -m venv .venv-sim
.venv-sim/bin/python -m pip install 'pip==24.3.1' 'setuptools==75.3.2' wheel
.venv-sim/bin/python -m pip install 'torch==2.2.0+cpu' --index-url https://download.pytorch.org/whl/cpu
.venv-sim/bin/python -m pip install -r requirements/sim.txt
.venv-sim/bin/python -m pip install --no-deps --no-build-isolation -e third_party/LIBERO
python3 scripts/configure_runtime.py --backend-python "$PWD/.venv-sim/bin/python" --libero-root "$PWD/third_party/LIBERO"
bash scripts/launch.sh --doctor
echo 'Simulation imports checked. Next: bash scripts/setup.sh; bash scripts/smoke_test_libero.sh; bash scripts/launch.sh'
