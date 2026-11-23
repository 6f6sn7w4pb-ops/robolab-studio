"""P4 training view (HDF5) -> LeRobot dataset for openpi training. Runs in openpi's environment.

openpi pins LeRobot at commit 0cf86487 (dataset format v2.1), so this script uses openpi's interpreter:

  export HF_LEROBOT_HOME=~/projects/robolab-studio/outputs/lerobot
  export HF_HUB_OFFLINE=1
  PY=~/projects/openpi/.venv/bin/python
  $PY scripts/convert_p4_to_lerobot.py convert --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
  $PY scripts/convert_p4_to_lerobot.py verify  --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
  $PY scripts/convert_p4_to_lerobot.py append-test --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --scratch-root <empty dir>
  $PY scripts/convert_p4_to_lerobot.py compare robolab/p4d_v1_train robolab/p4d_v1_train_clean

verify is the reproducible evidence: it reads the HDF5 directly (not through iter_policy_frames) and requires every
image of the LeRobot dataset to equal the HDF5 image flipped horizontally, pixel for pixel, plus exact state, actions,
task, index and timestamp. compare checks that two conversions of the same input are byte-identical.

HF_LEROBOT_HOME must be set explicitly and must be the same value later for openpi's compute_norm_stats.py and
train.py, which find the dataset as $HF_LEROBOT_HOME/<repo-id>. Nothing is downloaded or pushed to the Hub.

Format choices:
- Baseline fields only, named like openpi's LIBERO example: image, wrist_image (uint8 224x224x3, dtype "image",
  i.e. lossless PNG embedded in parquet; no video), state (8 float32), actions (7 float32), task (English).
- Images use the openpi LIBERO orientation (raw robosuite frame rotated 180 deg = canonical image flipped
  horizontally), the orientation pi05_libero was trained and evaluated with.
- fps = 20, the OSC control frequency of the recordings (openpi's LIBERO example uses 10 for its own data).

Extra per-frame fields (expected contacts, requested-part centre, ...) plug in through EXTRA_FIELDS below; the
training config later decides which fields a model uses, so all models can share one dataset.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if __name__ == '__main__':
    sys.dont_write_bytecode = True   # openpi's interpreter must not leave caches inside the RoboLab tree
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
os.environ.setdefault('HF_HUB_OFFLINE', '1')   # a missing local file must fail, never trigger a download

import numpy as np  # noqa: E402

SCHEMA = 'robolab.lerobot_conversion.v1'
MANIFEST = 'robolab_conversion.json'
FPS = 20
IMAGE_CONVENTION = 'libero_rotated_180'
LANGUAGE = 'en'
ROBOT_TYPE = 'panda'
CAMERAS = (('image', 'obs/agentview_rgb'), ('wrist_image', 'obs/eye_in_hand_rgb'))


@dataclass(frozen=True)
class ExtraField:
    """One extra per-frame field: LeRobot feature spec plus a reader returning a (T, ...) array for one demo
    group of the HDF5 training view (h5py.Group), aligned with its frames."""
    feature: dict
    read: Callable


# Name -> ExtraField. Empty for the baseline dataset. Example for later (not enabled):
#   'requested_center_gripper': ExtraField(dict(dtype='float32', shape=(3,), names=['x', 'y', 'z']),
#                                          lambda demo: demo['labels/requested_center_gripper_m'][()].astype(np.float32))
EXTRA_FIELDS: dict = {}


def lerobot_home():
    value = os.environ.get('HF_LEROBOT_HOME')
    if not value:
        raise SystemExit('Set HF_LEROBOT_HOME explicitly (e.g. ~/projects/robolab-studio/outputs/lerobot) and use the '
                         'same value for compute_norm_stats.py and train.py.')
    return Path(value).expanduser().resolve()


def demo_names(handle):
    return sorted(handle['data'], key=lambda name: int(name.split('_')[1]))


def names_attr(handle, key, size):
    try:
        names = json.loads(handle.attrs[key])
    except (KeyError, TypeError, ValueError):
        return [key.split('_')[0]]
    return names if len(names) == size else [key.split('_')[0]]


def features_for(handle, extra=()):
    first = handle['data'][demo_names(handle)[0]]
    features = {key: dict(dtype='image', shape=tuple(first[path].shape[1:]), names=['height', 'width', 'channel'])
                for key, path in CAMERAS}
    features['state'] = dict(dtype='float32', shape=(first['obs/state'].shape[1],),
                             names=names_attr(handle, 'state_names', first['obs/state'].shape[1]))
    features['actions'] = dict(dtype='float32', shape=(first['actions'].shape[1],),
                               names=names_attr(handle, 'action_names', first['actions'].shape[1]))
    for name in extra:
        features[name] = dict(EXTRA_FIELDS[name].feature)
    return features


def sha256_file(path, chunk=1 << 24):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while block := stream.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def git_state():
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', '--no-optional-locks', 'status', '--porcelain', '--', 'robolab', 'scripts'],
                                    cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
        return dict(head=head, dirty=dirty)
    except (OSError, subprocess.CalledProcessError):
        return None


def dir_bytes(path):
    return sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file())


def write_episodes(dataset, hdf5, demos, extra=()):
    """Add the given demos of the training view to an open LeRobotDataset, one episode per demo.

    Baseline frames come from robolab.p4_dataset_export.iter_policy_frames (the documented reader);
    extra fields are read from the same HDF5 demo group.
    """
    import h5py

    from robolab.p4_dataset_export import iter_policy_frames

    wanted = set(demos)
    episodes, current, buffered = [], None, 0
    with h5py.File(hdf5, 'r') as handle:
        case_to_demo = {handle['data'][name].attrs['case_id']: name for name in demo_names(handle)}
        wanted_cases = {handle['data'][name].attrs['case_id'] for name in wanted}
        extras = {}
        for frame in iter_policy_frames(hdf5, language=LANGUAGE, image_convention=IMAGE_CONVENTION):
            case = frame['episode']
            if case not in wanted_cases:
                if current is not None and len(episodes) == len(wanted_cases):
                    break   # demos are a prefix in file order: everything wanted has been read
                continue
            if case != current:
                if current is not None:
                    dataset.save_episode()
                    episodes[-1]['frames'] = buffered
                demo = handle['data'][case_to_demo[case]]
                extras = {name: EXTRA_FIELDS[name].read(demo) for name in extra}
                episodes.append(dict(episode_index=dataset.meta.total_episodes, case_id=case,
                                     demo=case_to_demo[case], task=frame['task']))
                current, buffered = case, 0
            row = dict(image=frame['image'], wrist_image=frame['wrist_image'], state=frame['state'],
                       actions=frame['actions'], task=frame['task'])
            for name, values in extras.items():
                row[name] = values[frame['frame_index']]
            dataset.add_frame(row)
            buffered += 1
        if current is not None:
            dataset.save_episode()
            episodes[-1]['frames'] = buffered
    return episodes


def convert(args):
    import h5py
    from lerobot.common.datasets.lerobot_dataset import CODEBASE_VERSION, LeRobotDataset

    home = lerobot_home()
    root = home/args.repo_id
    if root.exists():
        raise SystemExit(f'{root} already exists; this script never overwrites or deletes a dataset.')
    hdf5 = Path(args.hdf5).resolve()
    with h5py.File(hdf5, 'r') as handle:
        if handle.attrs['schema'] != 'robolab.p4_training_view.v1':
            raise SystemExit('Not a P4 training view')
        if int(handle.attrs['control_frequency_hz']) != FPS:
            raise SystemExit(f"Recording frequency {handle.attrs['control_frequency_hz']} Hz != fps {FPS}")
        demos = demo_names(handle)[:args.limit or None]
        features = features_for(handle, args.extra)
        source = {key: (value.decode() if isinstance(value, bytes) else
                        value.item() if hasattr(value, 'item') else value)
                  for key, value in handle.attrs.items() if key in ('schema', 'split', 'plan_sha256', 'source_index_sha256',
                                                                    'control_frequency_hz', 'image_convention')}
    print(json.dumps(dict(event='start', root=str(root), demos=len(demos), fps=FPS, image_convention=IMAGE_CONVENTION,
                          extra=list(args.extra))), flush=True)
    began = time.monotonic()
    dataset = LeRobotDataset.create(repo_id=args.repo_id, fps=FPS, root=root, robot_type=ROBOT_TYPE, features=features,
                                    use_videos=False, image_writer_threads=args.threads, image_writer_processes=0)
    try:
        episodes = write_episodes(dataset, hdf5, demos, args.extra)
    finally:
        dataset.stop_image_writer()
    manifest = dict(schema=SCHEMA, repo_id=args.repo_id, root=str(root), lerobot_codebase_version=CODEBASE_VERSION,
                    fps=FPS, image_convention=IMAGE_CONVENTION, language=LANGUAGE, robot_type=ROBOT_TYPE,
                    features={key: dict(value, shape=list(value['shape'])) for key, value in features.items()},
                    extra_fields=list(args.extra), source=dict(path=str(hdf5), sha256=sha256_file(hdf5), **source),
                    episodes=episodes, total_episodes=len(episodes), total_frames=sum(e['frames'] for e in episodes),
                    robolab_code=git_state(), elapsed_s=round(time.monotonic()-began, 1))
    (root/MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(event='converted', episodes=len(episodes), frames=manifest['total_frames'],
                          bytes=dir_bytes(root), elapsed_s=manifest['elapsed_s'])), flush=True)


def verify(args, root=None):
    """Second code path: read the LeRobot dataset back and compare every frame with the HDF5 directly."""
    import h5py
    from lerobot.common.datasets.lerobot_dataset import LeRobotDataset

    root = root or lerobot_home()/args.repo_id
    began = time.monotonic()
    dataset = LeRobotDataset(args.repo_id, root=root)
    raw = dataset.hf_dataset.with_format(None)   # PIL images straight from the embedded PNG bytes
    problems, checked_images = [], 0
    with h5py.File(Path(args.hdf5).resolve(), 'r') as handle:
        demos = demo_names(handle)[:args.limit or None] if not args.demos else args.demos
        expected_frames = sum(handle['data'][name]['actions'].shape[0] for name in demos)
        if dataset.num_episodes != len(demos):
            problems.append(f'episodes {dataset.num_episodes} != {len(demos)}')
        if dataset.num_frames != expected_frames:
            problems.append(f'frames {dataset.num_frames} != {expected_frames}')
        if dataset.fps != FPS:
            problems.append(f'fps {dataset.fps} != {FPS}')
        if dataset.meta.video_keys:
            problems.append(f'unexpected video keys {dataset.meta.video_keys}')
        starts = dataset.episode_data_index['from'].tolist()
        ends = dataset.episode_data_index['to'].tolist()
        for episode, name in enumerate(demos):
            demo = handle['data'][name]
            length = demo['actions'].shape[0]
            if ends[episode]-starts[episode] != length:
                problems.append(f'episode {episode} length {ends[episode]-starts[episode]} != {length}')
                continue
            task = demo.attrs['instruction_'+LANGUAGE]
            state = demo['obs/state'][()].astype(np.float32)
            actions = demo['actions'][()].astype(np.float32)
            images = {key: demo[path][()] for key, path in CAMERAS}
            for step in range(length):
                row = raw[starts[episode]+step]
                if int(row['episode_index']) != episode or int(row['frame_index']) != step:
                    problems.append(f'episode {episode} step {step}: index mismatch')
                if abs(float(row['timestamp'])-step/FPS) > 1e-6:
                    problems.append(f'episode {episode} step {step}: timestamp {row["timestamp"]}')
                if dataset.meta.tasks[int(row['task_index'])] != task:
                    problems.append(f'episode {episode} step {step}: task mismatch')
                if not np.array_equal(np.asarray(row['state'], np.float32), state[step]):
                    problems.append(f'episode {episode} step {step}: state mismatch')
                if not np.array_equal(np.asarray(row['actions'], np.float32), actions[step]):
                    problems.append(f'episode {episode} step {step}: actions mismatch')
                for key, _ in CAMERAS:
                    decoded = np.asarray(row[key])
                    expected = images[key][step][:, ::-1]   # canonical -> openpi LIBERO orientation
                    checked_images += 1
                    if decoded.dtype != np.uint8 or not np.array_equal(decoded, expected):
                        problems.append(f'episode {episode} step {step}: {key} pixels differ')
                if len(problems) > 20:
                    break
        # The training-time path (torch float in [0, 1]) must decode to the same pixels.
        for index in (0, dataset.num_frames//2, dataset.num_frames-1):
            item = dataset[index]
            for key, _ in CAMERAS:
                pixels = (item[key].numpy().transpose(1, 2, 0)*255).round().astype(np.uint8)
                if not np.array_equal(pixels, np.asarray(raw[index][key])):
                    problems.append(f'frame {index}: torch path {key} differs')
    report = dict(event='verified', ok=not problems, episodes=dataset.num_episodes, frames=dataset.num_frames,
                  images_compared=checked_images, fps=dataset.fps, tasks=len(dataset.meta.tasks),
                  bytes=dir_bytes(root), problems=problems[:20], elapsed_s=round(time.monotonic()-began, 1))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def append_test(args):
    """Can a dataset written earlier be reopened and extended? Built in a scratch root, never in outputs/."""
    import h5py
    from lerobot.common.datasets.lerobot_dataset import LeRobotDataset

    scratch = Path(args.scratch_root).expanduser().resolve()
    if scratch.exists() and any(scratch.iterdir()):
        raise SystemExit(f'{scratch} must be empty or absent')
    hdf5 = Path(args.hdf5).resolve()
    with h5py.File(hdf5, 'r') as handle:
        demos = demo_names(handle)[:3]
        features = features_for(handle)
    repo = 'robolab/append_test'
    root = scratch/repo
    first = LeRobotDataset.create(repo_id=repo, fps=FPS, root=root, robot_type=ROBOT_TYPE, features=features,
                                  use_videos=False)
    write_episodes(first, hdf5, demos[:2])
    del first
    reopened = LeRobotDataset(repo, root=root)          # a fresh process would do exactly this
    before = (reopened.num_episodes, reopened.num_frames)
    added = write_episodes(reopened, hdf5, demos[2:3])
    del reopened
    reference_root = scratch/'robolab/append_reference'
    reference = LeRobotDataset.create(repo_id='robolab/append_reference', fps=FPS, root=reference_root,
                                      robot_type=ROBOT_TYPE, features=features, use_videos=False)
    write_episodes(reference, hdf5, demos)
    del reference
    args.demos, args.limit, args.repo_id = demos, 0, repo
    report = verify(args, root=root)
    same_meta = {}
    for name in ('meta/info.json', 'meta/episodes.jsonl', 'meta/tasks.jsonl', 'meta/episodes_stats.jsonl'):
        left, right = (root/name).read_text(), (reference_root/name).read_text()
        same_meta[name] = left == right
    same_parquet = all((root/p.relative_to(reference_root)).read_bytes() == p.read_bytes()
                       for p in reference_root.rglob('*.parquet'))
    result = dict(event='append_test', before_append=dict(episodes=before[0], frames=before[1]),
                  appended_episode_index=added[0]['episode_index'], after=dict(episodes=report['episodes'],
                                                                              frames=report['frames']),
                  verify_ok=report['ok'], meta_identical_to_one_pass=same_meta,
                  parquet_identical_to_one_pass=same_parquet)
    print(json.dumps(result), flush=True)
    return result


MANIFEST_RUN_KEYS = ('robolab_code', 'repo_id', 'root', 'elapsed_s')   # differ between two runs of the same input


def compare(args):
    """Byte comparison of two converted datasets (data + LeRobot metadata); our manifest is compared field by
    field, ignoring only the run-specific keys."""
    home = lerobot_home()
    left, right = home/args.left, home/args.right
    files = lambda root: sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name != MANIFEST)  # noqa: E731
    left_files, right_files = files(left), files(right)
    differing = [name for name in sorted(set(left_files) & set(right_files))
                 if (left/name).read_bytes() != (right/name).read_bytes()]
    manifests = [json.loads((root/MANIFEST).read_text(encoding='utf-8')) for root in (left, right)]
    manifest_diff = sorted(key for key in set(manifests[0]) | set(manifests[1])
                           if manifests[0].get(key) != manifests[1].get(key))
    report = dict(event='compared', left=str(left), right=str(right), files=len(left_files),
                  only_left=sorted(set(left_files)-set(right_files)), only_right=sorted(set(right_files)-set(left_files)),
                  differing_files=differing, manifest_differing_keys=manifest_diff,
                  identical=not differing and left_files == right_files
                  and set(manifest_diff) <= set(MANIFEST_RUN_KEYS))
    print(json.dumps(report), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    cmp = sub.add_parser('compare', help='byte-compare two datasets under $HF_LEROBOT_HOME')
    cmp.add_argument('left')
    cmp.add_argument('right')
    for name in ('convert', 'verify', 'append-test'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--hdf5', required=True, help='P4 training view, e.g. outputs/p4d_lab/export/p4_osc_train.hdf5')
        if name != 'append-test':
            cmd.add_argument('--repo-id', required=True, help='dataset name under $HF_LEROBOT_HOME, e.g. robolab/p4d_v1_train')
            cmd.add_argument('--limit', type=int, default=0, help='first N demos only (0 = all)')
        else:
            cmd.add_argument('--scratch-root', required=True)
        if name == 'convert':
            cmd.add_argument('--extra', nargs='*', default=[], choices=sorted(EXTRA_FIELDS) or None,
                             help='extra per-frame fields from EXTRA_FIELDS (none defined yet)')
            cmd.add_argument('--threads', type=int, default=8, help='PNG writer threads')
    args = parser.parse_args(argv)
    args.demos = None
    if args.command == 'compare':
        return 0 if compare(args)['identical'] else 1
    if args.command == 'convert':
        convert(args)
    elif args.command == 'verify':
        return 0 if verify(args)['ok'] else 1
    else:
        return 0 if append_test(args)['verify_ok'] else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
