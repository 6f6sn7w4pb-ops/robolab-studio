"""openpi (pi0.5) inference service speaking RoboLab's VLA bridge protocol v1.

Runs in openpi's own environment (Python 3.11 + JAX), never in the simulation backend. Example:

  env -u VIRTUAL_ENV XLA_PYTHON_CLIENT_PREALLOCATE=false \\
    ~/projects/openpi/.venv/bin/python scripts/serve_openpi_bridge.py \\
    --config pi05_libero --checkpoint ~/.cache/openpi/openpi-assets/checkpoints/pi05_libero --port 8000

The checkpoint must already be a local directory; this service never downloads anything. A checkpoint trained
with RoboLab's LoRA config is served the same way with --config pi05_robolab_lora and its step directory, e.g.
outputs/openpi_checkpoints/pi05_robolab_lora/<exp>/<step>; that config is not part of openpi, so it is first
registered from scripts/openpi_robolab.py, as training does. The checkpoint carries its own norm stats.

Protocol (the client is robolab.policy.HttpPolicy, used by the 'pi05' VlaAdapter):
  GET  /health   service, protocol=1, action_space=libero_osc_7d, family=openpi, model_loaded, config, checkpoint,
                 noise (mode, scheme, seed, shape)
  GET  /stats    counters since start: requests, errors, clipping, inference and queue latency
  POST /predict  robolab.observation.v1 -> actions (N x 7), action_space, real_inference, is_mock, clipping report,
                 noise_key (seeded mode: first 16 hex digits of the request's noise key)

Input conversion follows openpi's examples/libero/main.py, the client pi05_libero was trained with:
  - Both images (PNG from the P5 evaluation since 2026-11-25, JPEG from older runs and the GUI) decode to
    224x224x3 uint8. RoboLab images are canonical (top-left origin), i.e. flipud(raw robosuite frame). The
    openpi client rotates the raw frame by 180 degrees, and raw[::-1, ::-1] == canonical[:, ::-1], so the
    canonical image is flipped horizontally.
  - observation/state is RoboLab's 8-D proprio (eef position, axis-angle of the eef quaternion, two gripper
    qpos), the openpi LIBERO definition. pi05_libero itself does not read it: pi0.5 adds no state token and
    the config sets discrete_state_input=False.
  - prompt is the observation's instruction.

Output: openpi's unnormalized LIBERO actions. Values less than CLIP_TOLERANCE outside [-1, 1] are clipped
and reported in the response; anything further out is returned as an error and nothing is clipped. Errors
the evaluation should record verbatim come back as a JSON 'error' field with HTTP 200, because that is what
HttpPolicy reads; malformed HTTP requests get 400/404.

One model on one GPU: inference is serialized with a lock, and concurrent requests wait their turn.

Flow-matching noise (--noise). openpi samples each action chunk from Gaussian noise. Left to itself
(--noise policy) the Policy splits a fresh key per request from one start key, so the noise depends on
the order requests arrive in; with several evaluation workers that order changes from run to run. The
default (--noise seeded) passes the noise explicitly instead: SHA-256 over the scheme name, --noise-seed,
the episode tick, the prompt, the 8-D state and both images as the model receives them seeds a NumPy
generator for an (action_horizon, action_dim) standard-normal array. The same observation therefore always
gets the same noise, whatever else the service is doing; a different --noise-seed draws a different,
equally reproducible sample (NumPy's default generator; openpi's lock file pins the NumPy version, so the
draw is stable for a given openpi environment). Whether whole episodes repeat byte for byte also needs the
GPU computation to be deterministic, which the evaluation's --recheck shows.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
if __name__ == '__main__':
    # openpi's interpreter must not leave cpython-311 caches inside the RoboLab tree.
    sys.dont_write_bytecode = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from robolab.policy import validate_observation  # noqa: E402  (stdlib-only module)

SERVICE = 'robolab-vla'
PROTOCOL = 1
ACTION_SPACE = 'libero_osc_7d'
FAMILY = 'openpi'
IMAGE_SIZE = 224
ACTION_DIM = 7
MAX_CHUNK = 32
CLIP_TOLERANCE = 0.05
MAX_REQUEST_BYTES = 5_000_000
CAMERA_KEYS = (('agentview', 'observation/image'), ('robot0_eye_in_hand', 'observation/wrist_image'))
IMAGE_CONVENTION = 'openpi_libero_rotated_180 = canonical[:, ::-1]'
ROBOLAB_CONFIGS = ROOT/'scripts'/'openpi_robolab.py'   # our LoRA training configs, registered at run time
NOISE_MODES = ('seeded', 'policy')
NOISE_SCHEME = 'robolab-openpi-noise-v1'
NOISE_INPUTS = ('observation/state', 'observation/image', 'observation/wrist_image')


class ActionRangeError(ValueError):
    def __init__(self, message, max_excess):
        super().__init__(message)
        self.max_excess = max_excess


def canonical_to_openpi_libero(rgb):
    """Canonical (top-left origin) image -> openpi LIBERO training orientation (raw frame rotated 180 deg)."""
    return np.ascontiguousarray(rgb[:, ::-1])


def decode_image(entry, camera):
    from PIL import Image

    raw = base64.b64decode(entry['data'], validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        rgb = np.asarray(image.convert('RGB'), dtype=np.uint8)
    if rgb.shape != (IMAGE_SIZE, IMAGE_SIZE, 3):
        raise ValueError(f'{camera} must be a {IMAGE_SIZE}x{IMAGE_SIZE} RGB image, got shape {list(rgb.shape)}')
    return rgb


def openpi_inputs(observation):
    """robolab.observation.v1 -> the input dict of openpi's LIBERO policy (LiberoInputs keys)."""
    validate_observation(observation)
    inputs = {}
    for camera, key in CAMERA_KEYS:
        entry = observation['images'][camera]
        if entry.get('origin', 'top_left') != 'top_left':
            raise ValueError(f'{camera}: only canonical top-left-origin images are accepted')
        inputs[key] = canonical_to_openpi_libero(decode_image(entry, camera))
    inputs['observation/state'] = np.asarray(observation['proprio'], dtype=np.float64)
    inputs['prompt'] = observation['instruction']
    return inputs


def noise_key(inputs, episode_tick, seed=0):
    """SHA-256 hex digest of what fixes one request's noise: scheme, seed, tick, prompt, state and images.

    Hashes the arrays the model receives (after decoding and flipping), so the key does not depend on how
    the images travelled, only on their pixels.
    """
    digest = hashlib.sha256()
    digest.update(f'{NOISE_SCHEME}|seed={int(seed)}|tick={episode_tick}|prompt='.encode())
    digest.update(inputs['prompt'].encode('utf-8'))
    for key in NOISE_INPUTS:
        array = np.ascontiguousarray(inputs[key])
        digest.update(f'|{key}|{array.dtype.str}|{array.shape}|'.encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def seeded_noise(key, shape):
    """Standard-normal float32 noise of the model's (action_horizon, action_dim) shape, seeded by noise_key."""
    return np.random.default_rng(int(key, 16)).standard_normal(tuple(shape), dtype=np.float32)


def bound_actions(actions, tolerance=CLIP_TOLERANCE):
    """(N, 7) chunk -> (list clipped to [-1, 1], clipped value count, max excess beyond 1).

    Raises ActionRangeError, without clipping anything, when a value is tolerance or more outside [-1, 1].
    """
    array = np.asarray(actions, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != ACTION_DIM or not 1 <= array.shape[0] <= MAX_CHUNK:
        raise ValueError(f'expected an (N, {ACTION_DIM}) action chunk with 1 <= N <= {MAX_CHUNK}, '
                         f'got shape {list(array.shape)}')
    if not np.isfinite(array).all():
        raise ValueError('the model returned non-finite action values')
    excess = np.abs(array)-1.
    max_excess = max(float(excess.max()), 0.)
    if max_excess >= tolerance:
        step, dim = (int(i) for i in np.unravel_index(int(np.argmax(excess)), excess.shape))
        raise ActionRangeError(f'action outside [-1, 1] by {max_excess:.4f} at step {step}, dim {dim} '
                               f'(value {array[step, dim]:.4f}); clipping tolerance is {tolerance}', max_excess)
    return np.clip(array, -1., 1.).tolist(), int((excess > 0).sum()), max_excess


class OpenpiBridge:
    """Request handling around any object with infer(dict, *, noise=None) -> {'actions': (N, 7)}: an openpi
    Policy, or a test fake (test_backend=True, which never claims real inference).

    noise='seeded' passes infer() noise derived from the observation (noise_key); it needs the model's
    noise_shape. noise='policy' leaves the noise to the Policy's own key sequence (order dependent).
    """

    def __init__(self, policy, *, config_name, checkpoint, test_backend=False, tolerance=CLIP_TOLERANCE,
                 request_log=None, noise='policy', noise_seed=0, noise_shape=None):
        if noise not in NOISE_MODES:
            raise ValueError('noise must be one of '+', '.join(NOISE_MODES))
        if noise == 'seeded' and (noise_shape is None or len(noise_shape) != 2):
            raise ValueError("noise='seeded' needs the model's (action_horizon, action_dim) noise_shape")
        self.policy = policy
        self.config_name = config_name
        self.checkpoint = str(checkpoint)
        self.test_backend = bool(test_backend)
        self.tolerance = float(tolerance)
        self.request_log = request_log
        self.noise = noise
        self.noise_seed = int(noise_seed)
        self.noise_shape = None if noise_shape is None else tuple(int(n) for n in noise_shape)
        self.inference_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.started = time.time()
        self.counters = dict(requests=0, predictions=0, errors=0, error_kinds={}, clipped_values=0,
                             clipped_responses=0, max_excess=0., inference_ms_total=0., inference_ms_max=0.,
                             wait_ms_total=0., wait_ms_max=0.)

    def noise_description(self):
        if self.noise == 'policy':
            return dict(mode='policy', scheme=None, seed=None, shape=None)
        return dict(mode='seeded', scheme=NOISE_SCHEME, seed=self.noise_seed, shape=list(self.noise_shape))

    def health(self):
        return dict(service=SERVICE, protocol=PROTOCOL, action_space=ACTION_SPACE, family=FAMILY,
                    model_loaded=True, test_backend=self.test_backend, config=self.config_name,
                    checkpoint=self.checkpoint, clip_tolerance=self.tolerance, image_convention=IMAGE_CONVENTION,
                    noise=self.noise_description())

    def stats(self):
        with self.stats_lock:
            counters = json.loads(json.dumps(self.counters))
        done = counters['predictions']
        counters.update(uptime_s=round(time.time()-self.started, 3), config=self.config_name,
                        mean_inference_ms=round(counters['inference_ms_total']/done, 3) if done else None,
                        mean_wait_ms=round(counters['wait_ms_total']/done, 3) if done else None)
        return counters

    def _record(self, entry, *, error_kind=None, clipped=0, max_excess=0., inference_ms=None, wait_ms=None):
        with self.stats_lock:
            counters = self.counters
            counters['requests'] += 1
            counters['max_excess'] = max(counters['max_excess'], max_excess)
            if inference_ms is not None:
                counters['inference_ms_total'] += inference_ms
                counters['inference_ms_max'] = max(counters['inference_ms_max'], inference_ms)
                counters['wait_ms_total'] += wait_ms
                counters['wait_ms_max'] = max(counters['wait_ms_max'], wait_ms)
            if error_kind is None:
                counters['predictions'] += 1
                counters['clipped_values'] += clipped
                counters['clipped_responses'] += bool(clipped)
            else:
                counters['errors'] += 1
                counters['error_kinds'][error_kind] = counters['error_kinds'].get(error_kind, 0)+1
            if self.request_log is not None:
                self.request_log.write(json.dumps(dict(entry, t=round(time.time(), 3), error_kind=error_kind,
                                                       clipped_values=clipped, max_excess=max_excess,
                                                       inference_ms=inference_ms, wait_ms=wait_ms)) + '\n')
                self.request_log.flush()

    def predict(self, observation):
        identity = dict(observation_id=observation.get('observation_id') if isinstance(observation, dict) else None,
                        episode_tick=observation.get('episode_tick') if isinstance(observation, dict) else None)
        try:
            inputs = openpi_inputs(observation)
        except Exception as error:
            self._record(identity, error_kind='invalid_observation')
            return dict(identity, error=f'invalid observation: {error}', error_kind='invalid_observation')
        noise = None
        if self.noise == 'seeded':
            key = noise_key(inputs, identity['episode_tick'], self.noise_seed)
            noise = seeded_noise(key, self.noise_shape)
            identity['noise_key'] = key[:16]   # first 64 bits, enough to tell requests apart in logs
        queued = time.perf_counter()
        try:
            with self.inference_lock:
                began = time.perf_counter()
                output = self.policy.infer(inputs) if noise is None else self.policy.infer(inputs, noise=noise)
                ended = time.perf_counter()
        except Exception as error:
            self._record(identity, error_kind='inference_error')
            return dict(identity, error=f'inference failed: {type(error).__name__}: {error}',
                        error_kind='inference_error')
        inference_ms, wait_ms = (ended-began)*1000, (began-queued)*1000
        try:
            actions, clipped, max_excess = bound_actions(output['actions'], self.tolerance)
        except ActionRangeError as error:
            self._record(identity, error_kind='action_out_of_range', max_excess=error.max_excess,
                         inference_ms=inference_ms, wait_ms=wait_ms)
            return dict(identity, error=str(error), error_kind='action_out_of_range', max_excess=error.max_excess)
        except Exception as error:
            self._record(identity, error_kind='invalid_model_output', inference_ms=inference_ms, wait_ms=wait_ms)
            return dict(identity, error=f'invalid model output: {error}', error_kind='invalid_model_output')
        self._record(identity, clipped=clipped, max_excess=max_excess, inference_ms=inference_ms, wait_ms=wait_ms)
        return dict(identity, actions=actions, action_space=ACTION_SPACE, real_inference=not self.test_backend,
                    is_mock=self.test_backend, source=f'{FAMILY}:{self.config_name}', chunk_length=len(actions),
                    clipped_values=clipped, max_excess=max_excess, inference_ms=round(inference_ms, 3),
                    queue_wait_ms=round(wait_ms, 3))


def make_server(bridge, host='127.0.0.1', port=8000):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, value, status=200):
            body = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                self.reply(bridge.health())
            elif self.path == '/stats':
                self.reply(bridge.stats())
            else:
                self.reply({'error': 'not found'}, 404)

        def do_POST(self):
            if self.path != '/predict':
                self.reply({'error': 'not found'}, 404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_REQUEST_BYTES:
                self.reply({'error': 'invalid request size'}, 400)
                return
            try:
                observation = json.loads(self.rfile.read(length))
            except ValueError:
                self.reply({'error': 'request body is not JSON'}, 400)
                return
            self.reply(bridge.predict(observation))

        def log_message(self, *args):
            pass  # requests are counted in /stats and optionally written to --request-log

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def register_robolab_config(config_name, registry, module_path=ROBOLAB_CONFIGS):
    """Add a RoboLab training config to openpi's config registry so its checkpoints can be served.

    openpi looks configs up in openpi.training.config._CONFIGS_DICT. Our LoRA configs are not in openpi's source:
    scripts/openpi_robolab.py builds them (make_config) and adds them at run time (register) for training, and
    serving a checkpoint trained with one needs the same config. Configs openpi already has (pi05_libero) never
    get here. The checkpoint carries its own norm stats, so only the model and data settings matter.
    """
    import importlib.util

    if not Path(module_path).is_file():
        raise SystemExit(f'{config_name} is not an openpi config and {module_path} is missing')
    spec = importlib.util.spec_from_file_location('openpi_robolab', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    known = getattr(module, 'CONFIG_NAME', None)
    if config_name != known:
        raise SystemExit(f'{config_name} is neither an openpi config nor the RoboLab config {known!r} '
                         f'defined in {module_path}')
    if config_name in registry:
        return registry[config_name]
    return module.register(module.make_config())   # openpi_robolab.register adds it to openpi's registry


def load_openpi_policy(config_name, checkpoint):
    from openpi.policies import policy_config
    from openpi.training import config as openpi_config

    if config_name not in openpi_config._CONFIGS_DICT:
        register_robolab_config(config_name, openpi_config._CONFIGS_DICT)
    config = openpi_config.get_config(config_name)
    if not isinstance(config.data, openpi_config.LeRobotLiberoDataConfig):
        raise SystemExit(f'{config_name} is not a LIBERO-format openpi config; this bridge serves {ACTION_SPACE} only')
    return config, policy_config.create_trained_policy(config, checkpoint)


def warm_up(policy, example, noise_shape=None):
    """One inference on an example input, so the first real request does not wait for JIT.

    Passing noise changes what openpi compiles, so in seeded mode the warm-up passes noise as well; otherwise
    the first real request would compile again.
    """
    began = time.perf_counter()
    if noise_shape is None:
        output = policy.infer(example)
    else:
        output = policy.infer(example, noise=np.zeros(tuple(noise_shape), dtype=np.float32))
    actions = np.asarray(output['actions'])
    seconds = time.perf_counter()-began
    if actions.ndim != 2 or actions.shape[1] != ACTION_DIM or not np.isfinite(actions).all():
        raise SystemExit(f'warm-up produced an unusable action chunk: shape {list(actions.shape)}')
    return dict(seconds=round(seconds, 3), shape=list(actions.shape),
                max_abs=round(float(np.abs(actions).max()), 6), with_noise=noise_shape is not None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', default='pi05_libero',
                        help="training config: one of openpi's (pi05_libero) or RoboLab's LoRA config from "
                             'scripts/openpi_robolab.py (pi05_robolab_lora), which is registered first')
    parser.add_argument('--checkpoint', type=Path, required=True,
                        help='local openpi checkpoint directory (the step directory, containing params/ and assets/)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--request-log', type=Path, help='append one JSON line per /predict request')
    parser.add_argument('--noise', choices=NOISE_MODES, default='seeded',
                        help='seeded (default): noise fixed by the observation, so the same observation always '
                             'gets the same noise; policy: openpi draws it in request order (how M0 ran)')
    parser.add_argument('--noise-seed', type=int, default=0,
                        help='mixed into the seeded noise; change it to draw a different reproducible sample')
    args = parser.parse_args(argv)
    checkpoint = args.checkpoint.expanduser().resolve()
    if not (checkpoint/'params').is_dir():
        parser.error('--checkpoint must be a local openpi checkpoint directory containing params/ '
                     '(this service never downloads)')
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    log = lambda **event: print(json.dumps(event, ensure_ascii=False), flush=True)  # noqa: E731
    log(event='loading', config=args.config, checkpoint=str(checkpoint), python=sys.executable,
        xla_preallocate=os.environ.get('XLA_PYTHON_CLIENT_PREALLOCATE', '<default: preallocate 75%>'))
    began = time.perf_counter()
    config, policy = load_openpi_policy(args.config, checkpoint)
    load_s = round(time.perf_counter()-began, 3)
    noise_shape = (config.model.action_horizon, config.model.action_dim) if args.noise == 'seeded' else None
    from openpi.policies import libero_policy

    warm = warm_up(policy, libero_policy.make_libero_example(), noise_shape)
    request_log = args.request_log.open('a', encoding='utf-8') if args.request_log else None
    bridge = OpenpiBridge(policy, config_name=args.config, checkpoint=checkpoint, request_log=request_log,
                          noise=args.noise, noise_seed=args.noise_seed, noise_shape=noise_shape)
    server = make_server(bridge, args.host, args.port)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    log(event='ready', host=args.host, port=server.server_port, load_s=load_s, warmup=warm,
        clip_tolerance=CLIP_TOLERANCE, image_convention=IMAGE_CONVENTION, noise=bridge.noise_description())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log(event='stopped', stats=bridge.stats())
        if request_log is not None:
            request_log.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
