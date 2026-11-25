"""openpi bridge service and the pi05 adapter with a fake policy: no GPU, JAX or openpi needed."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from robolab.p5_eval import AdapterPolicy, bridge_observation, make_policy, policy_spec
from robolab.policy import HttpPolicy, adapter_catalog, create_vla_adapter, validate_observation

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


def make_observation(sequence=1, size=224, image_encoding='png'):
    """Exactly what the evaluation sends: p5_eval.bridge_observation (PIL PNG by default, canonical top-left)."""
    return bridge_observation(raw_observation(size), sequence, image_encoding)


def textured_observation(seed=0, tick=4, instruction='Pick up the object by its thin end.'):
    """Random pixels: any lossy step in the image path shows up as a pixel difference."""
    rng = np.random.default_rng(seed)
    images = {name: rng.integers(0, 256, (224, 224, 3), dtype=np.uint8) for name in ('agentview', 'robot0_eye_in_hand')}
    return dict(raw_observation(), tick=tick, instruction=instruction, images=images)


def post(endpoint, payload):
    request = Request(endpoint+'/predict', data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


class FakePolicy:
    """Stands in for openpi's Policy: records inputs and noise, returns a fixed chunk, tracks concurrent calls."""
    def __init__(self, actions=None, delay=0.):
        self.actions = np.zeros((10, 7)) if actions is None else np.asarray(actions, dtype=float)
        self.delay = delay
        self.inputs = []
        self.noises = []
        self.active = self.max_active = 0
        self.guard = threading.Lock()

    def infer(self, inputs, *, noise=None):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            self.inputs.append(inputs)
            self.noises.append(noise)
            return {'actions': self.actions.copy(), 'policy_timing': {'infer_ms': 0.}}
        finally:
            with self.guard:
                self.active -= 1


class ServerCase(unittest.TestCase):
    def serve(self, policy, *, test_backend=True, **options):
        service = bridge.OpenpiBridge(policy, config_name='pi05_libero', checkpoint='/fixture/pi05_libero',
                                      test_backend=test_backend, **options)
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


class ImageEncodingTests(ServerCase):
    """The model must receive the training-view pixels: lossless PNG, flipped the openpi LIBERO way."""

    def test_png_reaches_the_model_pixel_for_pixel(self):
        raw = textured_observation()
        inputs = bridge.openpi_inputs(bridge_observation(raw, 1, 'png'))
        for camera, key in bridge.CAMERA_KEYS:
            np.testing.assert_array_equal(inputs[key], raw['images'][camera][:, ::-1])
        # The old JPEG path (quality 95) changes pixels; that is why the evaluation moved to PNG.
        jpeg = bridge.openpi_inputs(bridge_observation(raw, 1, 'jpeg'))
        self.assertGreater(int(np.abs(jpeg['observation/image'].astype(int)
                                      - raw['images']['agentview'][:, ::-1].astype(int)).max()), 0)

    def test_png_encoding_is_deterministic(self):
        raw = textured_observation()
        self.assertEqual(bridge_observation(raw, 1, 'png')['images'], bridge_observation(raw, 2, 'png')['images'])

    def test_observation_accepts_png_and_jpeg_only(self):
        for encoding in ('png', 'jpeg'):
            observation = make_observation(image_encoding=encoding)
            self.assertEqual(observation['images']['agentview']['encoding'], encoding+'_base64')
            validate_observation(observation)
        mislabelled = make_observation(image_encoding='jpeg')
        mislabelled['images']['agentview']['encoding'] = 'png_base64'
        unknown = make_observation()
        unknown['images']['agentview']['encoding'] = 'raw_base64'
        for name, observation in (('mislabelled', mislabelled), ('unknown', unknown)):
            with self.subTest(name), self.assertRaises(ValueError):
                validate_observation(observation)

    def test_evaluation_sends_the_encoding_its_spec_names(self):
        policy = FakePolicy()
        _, endpoint = self.serve(policy, test_backend=False)
        raw = textured_observation()
        expected = raw['images']['agentview'][:, ::-1]
        current = policy_spec('adapter:pi05', endpoint, 5.)
        legacy = {key: value for key, value in current.items() if key != 'image_encoding'}   # pre-2026-11-25 config
        for spec, encoding, exact in ((current, 'png', True), (legacy, 'jpeg', False)):
            with self.subTest(encoding):
                evaluation_policy = make_policy(spec)
                self.addCleanup(evaluation_policy.close)
                self.assertEqual((evaluation_policy.image_encoding, evaluation_policy.describe()['image_encoding']),
                                 (encoding, encoding))
                evaluation_policy.act(raw)
                received = policy.inputs[-1]['observation/image']
                self.assertEqual(bool(np.array_equal(received, expected)), exact)


class SeededNoiseTests(ServerCase):
    """Seeded noise depends on the observation only, never on request order."""
    SHAPE = (10, 32)

    def test_same_observation_gets_the_same_noise_in_any_order(self):
        first, second = make_observation(1), bridge_observation(textured_observation(), 2)
        runs = []
        for order in ((first, second), (second, first)):
            policy = FakePolicy()
            service = bridge.OpenpiBridge(policy, config_name='pi05_libero', checkpoint='/fixture', test_backend=True,
                                          noise='seeded', noise_shape=self.SHAPE)
            responses = [service.predict(observation) for observation in order]
            runs.append({response['observation_id']: (response['noise_key'], noise)
                         for response, noise in zip(responses, policy.noises)})
        for observation_id in (1, 2):
            (key_a, noise_a), (key_b, noise_b) = runs[0][observation_id], runs[1][observation_id]
            self.assertEqual(key_a, key_b)
            self.assertEqual(len(key_a), 16)
            self.assertEqual((noise_a.shape, noise_a.dtype), (self.SHAPE, np.float32))
            np.testing.assert_array_equal(noise_a, noise_b)
        self.assertFalse(np.array_equal(runs[0][1][1], runs[0][2][1]))

    def test_every_input_and_the_seed_change_the_key(self):
        base = bridge.openpi_inputs(make_observation())
        reference = bridge.noise_key(base, 4, 0)
        self.assertEqual(reference, bridge.noise_key(bridge.openpi_inputs(make_observation()), 4, 0))
        prompt = dict(base, prompt=base['prompt']+' ')
        state = dict(base, **{'observation/state': base['observation/state']+1e-9})
        image = dict(base, **{'observation/image': base['observation/image'].copy()})
        image['observation/image'][0, 0, 0] ^= 1
        wrist = dict(base, **{'observation/wrist_image': base['observation/wrist_image'].copy()})
        wrist['observation/wrist_image'][223, 223, 2] ^= 1
        variants = dict(tick=bridge.noise_key(base, 5, 0), seed=bridge.noise_key(base, 4, 1),
                        prompt=bridge.noise_key(prompt, 4, 0), state=bridge.noise_key(state, 4, 0),
                        image=bridge.noise_key(image, 4, 0), wrist=bridge.noise_key(wrist, 4, 0))
        for name, key in variants.items():
            with self.subTest(name):
                self.assertNotEqual(key, reference)
        self.assertEqual(len(set(variants.values())), len(variants))

    def test_seeded_noise_is_standard_normal_and_repeatable(self):
        key = bridge.noise_key(bridge.openpi_inputs(make_observation()), 4, 0)
        noise = bridge.seeded_noise(key, self.SHAPE)
        np.testing.assert_array_equal(noise, bridge.seeded_noise(key, self.SHAPE))
        many = np.concatenate([bridge.seeded_noise(f'{index:064x}', self.SHAPE).ravel() for index in range(50)])
        self.assertLess(abs(float(many.mean())), .05)
        self.assertLess(abs(float(many.std())-1.), .05)

    def test_policy_mode_leaves_noise_to_openpi(self):
        policy = FakePolicy()
        service, endpoint = self.serve(policy)
        transport = HttpPolicy(endpoint, timeout=5)
        transport.connect()
        result = transport.predict(make_observation())
        self.assertNotIn('noise_key', result)
        self.assertEqual(policy.noises, [None])
        self.assertEqual(service.health()['noise'], dict(mode='policy', scheme=None, seed=None, shape=None))

    def test_health_and_request_log_report_seeded_noise(self):
        log = io.StringIO()
        service = bridge.OpenpiBridge(FakePolicy(), config_name='pi05_libero', checkpoint='/fixture', test_backend=True,
                                      noise='seeded', noise_seed=7, noise_shape=self.SHAPE, request_log=log)
        self.assertEqual(service.health()['noise'],
                         dict(mode='seeded', scheme=bridge.NOISE_SCHEME, seed=7, shape=[10, 32]))
        response = service.predict(make_observation(3))
        line = json.loads(log.getvalue())
        self.assertEqual((line['observation_id'], line['noise_key']), (3, response['noise_key']))

    def test_bad_noise_settings_are_rejected(self):
        for options in (dict(noise='seeded'), dict(noise='seeded', noise_shape=(10,)), dict(noise='random')):
            with self.subTest(options), self.assertRaises(ValueError):
                bridge.OpenpiBridge(FakePolicy(), config_name='x', checkpoint='/fixture', **options)

    def test_warm_up_compiles_the_call_that_requests_will_make(self):
        example = bridge.openpi_inputs(make_observation())
        policy = FakePolicy()
        seeded = bridge.warm_up(policy, example, self.SHAPE)
        plain = bridge.warm_up(policy, example)
        self.assertEqual((seeded['with_noise'], plain['with_noise']), (True, False))
        self.assertEqual((policy.noises[0].shape, policy.noises[0].dtype), (self.SHAPE, np.float32))
        self.assertIsNone(policy.noises[1])


FAKE_TRAINING_CONFIGS = '''
CONFIG_NAME = 'pi05_robolab_lora'


def make_config():
    return dict(name=CONFIG_NAME, model='pi05 + LoRA')


def register(config):
    return dict(config, registered=True)
'''


class ConfigRegistrationTests(unittest.TestCase):
    """Our LoRA configs live outside openpi; the bridge registers them before loading a checkpoint."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.module = self.directory/'openpi_robolab.py'
        self.module.write_text(FAKE_TRAINING_CONFIGS)

    def test_robolab_config_is_built_and_registered(self):
        config = bridge.register_robolab_config('pi05_robolab_lora', {}, self.module)
        self.assertEqual(config, dict(name='pi05_robolab_lora', model='pi05 + LoRA', registered=True))

    def test_a_registered_config_is_reused(self):
        existing = object()
        self.assertIs(bridge.register_robolab_config('pi05_robolab_lora', {'pi05_robolab_lora': existing},
                                                     self.module), existing)

    def test_unknown_config_or_missing_script_is_refused(self):
        with self.assertRaises(SystemExit):
            bridge.register_robolab_config('pi05_robolab_typo', {}, self.module)
        with self.assertRaises(SystemExit):
            bridge.register_robolab_config('pi05_robolab_lora', {}, self.directory/'missing.py')

    @unittest.skipUnless(bridge.ROBOLAB_CONFIGS.is_file(), 'scripts/openpi_robolab.py not in this checkout')
    def test_training_script_offers_what_the_bridge_calls(self):
        spec = importlib.util.spec_from_file_location('openpi_robolab_check', bridge.ROBOLAB_CONFIGS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)   # no openpi import at module level
        self.assertEqual(module.CONFIG_NAME, 'pi05_robolab_lora')
        self.assertTrue(callable(module.make_config) and callable(module.register))


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

    def test_episode_records_how_the_service_draws_noise(self):
        service, endpoint = self.serve(FakePolicy(), test_backend=False, noise='seeded', noise_shape=(10, 32))
        policy = AdapterPolicy('pi05', endpoint=endpoint, timeout=5)
        self.addCleanup(policy.close)
        self.assertEqual(policy.describe()['adapter_status']['service_noise'], service.health()['noise'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
