"""RoboLab training configs for openpi (pi0.5 LoRA), registered at run time; openpi's source is never edited.

openpi finds configs in openpi.training.config._CONFIGS_DICT (get_config). This script builds our TrainConfig,
adds it to that dict, then calls openpi's own scripts/compute_norm_stats.py main(config_name) and
scripts/train.py main(config). Run with openpi's interpreter, from any directory:

  deactivate 2>/dev/null; unset VIRTUAL_ENV
  export HF_LEROBOT_HOME=~/projects/robolab-studio/outputs/lerobot HF_HUB_OFFLINE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false
  PY=~/projects/openpi/.venv/bin/python
  $PY scripts/openpi_robolab.py norm-stats
  $PY scripts/openpi_robolab.py train --batch-size 32 --steps 300 --exp-name probe_bs32 --measure outputs/openpi_probe/bs32.json

Paths: norm stats go to outputs/openpi_assets/<config>/<repo_id>, checkpoints to outputs/openpi_checkpoints/<config>/<exp>.
Both are under outputs/, which later becomes a link to the work disk. Weights for pi05_base are fetched by openpi's
weight loader into ~/.cache/openpi on first use.

`train --measure FILE` records, in the same process: the time from start to the initialized train state, the first
step (dominated by XLA compilation), the time of every later step (each "Step N" line is printed after
jax.device_get, i.e. after the step finished; log_interval is 1), the losses, JAX memory_stats (peak_bytes_in_use)
after training, sampled GPU memory and utilization (nvidia-smi) and host RSS of this process plus its data-loader
children (psutil), and the checkpoint size.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OPENPI_ROOT = Path(os.environ.get('OPENPI_ROOT', '~/projects/openpi')).expanduser().resolve()
if __name__ == '__main__':
    sys.dont_write_bytecode = True
os.environ.setdefault('HF_HUB_OFFLINE', '1')      # the LeRobot dataset is local; never download it

REPO_ID = 'robolab/p4d_v1_train'
CONFIG_NAME = 'pi05_robolab_lora'
BASE_WEIGHTS = 'gs://openpi-assets/checkpoints/pi05_base/params'
ASSETS_BASE = ROOT/'outputs'/'openpi_assets'
CHECKPOINT_BASE = ROOT/'outputs'/'openpi_checkpoints'


def make_config(*, batch_size=32, num_train_steps=300, exp_name='probe', repo_id=REPO_ID, name=CONFIG_NAME):
    """pi0.5 LoRA, following openpi's pi0_libero_low_mem_finetune plus pi05_libero's model settings."""
    from openpi.models import pi0_config
    from openpi.training import config as _config
    from openpi.training import weight_loaders

    model = dict(pi05=True, action_horizon=10, discrete_state_input=False,
                 paligemma_variant='gemma_2b_lora', action_expert_variant='gemma_300m_lora')
    return _config.TrainConfig(
        name=name,
        exp_name=exp_name,
        model=pi0_config.Pi0Config(**model),
        data=_config.LeRobotLiberoDataConfig(repo_id=repo_id, base_config=_config.DataConfig(prompt_from_task=True),
                                             extra_delta_transform=False),
        weight_loader=weight_loaders.CheckpointWeightLoader(BASE_WEIGHTS),
        freeze_filter=pi0_config.Pi0Config(**model).get_freeze_filter(),
        ema_decay=None,
        batch_size=batch_size,
        num_train_steps=num_train_steps,
        log_interval=1,
        save_interval=num_train_steps+1000,   # > total steps: only the final step is saved (train.py line 272)
        keep_period=None,
        wandb_enabled=False,
        assets_base_dir=str(ASSETS_BASE),
        checkpoint_base_dir=str(CHECKPOINT_BASE),
    )


def register(config):
    from openpi.training import config as _config

    if config.name in _config._CONFIGS_DICT:
        raise SystemExit(f'{config.name} already exists in openpi; refusing to replace it')
    _config._CONFIGS_DICT[config.name] = config
    return config


def openpi_script(name):
    sys.path.insert(0, str(OPENPI_ROOT/'scripts'))
    import importlib

    return importlib.import_module(name)


def check_environment():
    home = os.environ.get('HF_LEROBOT_HOME')
    if not home:
        raise SystemExit('Set HF_LEROBOT_HOME (e.g. ~/projects/robolab-studio/outputs/lerobot), the same value used '
                         'by scripts/convert_p4_to_lerobot.py.')
    dataset = Path(home).expanduser()/REPO_ID
    if not (dataset/'meta'/'info.json').is_file():
        raise SystemExit(f'LeRobot dataset not found at {dataset}')
    return dataset


class Sampler(threading.Thread):
    """GPU memory/utilization (nvidia-smi) and host RSS of this process tree (psutil), every period seconds."""
    def __init__(self, period=.5):
        super().__init__(daemon=True)
        self.period, self.samples, self.stopped = period, [], threading.Event()

    def run(self):
        import psutil

        me = psutil.Process()
        while not self.stopped.is_set():
            try:
                gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,utilization.gpu',
                                      '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                     timeout=5).stdout.strip().splitlines()[0].split(',')
                rss = me.memory_info().rss
                for child in me.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except psutil.Error:
                        pass
                self.samples.append(dict(t=time.perf_counter(), gpu_mib=int(gpu[0]), gpu_util=int(gpu[1]),
                                         rss_gib=rss/2**30, host_used_gib=psutil.virtual_memory().used/2**30))
            except Exception:   # sampling must never break training
                pass
            self.stopped.wait(self.period)


class StepClock:
    """Wraps sys.stdout: timestamps every line and records the "Step N: k=v, ..." lines of train.py."""
    def __init__(self, stream):
        self.stream, self.partial, self.steps = stream, '', []

    def write(self, text):
        self.partial += text
        while '\n' in self.partial:
            line, self.partial = self.partial.split('\n', 1)
            now = time.perf_counter()
            if line.startswith('Step '):
                head, _, rest = line.partition(':')
                values = {}
                for item in rest.split(','):
                    key, _, value = item.strip().partition('=')
                    try:
                        values[key] = float(value)
                    except ValueError:
                        pass
                self.steps.append(dict(t=now, step=int(head.split()[1]), **values))
            self.stream.write(f'[{time.strftime("%H:%M:%S")}] {line}\n')
        return len(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


class LogMarks(logging.Handler):
    def __init__(self):
        super().__init__()
        self.marks = {}

    def emit(self, record):
        message = record.getMessage()
        for key in ('Initialized data loader', 'Initialized train state'):
            if message.startswith(key) and key not in self.marks:
                self.marks[key] = time.perf_counter()


def dir_bytes(path):
    return sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file()) if Path(path).exists() else 0


def norm_stats(args):
    check_environment()
    config = register(make_config())
    openpi_script('compute_norm_stats').main(config.name)
    print(json.dumps(dict(event='norm_stats', path=str(Path(config.assets_dirs)/config.data.repo_id))), flush=True)


def train(args):
    dataset = check_environment()
    config = register(make_config(batch_size=args.batch_size, num_train_steps=args.steps, exp_name=args.exp_name))
    norm = Path(config.assets_dirs)/config.data.repo_id/'norm_stats.json'
    if not norm.is_file():
        raise SystemExit(f'Run norm-stats first ({norm} is missing)')
    train_module = openpi_script('train')
    import jax

    marks, sampler, clock = LogMarks(), Sampler(), StepClock(sys.stdout)
    logging.getLogger().addHandler(marks)
    sampler.start()
    started = time.perf_counter()
    sys.stdout = clock
    try:
        train_module.main(config)
    finally:
        sys.stdout = clock.stream
        sampler.stopped.set()
        sampler.join()
    finished = time.perf_counter()
    stats = jax.devices()[0].memory_stats() or {}
    steps = clock.steps
    report = dict(config=config.name, exp_name=config.exp_name, batch_size=config.batch_size,
                  num_train_steps=config.num_train_steps, dataset=str(dataset), norm_stats=str(norm),
                  env={key: os.environ.get(key) for key in ('HF_LEROBOT_HOME', 'XLA_PYTHON_CLIENT_PREALLOCATE',
                                                             'XLA_PYTHON_CLIENT_MEM_FRACTION')},
                  wall_s=round(finished-started, 1))
    if steps:
        state_ready = marks.marks.get('Initialized train state', started)
        durations = [b['t']-a['t'] for a, b in zip(steps, steps[1:])]
        steady = durations[10:] or durations
        report.update(
            setup_s=round(state_ready-started, 1),
            first_step_s=round(steps[0]['t']-state_ready, 1),
            step_s_median=round(statistics.median(steady), 4), step_s_mean=round(statistics.mean(steady), 4),
            step_s_p90=round(sorted(steady)[int(.9*(len(steady)-1))], 4),
            steps_logged=len(steps), loss_first=steps[0].get('loss'), loss_last=steps[-1].get('loss'),
            loss_first10_mean=statistics.mean(s['loss'] for s in steps[:10] if 'loss' in s),
            loss_last10_mean=statistics.mean(s['loss'] for s in steps[-10:] if 'loss' in s),
            losses=[round(s.get('loss', float('nan')), 5) for s in steps])
        window = [s for s in sampler.samples if steps[min(10, len(steps)-1)]['t'] <= s['t'] <= steps[-1]['t']]
    else:
        window = sampler.samples
    report.update(
        jax_peak_bytes_in_use_gib=round(stats.get('peak_bytes_in_use', 0)/2**30, 3),
        jax_bytes_limit_gib=round(stats.get('bytes_limit', 0)/2**30, 3),
        gpu_mib_max=max((s['gpu_mib'] for s in sampler.samples), default=None),
        gpu_util_training_mean=round(statistics.mean(s['gpu_util'] for s in window), 1) if window else None,
        gpu_util_training_median=statistics.median(s['gpu_util'] for s in window) if window else None,
        gpu_util_training_below_50pct=round(sum(s['gpu_util'] < 50 for s in window)/len(window), 3) if window else None,
        host_rss_gib_max=round(max((s['rss_gib'] for s in sampler.samples), default=0), 2),
        host_used_gib_max=round(max((s['host_used_gib'] for s in sampler.samples), default=0), 2),
        samples=len(sampler.samples),
        checkpoint_dir=str(config.checkpoint_dir), checkpoint_bytes=dir_bytes(config.checkpoint_dir),
        checkpoint_steps=sorted(p.name for p in Path(config.checkpoint_dir).iterdir() if p.is_dir())
        if Path(config.checkpoint_dir).exists() else [])
    if args.measure:
        out = Path(args.measure)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(dict(report, gpu_samples=[{k: round(v, 3) for k, v in s.items()}
                                                            for s in sampler.samples]), indent=1)+'\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'losses'}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('norm-stats', help='compute normalization statistics with openpi for our dataset')
    cmd = sub.add_parser('train', help='short LoRA training run through openpi scripts/train.py main(config)')
    cmd.add_argument('--batch-size', type=int, required=True)
    cmd.add_argument('--steps', type=int, required=True)
    cmd.add_argument('--exp-name', required=True)
    cmd.add_argument('--measure', help='write the measurement report (JSON) here')
    args = parser.parse_args(argv)
    norm_stats(args) if args.command == 'norm-stats' else train(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
