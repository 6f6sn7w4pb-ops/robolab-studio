"""Portability and non-destructive setup checks; standard library only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def probe(self, settings, extra=None):
        env = {k:v for k,v in os.environ.items() if k not in (
            'ROBOLAB_BACKEND_PYTHON','ROBOLAB_LIBERO_ROOT','LIBERO_CONFIG_PATH','ROBOLAB_OFT_ROOT')}
        env.update(ROBOLAB_RUNTIME_CONFIG=str(settings), PYTHONDONTWRITEBYTECODE='1')
        env.update(extra or {})
        result = subprocess.run([sys.executable, '-c',
            'import json; from robolab.config import runtime_environment; print(json.dumps(runtime_environment()))'],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_paths_expand_and_environment_overrides_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'runtime.json'
            config.write_text(json.dumps({'backend_python':'lab python/bin/python',
                'libero_root':'third_party/LIBERO', 'libero_config_path':'settings/libero'}))
            result = self.probe(config, {'ROBOLAB_LIBERO_ROOT':str(Path(directory)/'other LIBERO')})
            self.assertEqual(result['ROBOLAB_BACKEND_PYTHON'], str(ROOT/'lab python/bin/python'))
            self.assertEqual(result['ROBOLAB_LIBERO_ROOT'], str(Path(directory)/'other LIBERO'))
            self.assertEqual(result['LIBERO_CONFIG_PATH'], str(ROOT/'settings/libero'))

    def test_missing_configuration_uses_current_home(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.probe(Path(directory)/'absent.json')
            self.assertNotIn('/home/openvla', result['ROBOLAB_BACKEND_PYTHON'].replace(str(Path.home()), 'HOME'))

    def test_setup_never_overwrites_existing_configuration(self):
        spec = importlib.util.spec_from_file_location('configure_runtime', ROOT/'scripts/configure_runtime.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); module.ROOT = root
            base = root/'external LIBERO'/'libero'/'libero'
            for relative in ('envs', 'bddl_files', 'init_files', 'assets'):
                (base/relative).mkdir(parents=True)
            (base/'envs/env_wrapper.py').write_text('# fixture')
            destination = root/'settings/runtime.json'; config_dir = root/'settings/libero'
            module.configure(Path(sys.executable), base.parent.parent, destination, config_dir)
            self.assertTrue((root/'datasets').is_dir())
            before = destination.read_bytes(), (config_dir/'config.yaml').read_bytes()
            with self.assertRaises(ValueError):
                module.configure(Path(sys.executable), base.parent.parent, destination, config_dir)
            self.assertEqual(before, (destination.read_bytes(), (config_dir/'config.yaml').read_bytes()))
            self.assertEqual(json.loads(destination.read_text())['backend_python'], str(Path(sys.executable).absolute()))
            with self.assertRaises(ValueError):
                module.configure(Path(sys.executable), base.parent.parent, root/'elsewhere.json', root/'global-config')

    def test_missing_backend_reports_failure_without_creating_libero_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); settings = root/'runtime.json'
            settings.write_text(json.dumps({'backend_python':str(root/'no-python'),
                'libero_root':str(root/'no-libero'), 'libero_config_path':str(root/'no-config')}))
            env = {k:v for k,v in os.environ.items() if k not in (
                'ROBOLAB_BACKEND_PYTHON','ROBOLAB_LIBERO_ROOT','LIBERO_CONFIG_PATH')}
            env.update(ROBOLAB_RUNTIME_CONFIG=str(settings), PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([sys.executable, str(ROOT/'scripts/check_runtime.py')],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report['ready']); self.assertEqual(len(report['problems']), 3)
            self.assertFalse((root/'no-config').exists())


if __name__ == '__main__': unittest.main()
