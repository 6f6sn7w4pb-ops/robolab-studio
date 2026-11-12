"""Collect the planned P4 OSC dataset with verified exact replay, then index and audit it.

  build_p4_dataset.py --output-dir DIR [--plan v1|pilot] [--workers N] [--recheck K]
  build_p4_dataset.py --output-dir DIR --resume [--workers N] [--recheck K]

Each worker owns one simulator (about 2.7 GB RSS on the development laptop). Episodes that
were already verified are skipped on --resume; interrupted ones are moved to discarded/.
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


def _setup():
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('DISPLAY', None)
    os.environ.pop('WAYLAND_DISPLAY', None)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT/'scripts'))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0, str(LIBERO_ROOT))


def _worker(args):
    from robolab.p4_dataset_build import load_plan, ordered_cases, run_worker

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
        _, cases, controls = load_plan(root)
        order = [case.case_id for case in ordered_cases(cases, controls)]
        case_ids = order[args.shard::args.num_shards]
    run_worker(root, case_ids, subdir=args.subdir, stop=lambda: stopped, log=lambda line: print(line, flush=True))
    return 0


def _spawn(root, extra, log_path):
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--output-dir', str(root)]+extra
    stream = log_path.open('ab')
    return subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, cwd=ROOT), stream


def _wait(processes, root, total, stop_requested):
    from robolab.p4_dataset_build import CHECK_FILE
    last = None
    while any(process.poll() is None for process, _ in processes):
        if stop_requested():
            for process, _ in processes:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
        done = sum(1 for _ in (root/'episodes').glob('*/'+CHECK_FILE)) if (root/'episodes').is_dir() else 0
        if done != last:
            print(json.dumps(dict(event='progress', checked=done, planned=total)), flush=True)
            last = done
        time.sleep(5)
    for _, stream in processes:
        stream.close()
    return [process.returncode for process, _ in processes]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--plan', choices=('v1', 'pilot'), default='v1')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--recheck', type=int, default=4, help='episodes to re-collect and compare byte for byte')
    parser.add_argument('--reindex', action='store_true',
                        help='rebuild result.json from recorded files only (no simulation); re-compares recheck/')
    parser.add_argument('--compare-with', type=Path,
                        help='byte-compare episodes whose planned scene is unchanged in another dataset directory')
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
    from robolab.p4_dataset import plan_document
    from robolab.p4_dataset_build import (build_index, compare_recollection, load_plan, ordered_cases,
                                          publish_json, read_json, CHECK_FILE)

    root = args.output_dir.resolve()
    if args.compare_with:
        from robolab.p4_dataset_build import compare_with_dataset
        other = args.compare_with.resolve()
        report = compare_with_dataset(root, other)
        publish_json(root/f'comparison_with_{other.name}.json', report)
        print(json.dumps({key: value for key, value in report.items() if key != 'skipped_changed_scenes'}), flush=True)
        return 0 if report['compared'] and report['identical'] == report['compared'] else 1
    if args.reindex:
        load_plan(root)
        started = time.monotonic()
        recheck_dir = root/'recheck'
        chosen = sorted(path.name for path in recheck_dir.iterdir()
                        if (path/CHECK_FILE).is_file() and read_json(path/CHECK_FILE)['status'] == 'verified'
                        ) if recheck_dir.is_dir() else []
        recheck = [compare_recollection(root, case_id) for case_id in chosen] or None
        previous = read_json(root/'result.json') if (root/'result.json').is_file() else {}
        index = build_index(root, elapsed_wall_s=previous.get('elapsed_wall_s'), recheck=recheck)
        index.update(worker_exit_codes=previous.get('worker_exit_codes'),
                     reindexed_from_files=dict(wall_s=round(time.monotonic()-started, 3)))
        publish_json(root/'result.json', index)
        print(json.dumps(dict(event='reindexed', status=index['status'], passed=index['passed'], gate=index['gate'],
                              totals=index['totals'])), flush=True)
        return 0 if index['passed'] else 1
    if args.resume:
        if not (root/'plan.json').is_file():
            parser.error('--resume needs an existing dataset directory with plan.json')
        load_plan(root)
    else:
        root.mkdir(parents=True, exist_ok=False)
        document, _, _ = plan_document(args.plan)
        publish_json(root/'plan.json', document)
    (root/'logs').mkdir(exist_ok=True)
    plan, cases, controls = load_plan(root)
    order = ordered_cases(cases, controls)
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    started = time.monotonic()
    publish_json(root/'result.json', build_index(root, status='recording'))
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    workers = min(args.workers, len(order))
    processes = [_spawn(root, ['--shard', str(i), '--num-shards', str(workers)], root/'logs'/f'{stamp}_worker{i}.log')
                 for i in range(workers)]
    codes = _wait(processes, root, len(order), lambda: stop)
    recheck = None
    if not stop and args.recheck > 0:
        chosen, seen = [], set()
        for case in order:
            directory = root/'episodes'/case.case_id
            ok = (directory/CHECK_FILE).is_file() and read_json(directory/CHECK_FILE)['status'] == 'verified'
            if ok and case.split not in seen:
                chosen.append(case.case_id)
                seen.add(case.split)
        chosen = chosen[:args.recheck]
        if chosen:
            process = _spawn(root, ['--only', ','.join(chosen), '--subdir', 'recheck'],
                             root/'logs'/f'{stamp}_recheck.log')
            _wait([process], root, len(order), lambda: stop)
            recheck = [compare_recollection(root, case_id) for case_id in chosen]
    status = 'cancelled' if stop else None
    index = build_index(root, elapsed_wall_s=round(time.monotonic()-started, 3), recheck=recheck, status=status)
    index['worker_exit_codes'] = codes
    publish_json(root/'result.json', index)
    totals = index['totals']
    print(json.dumps(dict(event='finished', status=index['status'], passed=index['passed'], gate=index['gate'],
                          verified=totals['verified'], planned=totals['planned'],
                          successful_demos=totals['successful_demos'], plan=plan['plan'])), flush=True)
    return 0 if index['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
