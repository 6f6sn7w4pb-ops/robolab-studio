"""openpi bridge service and the pi05 adapter with a fake policy: no GPU, JAX or openpi needed."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from robolab.p5_eval import AdapterPolicy, bridge_observation
from robolab.policy import HttpPolicy, adapter_catalog, create_vla_adapter

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'scripts'/'serve_openpi_bridge.py'


def load_bridge_module():
    spec = importlib.util.spec_from_file_location('serve_openpi_bridge', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load_bridge_module()

# Synthetic canonical image: top-left, top-right, bottom-left, bottom-right quadrant colors.
QUADRANTS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
CENTERS = ((56, 56), (56, 168), (168, 56), (168, 168))


def quadrant_image():
    image = np.zeros((224, 224, 3), np.uint8)
    for (row, col), color in zip(CENTERS, QUADRANTS):
        image[row-56:row+56, col-56:col+56] = color
    return image


def quadrant_colors(image):
    """Mean color of a 40x40 patch in the middle of each quadrant (robust to JPEG)."""
    return [image[row-20:row+20, col-20:col+20].reshape(-1, 3).mean(axis=0) for row, col in CENTERS]


def raw_observation(size=224, instruction='Pick up the object by its thick end.'):
    image = quadrant_image() if size == 224 else np.zeros((size, size, 3), np.uint8)
    return dict(tick=4, time_s=.2, instruction=instruction, images={'agentview': image, 'robot0_eye_in_hand': image},
                state=np.linspace(-.4, .4, 8), joints=np.zeros(7), calibration={},
                action_space='robosuite_1_4_1_panda_osc_pose_normalized_7d_v1')


def make_observation(sequence=1, size=224):
    """Exactly what the evaluation sends: p5_eval.bridge_observation (PIL JPEG, canonical top-left)."""
    return bridge_observation(raw_observation(size), sequence)


def post(endpoint, payload):
    request = Request(endpoint+'/predict', data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


class FakePolicy:
    """Stands in for openpi's Policy: records inputs, returns a fixed chunk, tracks concurrent calls."""
    def __init__(self, actions=None, delay=0.):
        self.actions = np.zeros((10, 7)) if actions is None else np.asarray(actions, dtype=float)
        self.delay = delay
        self.inputs = []
        self.active = self.max_active = 0
        self.guard = threading.Lock()

    def infer(self, inputs):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            self.inputs.append(inputs)
            return {'actions': self.actions.copy(), 'policy_timing': {'infer_ms': 0.}}
        finally:
            with self.guard:
                self.active -= 1


class ServerCase(unittest.TestCase):
    def serve(self, policy, *, test_backend=True):
        service = bridge.OpenpiBridge(policy, config_name='pi05_libero', checkpoint='/fixture/pi05_libero',
                                      test_backend=test_backend)
        server = bridge.make_server(service, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(stop)
        return service, f'http://127.0.0.1:{server.server_port}'


class BridgeProtocolTests(ServerCase):
    def test_health_fields(self):
        _, endpoint = self.serve(FakePolicy())
        with urlopen(endpoint+'/health', timeout=5) as response:
            health = json.loads(response.read())
        self.assertEqual({key: health[key] for key in ('service', 'protocol', 'action_space', 'family', 'model_loaded',
                                                       'config', 'checkpoint')},
                         dict(service='robolab-vla', protocol=1, action_space='libero_osc_7d', family='openpi',
                              model_loaded=True, config='pi05_libero', checkpoint='/fixture/pi05_libero'))
        self.assertTrue(health['test_backend'])
        self.assertEqual(HttpPolicy(endpoint).connect()['family'], 'openpi')

    def test_predict_round_trip_flips_images_and_passes_state_and_prompt(self):
        actions = np.linspace(-.9, .9, 70).reshape(10, 7)
        policy = FakePolicy(actions)
        _, endpoint = self.serve(policy)
        transport = HttpPolicy(endpoint, timeout=5)
        transport.connect()
        observation = make_observation(sequence=9)
        result = transport.predict(observation)
        np.testing.assert_allclose(result['actions'], actions)
        self.assertEqual(result['action_space'], 'libero_osc_7d')
        # A test backend never claims real inference.
        self.assertEqual((result['real_inference'], result['is_mock']), (False, True))
        self.assertEqual((result['observation_id'], result['episode_tick'], result['chunk_length']), (9, 4, 10))
        self.assertEqual((result['clipped_values'], result['max_excess']), (0, 0.))
        received = policy.inputs[-1]
        self.assertEqual(set(received), {'observation/image', 'observation/wrist_image', 'observation/state', 'prompt'})
        self.assertEqual(received['prompt'], observation['instruction'])
        np.testing.assert_array_equal(received['observation/state'], observation['proprio'])
        top_left, top_right, bottom_left, bottom_right = QUADRANTS
        for key in ('observation/image', 'observation/wrist_image'):
            image = received[key]
            self.assertEqual((image.shape, image.dtype), ((224, 224, 3), np.uint8))
            # Horizontal flip: left and right swap, top stays on top (not a vertical flip or a 180 deg turn).
            for got, want in zip(quadrant_colors(image), (top_right, top_left, bottom_right, bottom_left)):
                np.testing.assert_allclose(got, want, atol=6)

    def test_orientation_matches_openpi_libero_client(self):
        raw = np.arange(6*5*3, dtype=np.uint8).reshape(6, 5, 3)   # raw robosuite frame, bottom-left origin
        canonical = np.flipud(raw)                                 # what RoboLab's renderer returns
        openpi_client = raw[::-1, ::-1]                            # openpi examples/libero/main.py: rotate 180 deg
        np.testing.assert_array_equal(bridge.canonical_to_openpi_libero(canonical), openpi_client)

    def test_bound_actions_rules(self):
        clipped, count, excess = bridge.bound_actions([[0.]*6+[1.0499]])
        self.assertEqual((clipped[0][6], count), (1., 1))
        self.assertAlmostEqual(excess, .0499)
        for bad in ([[0.]*6+[1.05]], [[0.]*6+[-1.2]]):
            with self.subTest(value=bad), self.assertRaises(bridge.ActionRangeError):
                bridge.bound_actions(bad)
        for bad in ([[0.]*6], [[float('nan')]*7], np.zeros((33, 7)), np.zeros((0, 7))):
            with self.subTest(shape=np.shape(bad)), self.assertRaises(ValueError):
                bridge.bound_actions(bad)

    def test_small_excess_is_clipped_and_reported(self):
        actions = np.zeros((10, 7))
        actions[0, 6], actions[3, 6], actions[5, 0] = 1.01, -1.01, 1.004
        service, endpoint = self.serve(FakePolicy(actions))
        transport = HttpPolicy(endpoint, timeout=5)
        transport.connect()
        result = transport.predict(make_observation())
        self.assertEqual(result['clipped_values'], 3)
        self.assertAlmostEqual(result['max_excess'], .01, places=9)
        clipped = np.asarray(result['actions'])
        self.assertEqual((clipped[0, 6], clipped[3, 6], clipped[5, 0]), (1., -1., 1.))
        self.assertTrue(np.all(np.abs(clipped) <= 1.))   # the evaluation's strict range check passes
        stats = service.stats()
        self.assertEqual((stats['clipped_values'], stats['clipped_responses'], stats['errors']), (3, 1, 0))
        self.assertAlmostEqual(stats['max_excess'], .01, places=9)

    def test_large_excess_is_an_error_and_nothing_is_clipped(self):
        actions = np.zeros((10, 7))
        actions[2, 6] = 1.1
        service, endpoint = self.serve(FakePolicy(actions))
        response = post(endpoint, make_observation())
        self.assertEqual(response['error_kind'], 'action_out_of_range')
        self.assertNotIn('actions', response)
        self.assertAlmostEqual(response['max_excess'], .1, places=9)
        transport = HttpPolicy(endpoint, timeout=5)
        transport.connect()
        with self.assertRaisesRegex(ValueError, r'outside \[-1, 1\] by 0\.1000 at step 2, dim 6'):
            transport.predict(make_observation())
        stats = service.stats()
        self.assertEqual((stats['errors'], stats['error_kinds'], stats['clipped_values'], stats['predictions']),
                         (2, {'action_out_of_range': 2}, 0, 0))

    def test_concurrent_requests_are_serialized(self):
        policy = FakePolicy(delay=.05)
        _, endpoint = self.serve(policy)
        observation = make_observation()
        results, errors = [], []

        def worker():
            try:
                transport = HttpPolicy(endpoint, timeout=10)
                transport.connect()
                results.append(transport.predict(observation))
            except Exception as error:   # collected and asserted below
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((len(results), errors), (4, []))
        self.assertEqual(policy.max_active, 1)
        # The requests did overlap: at least one of them queued behind another inference.
        self.assertGreater(max(result['queue_wait_ms'] for result in results), 20.)

    def test_invalid_observations_are_reported_and_never_inferred(self):
        policy = FakePolicy()
        service, endpoint = self.serve(policy)
        wrong_size = make_observation(size=96)
        other_origin = make_observation()
        other_origin['images']['agentview']['origin'] = 'bottom_left'
        missing_camera = make_observation()
        del missing_camera['images']['robot0_eye_in_hand']
        for name, observation in (('size', wrong_size), ('origin', other_origin), ('camera', missing_camera)):
            with self.subTest(name):
                self.assertEqual(post(endpoint, observation)['error_kind'], 'invalid_observation')
        self.assertEqual(policy.inputs, [])
        self.assertEqual(service.stats()['error_kinds'], {'invalid_observation': 3})
        with self.assertRaises(HTTPError) as context:
            urlopen(endpoint+'/unknown', timeout=5)
        context.exception.close()
        self.assertEqual(context.exception.code, 404)

    def test_request_log_has_one_line_per_request(self):
        actions = np.zeros((10, 7))
        actions[0, 6] = 1.02
        log = io.StringIO()
        service = bridge.OpenpiBridge(FakePolicy(actions), config_name='pi05_libero', checkpoint='/fixture',
                                      test_backend=True, request_log=log)
        service.predict(make_observation(sequence=3))
        service.predict({'schema': 'something else'})
        lines = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertEqual([(line['observation_id'], line['error_kind'], line['clipped_values']) for line in lines],
                         [(3, None, 1), (None, 'invalid_observation', 0)])

    def test_module_import_needs_no_model_runtime(self):
        code = ('import importlib.util, sys; spec = importlib.util.spec_from_file_location("b", sys.argv[1]); '
                'module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); '
                'loaded = {"jax", "openpi", "torch", "tensorflow"} & set(sys.modules); assert not loaded, loaded')
        process = subprocess.run([sys.executable, '-c', code, str(SCRIPT)], capture_output=True, text=True,
                                 timeout=30, cwd=ROOT)
        self.assertEqual(process.returncode, 0, process.stderr)


class Pi05AdapterTests(ServerCase):
    def test_registered_with_english_label(self):
        entry = next(item for item in adapter_catalog() if item['id'] == 'pi05')
        catalog = json.loads((ROOT/'robolab'/'locales'/'en.json').read_text(encoding='utf-8'))
        self.assertIn(entry['label'], catalog)

    def test_gui_signature_without_endpoint(self):
        adapter = create_vla_adapter('pi05', checkpoint='', endpoint='')   # engine.configure_policy's call
        self.assertEqual((adapter.status()['stage'], adapter.status()['model_loaded']), ('configured', False))
        with self.assertRaises(ValueError):
            adapter.load()

    def test_test_backend_is_never_promoted_to_a_real_model(self):
        _, endpoint = self.serve(FakePolicy(), test_backend=True)
        adapter = create_vla_adapter('pi05', endpoint=endpoint, timeout=5)
        with self.assertRaises(ValueError):
            adapter.load()
        status = adapter.status()
        self.assertEqual((status['family'], status['model_loaded'], status['real_inference_verified']),
                         ('openpi', False, False))
        with self.assertRaises(ValueError):
            AdapterPolicy('pi05', endpoint=endpoint, timeout=5)

    def test_loaded_service_marks_evaluation_as_vla(self):
        # A fake standing in for a loaded model: the adapter can only trust what the service reports.
        _, endpoint = self.serve(FakePolicy(), test_backend=False)
        policy = AdapterPolicy('pi05', endpoint=endpoint, timeout=5)
        self.assertTrue(policy.is_vla)
        status = policy.adapter.status()
        self.assertEqual((status['real_model'], status['model_loaded'], status['service_config'], status['checkpoint']),
                         (True, True, 'pi05_libero', '/fixture/pi05_libero'))
        chunk = policy.act(raw_observation())
        self.assertEqual(np.asarray(chunk).shape, (10, 7))
        self.assertTrue(policy.adapter.status()['real_inference_verified'])
        self.assertEqual(policy.last_response, {'is_mock': False, 'real_inference': True, 'source': 'openpi:pi05_libero'})
        policy.close()
        self.assertFalse(policy.adapter.status()['model_loaded'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
