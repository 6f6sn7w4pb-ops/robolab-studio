"""Write project-owned paths and LIBERO config, without importing LIBERO."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def configure(backend, libero, destination, config_dir):
    backend = backend.expanduser().absolute()
    libero = libero.expanduser().resolve()
    destination = destination.expanduser().resolve()
    config_dir = config_dir.expanduser().resolve()
    if not backend.is_file():
        raise ValueError(f'Backend Python not found: {backend}')
    base = libero/'libero'/'libero'
    for relative in ('envs/env_wrapper.py', 'bddl_files', 'init_files', 'assets'):
        if not (base/relative).exists():
            raise ValueError(f'LIBERO source/assets missing: {base/relative}')
    if destination.exists() or (config_dir/'config.yaml').exists():
        raise ValueError('Configuration already exists; edit it explicitly instead of overwriting it.')
    if not config_dir.is_relative_to(ROOT/'settings'):
        raise ValueError('LIBERO config destination must be inside this project/settings.')
    values = {'benchmark_root': str(base), 'bddl_files': str(base/'bddl_files'),
              'init_states': str(base/'init_files'), 'assets': str(base/'assets'),
              'datasets': str(ROOT/'datasets')}
    (ROOT/'datasets').mkdir(exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    # JSON scalars are valid YAML; no PyYAML installation required here.
    with (config_dir/'config.yaml').open('x', encoding='utf-8') as stream:
        stream.write(''.join(f'{key}: {json.dumps(value)}\n' for key, value in values.items()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump({'backend_python': str(backend), 'libero_root': str(libero),
                   'libero_config_path': str(config_dir)}, stream, indent=2)
        stream.write('\n')
    print(f'Runtime paths: {destination}\nProject LIBERO config: {config_dir}/config.yaml')


def main():
    from robolab.config import BACKEND_PYTHON, LIBERO_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-python', type=Path, default=BACKEND_PYTHON)
    parser.add_argument('--libero-root', type=Path, default=LIBERO_ROOT)
    args = parser.parse_args()
    try:
        configure(args.backend_python, args.libero_root, ROOT/'settings'/'runtime.json', ROOT/'settings'/'libero')
    except (ValueError, OSError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
