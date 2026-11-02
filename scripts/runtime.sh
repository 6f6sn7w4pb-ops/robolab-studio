#!/usr/bin/env bash
# Sourced after cd to the project. Paths are quoted by Python.
set -eu
export PYTHONDONTWRITEBYTECODE=1
runtime_exports="$(python3 - <<'PY'
import shlex
from robolab.config import runtime_environment
for name, value in runtime_environment().items():
    print('export ' + name + '=' + shlex.quote(value))
PY
)"
eval "$runtime_exports"
unset runtime_exports
export PYTHONPATH="$PWD:$ROBOLAB_LIBERO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
