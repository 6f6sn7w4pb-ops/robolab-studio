"""P5 batch closed-loop evaluation on the planned P4 test scenes.

  run_p5_eval.py --policy teacher --output-dir DIR [--dataset outputs/p4d_dataset_v1]
                 [--splits test_instance,test_unseen] [--workers N] [--recheck K] [--reference]
  run_p5_eval.py --policy adapter:mock --output-dir DIR
  run_p5_eval.py --policy teacher --scene-set extended --anti-roll [--appearance all] --output-dir DIR
  run_p5_eval.py --policy adapter:<name> --endpoint http://HOST:PORT --output-dir DIR
  run_p5_eval.py --output-dir DIR --resume | --resummarize

Policies: teacher (privileged rule teacher, harness reference), wrong_part_teacher (aims at the
other part; part-metric control), adapter:<name> (registered VlaAdapter via robolab.observation.v1).
Scene conditions (evaluation only, recorded in the config): --scene-set extended adds 176 audited
extra test episodes; --anti-roll gives cylinder targets rolling friction against the floor;
--appearance lighting,background,objects|all applies seeded per-scene rendering changes.
Each worker owns one simulator (about 2.7 GB RSS); --workers is capped by available memory.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
WORKER_RSS_GB = 2.7
RESERVE_GB = 0.5


def _setup():
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('DISPLAY', None)
    os.environ.pop('WAYLAND_DISPLAY', None)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0, str(LIBERO_ROOT))


def available_gb():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1])/1024/1024
    except OSError:
        pass
    return None


def worker_budget(requested, available):
    """Workers that fit in memory (at least one); None means unknown memory, keep the request."""
    if available is None:
        return requested
    return max(1, min(requested, int((available-RESERVE_GB)//WORKER_RSS_GB)))


def _worker(args):
    from robolab.p5_eval import read_json, run_worker

    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    root = args.output_dir.resolve()
    if args.only:
        case_ids = args.only.split(',')
    else:
        case_ids = read_json(root/'eval_config.json')['case_ids'][args.shard::args.num_shards]
    run_worker(root, case_ids, subdir=args.subdir, stop=lambda: stopped, log=lambda line: print(line, flush=True))
    return 0


def _spawn(root, extra, log_path):
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--output-dir', str(root)]+extra
    stream = log_path.open('ab')
    return subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, cwd=ROOT), stream


def _wait(processes, root, total, stop_requested, subdir='episodes'):
    from robolab.p5_eval import RESULT_FILE
    last = None
    while any(process.poll() is None for process, _ in processes):
        if stop_requested():
            for process, _ in processes:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
        done = sum(1 for _ in (root/subdir).glob('*/'+RESULT_FILE)) if (root/subdir).is_dir() else 0
        if done != last:
            print(json.dumps(dict(event='progress', finished=done, planned=total)), flush=True)
            last = done
        time.sleep(5)
    for _, stream in processes:
        stream.close()
    return [process.returncode for process, _ in processes]


def _commit():
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', 'robolab', 'scripts'], cwd=ROOT,
                                    capture_output=True, text=True, check=True).stdout.strip())
        return dict(head=head, dirty=dirty)
    except (OSError, subprocess.CalledProcessError):
        return None


def _recheck_ids(config, root, count):
    from robolab.p5_eval import RESULT_FILE
    chosen, seen = [], set()
    for case_id in config['case_ids']:
        if not (root/'episodes'/case_id/RESULT_FILE).is_file():
            continue
        split = json.loads((root/'episodes'/case_id/RESULT_FILE).read_bytes())['case']['split']
        if split not in seen:
            chosen.append(case_id)
            seen.add(split)
    return chosen[:count]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--policy', default='teacher')
    parser.add_argument('--endpoint', default='', help='inference service for adapter policies')
    parser.add_argument('--timeout', type=float, default=10.)
    parser.add_argument('--dataset', type=Path, default=ROOT/'outputs'/'p4d_dataset_v1',
                        help='P4-D dataset directory whose plan supplies the scenes')
    parser.add_argument('--splits', default='test_instance,test_unseen')
    parser.add_argument('--scene-set', choices=('dataset', 'extended'), default='dataset',
                        help='dataset: the P4-D plan test cases; extended: plus the audited extra test scenes')
    parser.add_argument('--anti-roll', action='store_true',
                        help='rolling friction between cylinder targets and the floor (removes slow self-rolling)')
    parser.add_argument('--appearance', default='',
                        help='rendering variation: lighting,background,objects (comma list), all, or none')
    parser.add_argument('--language', choices=('en', 'zh'), default='en')
    parser.add_argument('--horizon', type=int, default=0, help='ticks per episode (default: dataset length)')
    parser.add_argument('--execute-steps', type=int, default=0, help='actions executed per chunk (0 = whole chunk)')
    parser.add_argument('--keyframe-every', type=int, default=20)
    parser.add_argument('--reference', action='store_true',
                        help='compare actions, qpos, images and state tick by tick with the recorded dataset episode')
    parser.add_argument('--limit', type=int, default=0, help='evaluate only the first N planned cases (smoke runs)')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--recheck', type=int, default=2, help='episodes to re-run and compare byte for byte')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--resummarize', action='store_true', help='rebuild summary.json from episode files')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--shard', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--num-shards', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--only', default='', help=argparse.SUPPRESS)
    parser.add_argument('--subdir', default='episodes', help=argparse.SUPPRESS)
    args = parser.parse_args()
    _setup()
    if args.worker:
        return _worker(args)
    if not 1 <= args.workers <= 4:
        parser.error('--workers must be 1-4')
    from robolab.p4_osc_recording import TICKS
    from robolab.p5_eval import (EVAL_SPLITS, build_summary, compare_runs, config_document, describe_without_loading,
                                 policy_spec, publish_json, read_json, select_cases)
    from robolab.p5_variation import parse_appearance

    root = args.output_dir.resolve()
    if args.resummarize:
        previous = read_json(root/'summary.json') if (root/'summary.json').is_file() else {}
        summary = build_summary(root, recheck=previous.get('recheck'), elapsed_wall_s=previous.get('elapsed_wall_s'))
        publish_json(root/'summary.json', summary)
        print(json.dumps(dict(event='resummarized', status=summary['status'], checks=summary['harness_checks'])))
        return 0
    if args.resume:
        if not (root/'eval_config.json').is_file():
            parser.error('--resume needs an existing evaluation directory')
        config = read_json(root/'eval_config.json')
    else:
        splits = tuple(name for name in args.splits.split(',') if name)
        known = set(EVAL_SPLITS)|{'train', 'validation', 'control'}
        if not splits or set(splits)-known:
            parser.error('unknown split in --splits')
        try:
            spec = policy_spec(args.policy, args.endpoint, args.timeout)
        except ValueError as error:
            parser.error(str(error))
        if spec['kind'] == 'adapter' and args.reference:
            print('note: --reference compares against the teacher demonstrations; divergence is expected', flush=True)
        try:
            appearance = parse_appearance(args.appearance)
        except ValueError as error:
            parser.error(str(error))
        dataset = args.dataset.resolve()
        plan, cases, scene_info = select_cases(dataset, set(splits), args.scene_set)
        if args.limit:
            cases = cases[:args.limit]
        if not cases:
            parser.error('no planned cases in the selected splits')
        root.mkdir(parents=True, exist_ok=False)
        config = config_document(spec, describe_without_loading(spec), dataset, plan, cases, splits=splits,
                                 language=args.language, horizon=args.horizon or TICKS,
                                 execute_steps=args.execute_steps, keyframe_every=args.keyframe_every,
                                 reference=args.reference, commit=_commit(), scene_set=args.scene_set,
                                 scene_set_sha256=scene_info['sha256'], anti_roll=args.anti_roll,
                                 appearance=appearance)
        publish_json(root/'eval_config.json', config)
    (root/'logs').mkdir(exist_ok=True)
    available = available_gb()
    workers = min(worker_budget(args.workers, available), len(config['case_ids']))
    if workers < args.workers:
        print(json.dumps(dict(event='workers_capped', requested=args.workers, using=workers,
                              mem_available_gb=round(available, 2))), flush=True)
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    started = time.monotonic()
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    processes = [_spawn(root, ['--shard', str(i), '--num-shards', str(workers)], root/'logs'/f'{stamp}_worker{i}.log')
                 for i in range(workers)]
    codes = _wait(processes, root, len(config['case_ids']), lambda: stop)
    recheck = None
    if not stop and args.recheck > 0:
        chosen = _recheck_ids(config, root, args.recheck)
        if chosen:
            process = _spawn(root, ['--only', ','.join(chosen), '--subdir', 'recheck'], root/'logs'/f'{stamp}_recheck.log')
            _wait([process], root, len(chosen), lambda: stop, subdir='recheck')
            recheck = [compare_runs(root/'episodes'/case_id, root/'recheck'/case_id) for case_id in chosen
                       if (root/'recheck'/case_id).is_dir()]
    summary = build_summary(root, recheck=recheck, elapsed_wall_s=round(time.monotonic()-started, 3),
                            status='cancelled' if stop else None)
    summary['worker_exit_codes'] = codes
    publish_json(root/'summary.json', summary)
    overall = (summary['results'] or {}).get('overall', {})
    print(json.dumps(dict(event='finished', status=summary['status'], finished=summary['finished'],
                          planned=summary['planned'], checks=summary['harness_checks'],
                          success=overall.get('success'), part_hit=overall.get('part_hit'),
                          collision=overall.get('collision'))), flush=True)
    return 0 if summary['status'] == 'finalized' else 1


if __name__ == '__main__':
    raise SystemExit(main())
