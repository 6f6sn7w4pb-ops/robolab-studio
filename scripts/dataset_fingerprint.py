"""Fingerprint of a P4 OSC dataset, to check a regenerated copy on another machine without the data.

  dataset_fingerprint.py write --dataset outputs/p4d_dataset_v1 --output docs/fingerprints/p4d_dataset_v1.json.gz
  dataset_fingerprint.py check --dataset outputs/p4d_lab --fingerprint docs/fingerprints/p4d_dataset_v1.json.gz

Per episode it stores a short hash of every line of actions_and_contacts.jsonl (the commands,
OSC targets, raw contacts and end-of-tick state: physics) and of every PNG (images). model.xml
and manifest.json are left out: model.xml embeds this machine's LIBERO asset paths and the
manifest stores its hash, so they differ between machines even when the data are identical.
`check` reports, per episode, whether physics and images match and the first tick that differs.
Standard library only.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

SCHEMA = 'robolab.p4_dataset_fingerprint.v1'
CAMERAS = ('agentview', 'robot0_eye_in_hand')
SHORT = 16          # hex digits kept per line / image (64 bits)


def _short(data):
    return hashlib.sha256(data).hexdigest()[:SHORT]


def episode_fingerprint(directory):
    lines = (directory/'actions_and_contacts.jsonl').read_bytes().splitlines()
    images = {camera: [] for camera in CAMERAS}
    for line in (directory/'frames.jsonl').read_bytes().splitlines():
        frame = json.loads(line)
        for camera in CAMERAS:
            images[camera].append(_short((directory/frame['images'][camera]['file']).read_bytes()))
    return dict(ticks=len(lines), actions=[_short(line) for line in lines], images=images)


def _episodes(dataset):
    return sorted(path for path in (Path(dataset)/'episodes').iterdir() if (path/'actions_and_contacts.jsonl').is_file())


def write(dataset, output):
    dataset = Path(dataset)
    index = json.loads((dataset/'result.json').read_bytes())
    episodes = {}
    for directory in _episodes(dataset):
        episodes[directory.name] = episode_fingerprint(directory)
        print(directory.name, flush=True)
    stats = dataset/'export'/'norm_stats.json'
    document = dict(schema=SCHEMA, source=str(dataset.name), plan_sha256=index.get('plan_sha256'),
                    seed=index.get('seed'), hash='sha256 truncated to %d hex digits' % SHORT,
                    norm_stats_sha256=hashlib.sha256(stats.read_bytes()).hexdigest() if stats.is_file() else None,
                    episodes=episodes)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(output, 'wb', mtime=0) as stream:
        stream.write(json.dumps(document, sort_keys=True, separators=(',', ':')).encode())
    return document


def _first_difference(first, second):
    for tick, (a, b) in enumerate(zip(first, second)):
        if a != b:
            return tick
    return None if len(first) == len(second) else min(len(first), len(second))


def check(dataset, fingerprint):
    with gzip.open(fingerprint, 'rb') as stream:
        reference = json.loads(stream.read())
    if reference.get('schema') != SCHEMA:
        raise ValueError('not a P4 dataset fingerprint')
    dataset = Path(dataset)
    index = json.loads((dataset/'result.json').read_bytes())
    present = {directory.name: directory for directory in _episodes(dataset)}
    rows = []
    for case_id, expected in sorted(reference['episodes'].items()):
        if case_id not in present:
            rows.append(dict(case_id=case_id, missing=True))
            continue
        actual = episode_fingerprint(present[case_id])
        physics_tick = _first_difference(expected['actions'], actual['actions'])
        image_ticks = {camera: _first_difference(expected['images'][camera], actual['images'][camera]) for camera in CAMERAS}
        differing = sum(a != b for camera in CAMERAS for a, b in zip(expected['images'][camera], actual['images'][camera]))
        rows.append(dict(case_id=case_id, missing=False, physics_identical=physics_tick is None,
                         first_physics_difference_tick=physics_tick,
                         images_identical=all(tick is None for tick in image_ticks.values()),
                         first_image_difference_tick=min((t for t in image_ticks.values() if t is not None), default=None),
                         differing_images=differing))
    found = [row for row in rows if not row['missing']]
    stats = dataset/'export'/'norm_stats.json'
    summary = dict(plan_identical=index.get('plan_sha256') == reference['plan_sha256'],
                   episodes=len(rows), missing=sum(row['missing'] for row in rows),
                   extra=sorted(set(present)-set(reference['episodes'])),
                   physics_identical=sum(row['physics_identical'] for row in found),
                   images_identical=sum(row['images_identical'] for row in found),
                   differing_images=sum(row['differing_images'] for row in found),
                   norm_stats_identical=None if not stats.is_file() or not reference['norm_stats_sha256']
                   else hashlib.sha256(stats.read_bytes()).hexdigest() == reference['norm_stats_sha256'])
    if not summary['plan_identical'] or summary['missing'] or summary['physics_identical'] < len(found):
        verdict = 'physics_differs'
    elif summary['images_identical'] < len(found):
        verdict = 'images_differ_physics_identical'
    else:
        verdict = 'identical'
    return dict(verdict=verdict, summary=summary, episodes=rows)


MEANING = {
    'identical': 'Byte-identical to the laptop dataset: physics and every image match.',
    'images_differ_physics_identical': ('Physics matches exactly; some rendered images differ (different GPU/driver). '
                                        'Train and evaluate on data rendered by this machine.'),
    'physics_differs': ('Physics differs (or the plan or episodes differ). Check that MuJoCo 2.3.7, robosuite 1.4.1 '
                        'and NumPy 1.26.4 are installed; see docs/PROJECT_REPORT.md section 15.3.'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    w = sub.add_parser('write')
    w.add_argument('--dataset', type=Path, required=True)
    w.add_argument('--output', type=Path, required=True)
    c = sub.add_parser('check')
    c.add_argument('--dataset', type=Path, required=True)
    c.add_argument('--fingerprint', type=Path, required=True)
    c.add_argument('--report', type=Path, help='write the per-episode comparison as JSON')
    args = parser.parse_args()
    if args.command == 'write':
        document = write(args.dataset, args.output)
        print(json.dumps(dict(written=str(args.output), episodes=len(document['episodes']))))
        return 0
    result = check(args.dataset, args.fingerprint)
    if args.report:
        args.report.write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps(dict(verdict=result['verdict'], **result['summary'])))
    print(MEANING[result['verdict']])
    return 0 if result['verdict'] == 'identical' else 1


if __name__ == '__main__':
    sys.exit(main())
