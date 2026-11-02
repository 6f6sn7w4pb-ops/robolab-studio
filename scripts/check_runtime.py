"""Read-only readiness check. No GL context, model weights or downloads."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from robolab.config import BACKEND_PYTHON, LIBERO_ROOT, LIBERO_CONFIG, backend_environment


def check(gui=False):
    problems, warnings = [], []
    report = {'backend_python': str(BACKEND_PYTHON), 'libero_root': str(LIBERO_ROOT),
              'libero_config': str(LIBERO_CONFIG/'config.yaml'), 'gui_requested': gui,
              'rendering_tested': False, 'real_vla_tested': False}
    if not BACKEND_PYTHON.is_file():
        problems.append('Backend Python missing; configure settings/runtime.json or ROBOLAB_BACKEND_PYTHON.')
    if not (LIBERO_ROOT/'libero'/'libero'/'envs'/'env_wrapper.py').is_file():
        problems.append('LIBERO source missing; set ROBOLAB_LIBERO_ROOT or see docs/INSTALL.md.')
    if not (LIBERO_CONFIG/'config.yaml').is_file():
        problems.append('LIBERO config missing; run python3 scripts/configure_runtime.py first.')
    if not problems:
        code = '''import json, importlib.metadata as md
import yaml
from pathlib import Path
import os
config = yaml.safe_load((Path(os.environ['LIBERO_CONFIG_PATH'])/'config.yaml').read_text())
for key in ('benchmark_root', 'bddl_files', 'init_states', 'assets'):
    if not isinstance(config, dict) or key not in config or not Path(config[key]).is_dir():
        raise ValueError('Missing/invalid LIBERO path: '+key)
import mujoco, numpy, PIL
from libero.libero import benchmark
from libero.libero.envs import OffScreenRenderEnv
suite = benchmark.get_benchmark_dict()['libero_object']()
task = suite.get_task(0)
for key, name in [('bddl_files', task.bddl_file), ('init_states', task.init_states_file)]:
    if not (Path(config[key])/task.problem_folder/name).is_file():
        raise ValueError('Missing default task resource: '+name)
print('ROBOLAB_RUNTIME='+json.dumps({name: md.version(name) for name in ('mujoco','robosuite','libero','numpy','Pillow','torch')}))
'''
        try:
            env = backend_environment()
            env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
            result = subprocess.run([str(BACKEND_PYTHON), '-c', code], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
            lines = [line for line in result.stdout.splitlines() if line.startswith('ROBOLAB_RUNTIME=')]
            if result.returncode or not lines:
                problems.append('Backend import failed: '+(result.stderr or result.stdout)[-4000:])
            else:
                report['versions'] = json.loads(lines[-1].split('=', 1)[1])
                for name, expected in {'mujoco': '2.3.7', 'robosuite': '1.4.1', 'libero': '0.1.0', 'numpy':'1.26.4'}.items():
                    if report['versions'][name] != expected:
                        problems.append(f'{name}={report["versions"][name]}; verified version is {expected}. Use an isolated environment; do not upgrade your existing research environment.')
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            problems.append(str(error))
    if gui:
        python = ROOT/'.venv'/'bin'/'python'
        if not python.is_file():
            problems.append('GUI environment missing; run bash scripts/setup.sh.')
        else:
            try:
                result = subprocess.run([str(python), '-c', 'import PySide6; print(PySide6.__version__)'],
                                        capture_output=True, text=True, timeout=15)
                if result.returncode or result.stdout.strip() != '6.8.3':
                    problems.append('GUI requires PySide6 6.8.3 in project .venv.')
            except (OSError, subprocess.TimeoutExpired) as error:
                problems.append(str(error))
        if not os.getenv('DISPLAY') and not os.getenv('WAYLAND_DISPLAY'):
            problems.append('No desktop display. Use a Linux desktop/WSLg or run the headless P1 test.')
    warnings.append('Imports/resources only. Run P1 to verify EGL and --smoke to verify the real desktop.')
    report.update(ready=not problems, problems=problems, warnings=warnings)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    report = check(args.gui)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
