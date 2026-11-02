#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
. scripts/runtime.sh
unset QT_QPA_PLATFORM_PLUGIN_PATH QT_QPA_FONTDIR
export PYTHONDONTWRITEBYTECODE=1
if [ -n "${ROBOLAB_QT_PLATFORM:-}" ]; then
  export QT_QPA_PLATFORM="$ROBOLAB_QT_PLATFORM"
elif [ -n "${WAYLAND_DISPLAY:-}" ]; then
  export QT_QPA_PLATFORM=wayland
else
  export QT_QPA_PLATFORM=xcb
fi
case "${1:-}" in
  --doctor) exec python3 scripts/check_runtime.py ;;
  --baseline-doctor) exec "$ROBOLAB_BACKEND_PYTHON" scripts/doctor.py ;;
esac
if [ ! -x .venv/bin/python ]; then
  echo 'Missing GUI environment. Run scripts/setup.sh first.' >&2
  exit 1
fi
case "${1:-}" in
  --self-test) exec .venv/bin/python -m robolab.qa_gui ;;
  --policy-test) exec .venv/bin/python -m robolab.qa_policy_gui ;;
  --p1-test) exec .venv/bin/python -m robolab.qa_p1_gui ;;
  --language-test) exec .venv/bin/python -m robolab.qa_language_gui ;;
  --p2-test) exec .venv/bin/python -m robolab.qa_p2_gui ;;
  --smoke) exec .venv/bin/python -m robolab.qa_gui --smoke ;;
  '') python3 scripts/check_runtime.py --gui; exec .venv/bin/python -m robolab ;;
  *) echo "Unknown option: $1" >&2; exit 2 ;;
esac
