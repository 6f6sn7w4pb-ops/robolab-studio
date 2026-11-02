"""Portable process-local configuration; never changes user environments."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = Path(os.getenv('ROBOLAB_RUNTIME_CONFIG', str(ROOT/'settings'/'runtime.json'))).expanduser()

def load_settings(path):
    if not path.is_file(): return {}
    settings = json.loads(path.read_text(encoding='utf-8'))
    allowed = {'backend_python', 'libero_root', 'libero_config_path', 'oft_root'}
    if not isinstance(settings, dict) or set(settings)-allowed:
        raise ValueError(f'Invalid runtime settings: {path}; allowed keys: {sorted(allowed)}')
    if any(not isinstance(value, str) or not value.strip() for value in settings.values()):
        raise ValueError(f'Runtime paths must be nonempty strings: {path}')
    return settings

SETTINGS = load_settings(SETTINGS_FILE)

def runtime_path(key, environment, default):
    value = Path(os.path.expandvars(os.getenv(environment, SETTINGS.get(key, str(default))))).expanduser()
    return value if value.is_absolute() else ROOT/value

def first_existing(candidates):
    return next((path for path in candidates if path.exists()), candidates[-1])

BACKEND_PYTHON = runtime_path('backend_python', 'ROBOLAB_BACKEND_PYTHON', first_existing([
    ROOT/'.venv-sim'/'bin'/'python', Path.home()/'venvs'/'openvla-oft'/'bin'/'python']))
LIBERO_ROOT = runtime_path('libero_root', 'ROBOLAB_LIBERO_ROOT', first_existing([
    ROOT/'third_party'/'LIBERO', ROOT.parent/'LIBERO', Path.home()/'projects'/'LIBERO']))
OFT_ROOT = runtime_path('oft_root', 'ROBOLAB_OFT_ROOT', ROOT.parent/'openvla-oft')
LIBERO_CONFIG = runtime_path('libero_config_path', 'LIBERO_CONFIG_PATH', first_existing([
    ROOT/'settings'/'libero', Path.home()/'.libero']))
DEFAULT_PROJECTS = ROOT / 'projects'

# LIBERO reads this on first import. Only this process and children inherit it.
os.environ.setdefault('LIBERO_CONFIG_PATH', str(LIBERO_CONFIG))

def runtime_environment():
    return {'ROBOLAB_BACKEND_PYTHON': str(BACKEND_PYTHON),
            'ROBOLAB_LIBERO_ROOT': str(LIBERO_ROOT), 'ROBOLAB_OFT_ROOT': str(OFT_ROOT),
            'LIBERO_CONFIG_PATH': str(LIBERO_CONFIG)}

def backend_environment():
    env = dict(os.environ)
    env.update(runtime_environment())
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(LIBERO_ROOT), env.get('PYTHONPATH', '')])
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    env['MUJOCO_GL'] = 'glfw'
    for key in ('PYOPENGL_PLATFORM', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_FONTDIR'):
        env.pop(key, None)
    return env
