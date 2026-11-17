"""Dataset fingerprint: detects physics and image differences, ignores machine paths (stdlib only)."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dataset_fingerprint import check, write  # noqa: E402


def make_dataset(root, *, action_change=None, image_change=None, asset_path='/home/a/LIBERO'):
    (root/'episodes').mkdir(parents=True)
    (root/'result.json').write_text(json.dumps(dict(plan_sha256='p', seed=1)))
    for case in ('box-000_thick', 'box-000_thin'):
        directory = root/'episodes'/case
        (directory/'rgb').mkdir(parents=True)
        (directory/'model.xml').write_text(f'<mujoco><mesh file="{asset_path}/x.stl"/></mujoco>')
        actions, frames = [], []
        for tick in range(5):
            value = 1. if (case, tick) == action_change else 0.
            actions.append(json.dumps(dict(tick=tick, action=[value]*7)))
            files = {}
            for camera in ('agentview', 'robot0_eye_in_hand'):
                name = f'rgb/{tick:06d}_{camera}.png'
                pixel = b'B' if (case, tick, camera) == image_change else b'A'
                (directory/name).write_bytes(b'png'+pixel+bytes([tick]))
                files[camera] = dict(file=name)
            frames.append(json.dumps(dict(images=files)))
        (directory/'actions_and_contacts.jsonl').write_text('\n'.join(actions)+'\n')
        (directory/'frames.jsonl').write_text('\n'.join(frames)+'\n')


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_dataset(self.root/'laptop')
        write(self.root/'laptop', self.root/'fp.json.gz')

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_data_on_another_path_is_identical(self):
        make_dataset(self.root/'lab', asset_path='/home/lab/other/LIBERO')
        self.assertEqual(check(self.root/'lab', self.root/'fp.json.gz')['verdict'], 'identical')

    def test_image_only_difference(self):
        make_dataset(self.root/'lab', image_change=('box-000_thin', 3, 'agentview'))
        result = check(self.root/'lab', self.root/'fp.json.gz')
        self.assertEqual(result['verdict'], 'images_differ_physics_identical')
        row = next(r for r in result['episodes'] if r['case_id'] == 'box-000_thin')
        self.assertEqual((row['first_image_difference_tick'], row['differing_images']), (3, 1))

    def test_physics_difference(self):
        make_dataset(self.root/'lab', action_change=('box-000_thick', 2))
        result = check(self.root/'lab', self.root/'fp.json.gz')
        self.assertEqual(result['verdict'], 'physics_differs')
        row = next(r for r in result['episodes'] if r['case_id'] == 'box-000_thick')
        self.assertEqual(row['first_physics_difference_tick'], 2)

    def test_missing_episode(self):
        make_dataset(self.root/'lab')
        for path in (self.root/'lab'/'episodes'/'box-000_thin').rglob('*'):
            if path.is_file():
                path.unlink()
        result = check(self.root/'lab', self.root/'fp.json.gz')
        self.assertEqual((result['verdict'], result['summary']['missing']), ('physics_differs', 1))


if __name__ == '__main__':
    unittest.main()
