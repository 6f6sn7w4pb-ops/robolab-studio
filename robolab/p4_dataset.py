"""Seeded plan for the first P4 OSC dataset: scenes, paired prompts and split audit.

Sampling uses SHA-256 derived uniforms instead of a library RNG stream, so the same
seed gives the same plan on any machine or NumPy version. The plan is pure data; it
does not touch MuJoCo until a scene is installed into an engine.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math

from .p4_parts import PARTS, SteppedBar
from .p4_pilot_scene import FAMILIES, TEST_FAMILY, PilotScene, blocked_control, pilot_scenes

PLAN_SCHEMA = 'robolab.p4_dataset_plan.v1'
DATASET_SEED = 20261111
SPLITS = ('train', 'validation', 'test_instance', 'test_unseen')
DEV_SPLITS = ('train', 'validation')
SEEN_FAMILIES = tuple(family for family in FAMILIES if family != TEST_FAMILY)
SCENES_PER_SPLIT = {
    'box_step': {'train': 30, 'validation': 4, 'test_instance': 6},
    'cylinder_step': {'train': 30, 'validation': 4, 'test_instance': 6},
    'ellipsoid_step': {'test_unseen': 20},
}
# Units: metres and degrees. The envelope stays inside SteppedBar.install's checks.
RANGES = {
    'segment_length_m': (.064, .086),
    'thick_width_m': (.048, .062),
    'thin_width_m': (.028, None),           # upper bound: thick width - MIN_WIDTH_GAP_M
    'thick_height_m': (.042, .054),
    'thin_height_m': (.033, None),          # upper bound: thick height - MIN_HEIGHT_GAP_M
    'target_x_m': (-.16, -.08),
    'target_y_m': (-.29, -.19),
    'yaw_deg': (0., 360.),
    'obstacle_half_x_m': (.018, .028),
    'obstacle_half_y_m': (.016, .024),
    'obstacle_half_z_m': (.025, .035),
    'obstacle_distance_m': (.10, .22),
    'obstacle_bearing_deg': (0., 360.),
    'obstacle_x_m': (-.32, .06),            # visible table area for the agent-view camera
    'obstacle_y_m': (-.36, -.06),
}
MIN_WIDTH_GAP_M = .014
MIN_HEIGHT_GAP_M = .006
# An ellipsoid lobe lying on the floor is in stable equilibrium only if its vertical semi-axis is
# shorter than its horizontal one. Taller lobes balance on their crown and roll over within seconds
# (seen in the first collection attempt), so both ellipsoid lobes keep height/width <= this ratio.
ELLIPSOID_MAX_HEIGHT_TO_WIDTH = .92
ELLIPSOID_RANGES = {
    'thick_width_m': (.050, .062),
    'thin_width_m': (.036, None),           # upper bound: thick width - MIN_WIDTH_GAP_M
    'thick_height_m': (.042, None),         # upper bound: min(.054, ratio * thick width)
    'thin_height_m': (.033, None),          # upper bound: min(ratio * thin width, thick height - gap)
}
# Keep-out zones in the object frame (x along the bar axis, origin at the part junction).
BAR_MARGIN_M = .03
FINGER_SWEEP_HALF_ALONG_M = .045
FINGER_SWEEP_HALF_ACROSS_M = .125
OBSTACLE_CLEARANCE_M = .005
MAX_PLACEMENT_ATTEMPTS = 400

# Prompts are paired: one template index per scene, so the thick/thin pair differs only in the part.
SEEN_PROMPTS = {
    'thick': (('Pick up the object by the thick end.', '抓住物体的粗端并抬起。'),
              ('Grasp the thicker end of the object and lift it.', '抓住物体较粗的一端，把它提起来。'),
              ('Lift the object by holding its thick end.', '握住粗的那一端，把物体抬起来。')),
    'thin': (('Pick up the object by the thin end.', '抓住物体的细端并抬起。'),
             ('Grasp the thinner end of the object and lift it.', '抓住物体较细的一端，把它提起来。'),
             ('Lift the object by holding its thin end.', '握住细的那一端，把物体抬起来。')),
}
HELD_OUT_PROMPTS = {
    'thick': (('Lift the object by pinching its wider end.', '捏住较宽的一端，把物体抬起来。'),
              ('Take hold of the bulkier end and raise the object.', '拿住更粗壮的那一头，把物体举起来。')),
    'thin': (('Lift the object by pinching its narrower end.', '捏住较窄的一端，把物体抬起来。'),
             ('Take hold of the slimmer end and raise the object.', '拿住更纤细的那一头，把物体举起来。')),
}


@dataclass(frozen=True)
class DatasetCase:
    case_id: str
    scene: PilotScene
    part: str
    instruction_en: str
    instruction_zh: str
    phrasing: str               # 'seen' or 'held_out'
    expected: str               # 'success' or 'blocked_failure'

    @property
    def split(self):
        return self.scene.split

    @property
    def instruction(self):
        return dict(part=self.part, en=self.instruction_en, zh=self.instruction_zh, phrasing=self.phrasing)

    def describe(self):
        return dict(case_id=self.case_id, scene=asdict(self.scene), part=self.part, split=self.split,
                    family=self.scene.family, instruction=self.instruction, expected=self.expected,
                    shape_id=shape_id(self.scene), layout_id=layout_id(self.scene))


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def shape_id(scene):
    return canonical_digest([scene.family, asdict(scene.bar)])


def layout_id(scene):
    return canonical_digest([scene.target_xy, scene.yaw_deg, scene.obstacle_xy, scene.obstacle_half_size_m])


def uniform(seed, key, field, low, high, attempt=0):
    digest = hashlib.sha256(f'{seed}|{key}|{field}|{attempt}'.encode()).digest()
    return low+(high-low)*int.from_bytes(digest[:8], 'big')/2**64


def _point_box_distance(x, y, half_x, half_y, center_x=0.):
    dx = max(abs(x-center_x)-half_x, 0.)
    dy = max(abs(y)-half_y, 0.)
    return math.hypot(dx, dy)


def obstacle_clearance(scene):
    """Smallest gap from the obstacle's bounding circle to the bar and finger keep-out zones."""
    yaw = math.radians(scene.yaw_deg)
    dx = scene.obstacle_xy[0]-scene.target_xy[0]
    dy = scene.obstacle_xy[1]-scene.target_xy[1]
    along = math.cos(yaw)*dx+math.sin(yaw)*dy
    across = -math.sin(yaw)*dx+math.cos(yaw)*dy
    radius = math.hypot(scene.obstacle_half_size_m[0], scene.obstacle_half_size_m[1])
    length = scene.bar.segment_length_m
    zones = [(length+BAR_MARGIN_M, scene.bar.thick_width_m/2+BAR_MARGIN_M, 0.)]
    zones += [(FINGER_SWEEP_HALF_ALONG_M, FINGER_SWEEP_HALF_ACROSS_M, side*length/2) for side in (-1, 1)]
    return min(_point_box_distance(along, across, half_x, half_y, center) for half_x, half_y, center in zones)-radius


def _bar(seed, key, family):
    def draw(field, low, high):
        return round(uniform(seed, key, field, low, high), 4)
    if family == 'ellipsoid_step':
        ratio, ranges = ELLIPSOID_MAX_HEIGHT_TO_WIDTH, ELLIPSOID_RANGES
        thick_width = draw('thick_width_m', *ranges['thick_width_m'])
        thin_width = draw('thin_width_m', ranges['thin_width_m'][0], thick_width-MIN_WIDTH_GAP_M)
        thick_height = draw('thick_height_m', ranges['thick_height_m'][0],
                            min(RANGES['thick_height_m'][1], ratio*thick_width))
        thin_height = draw('thin_height_m', ranges['thin_height_m'][0],
                           min(ratio*thin_width, thick_height-MIN_HEIGHT_GAP_M))
        bar = SteppedBar(segment_length_m=draw('segment_length_m', *RANGES['segment_length_m']),
                         thick_width_m=thick_width, thin_width_m=thin_width,
                         thick_height_m=thick_height, thin_height_m=thin_height)
        if not rests_stably(family, bar):
            raise ValueError('Ellipsoid sample violates the stable-rest ratio: '+key)
        return bar
    thick_width = draw('thick_width_m', *RANGES['thick_width_m'])
    thick_height = draw('thick_height_m', *RANGES['thick_height_m'])
    return SteppedBar(segment_length_m=draw('segment_length_m', *RANGES['segment_length_m']),
                      thick_width_m=thick_width,
                      thin_width_m=draw('thin_width_m', RANGES['thin_width_m'][0], thick_width-MIN_WIDTH_GAP_M),
                      thick_height_m=thick_height,
                      thin_height_m=draw('thin_height_m', RANGES['thin_height_m'][0], thick_height-MIN_HEIGHT_GAP_M))


def rests_stably(family, bar):
    """Boxes rest on a face and cylinders roll neutrally; ellipsoid lobes need height < width."""
    if family != 'ellipsoid_step':
        return True
    # Rounded sample values can exceed the ratio by a rounding step; allow that much.
    tolerance = 5e-5
    return (bar.thick_height_m <= ELLIPSOID_MAX_HEIGHT_TO_WIDTH*bar.thick_width_m+tolerance
            and bar.thin_height_m <= ELLIPSOID_MAX_HEIGHT_TO_WIDTH*bar.thin_width_m+tolerance)


def _scene(seed, family, split, index, held_out):
    key = f'{family}/{split}/{index}'
    bar = _bar(seed, key, family)
    target = (round(uniform(seed, key, 'target_x_m', *RANGES['target_x_m']), 4),
              round(uniform(seed, key, 'target_y_m', *RANGES['target_y_m']), 4))
    yaw = round(uniform(seed, key, 'yaw_deg', *RANGES['yaw_deg']), 1)
    half = tuple(round(uniform(seed, key, f'obstacle_half_{axis}_m', *RANGES[f'obstacle_half_{axis}_m']), 4)
                 for axis in 'xyz')
    for attempt in range(MAX_PLACEMENT_ATTEMPTS):
        distance = uniform(seed, key, 'obstacle_distance_m', *RANGES['obstacle_distance_m'], attempt)
        bearing = math.radians(uniform(seed, key, 'obstacle_bearing_deg', *RANGES['obstacle_bearing_deg'], attempt))
        obstacle = (round(target[0]+distance*math.cos(bearing), 4), round(target[1]+distance*math.sin(bearing), 4))
        scene = PilotScene(f'{family}-{split}-{index:03d}', family, split, bar, target, yaw, obstacle, half,
                           held_out_phrasing=held_out)
        inside = (RANGES['obstacle_x_m'][0]+half[0] <= obstacle[0] <= RANGES['obstacle_x_m'][1]-half[0]
                  and RANGES['obstacle_y_m'][0]+half[1] <= obstacle[1] <= RANGES['obstacle_y_m'][1]-half[1])
        if inside and obstacle_clearance(scene) >= OBSTACLE_CLEARANCE_M:
            return scene
    raise ValueError('No collision-free obstacle placement for '+key)


def _prompt(seed, scene, part):
    pool = HELD_OUT_PROMPTS if scene.held_out_phrasing else SEEN_PROMPTS
    template = int(uniform(seed, scene.scene_id, 'prompt_template', 0, len(pool['thick'])))
    return pool[part][min(template, len(pool[part])-1)]


def _cases(seed, scenes):
    for scene in scenes:
        for part in PARTS:
            en, zh = _prompt(seed, scene, part)
            yield DatasetCase(f'{scene.scene_id}_{part}', scene, part, en, zh,
                              'held_out' if scene.held_out_phrasing else 'seen', 'success')


def blocked_controls(seed=DATASET_SEED):
    """Expected-failure controls: a tall box right beside the aimed part, inside the finger sweep."""
    controls = []
    for index, (family, part, yaw) in enumerate((('box_step', 'thin', 0.), ('box_step', 'thick', 137.),
                                                ('cylinder_step', 'thin', 251.), ('ellipsoid_step', 'thick', 63.))):
        key = f'{family}/control/{index}'
        bar = _bar(seed, key, family)
        target = (-.12, -.24)
        side = -1 if part == 'thick' else 1
        width = bar.thick_width_m if part == 'thick' else bar.thin_width_m
        half = (.022, .022, .048)
        rad = math.radians(yaw)
        center_along, offset_across = side*bar.segment_length_m/2, width/2+.013+half[1]
        obstacle = (round(target[0]+math.cos(rad)*center_along-math.sin(rad)*offset_across, 4),
                    round(target[1]+math.sin(rad)*center_along+math.cos(rad)*offset_across, 4))
        scene = PilotScene(f'{family}-control-{index:03d}', family, 'control', bar, target, yaw, obstacle, half)
        en, zh = SEEN_PROMPTS[part][0]
        controls.append(DatasetCase(f'{scene.scene_id}_{part}', scene, part, en, zh, 'seen', 'blocked_failure'))
    return controls


def plan_v1(seed=DATASET_SEED):
    scenes = []
    for family, counts in SCENES_PER_SPLIT.items():
        for split, count in counts.items():
            for index in range(count):
                # Half of every test split uses phrasing never seen in training or validation.
                held_out = split.startswith('test') and index >= count//2
                scenes.append(_scene(seed, family, split, index, held_out))
    return list(_cases(seed, scenes)), blocked_controls(seed)


def plan_pilot():
    """The P4-B pilot scenes re-collected through the actual OSC controller."""
    from .p4_parts import INSTRUCTIONS
    from .p4_pilot_scene import TEST_PROMPTS
    cases = []
    for scene in pilot_scenes():
        for part in PARTS:
            en, zh = (TEST_PROMPTS if scene.held_out_phrasing else INSTRUCTIONS)[part]
            cases.append(DatasetCase(f'{scene.scene_id}_{part}', scene, part, en, zh,
                                     'held_out' if scene.held_out_phrasing else 'seen', 'success'))
    control = blocked_control()
    en, zh = INSTRUCTIONS['thin']
    return cases, [DatasetCase(f'{control.scene_id}_thin', control, 'thin', en, zh, 'seen', 'blocked_failure')]


PLANS = {'v1': plan_v1, 'pilot': plan_pilot}


def audit_plan(cases, controls=()):
    """Reject plans that leak a held-out family, shape instance, layout or phrase into development data."""
    by_scene = {}
    for case in cases:
        by_scene.setdefault(case.scene.scene_id, []).append(case)
        if case.split not in SPLITS:
            raise ValueError('Unknown split: '+case.split)
        if case.expected != 'success':
            raise ValueError('Indexed cases must be ordinary teacher demonstrations')
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError('Duplicate case id')
    for scene_id, group in by_scene.items():
        if len(group) != 2 or {case.part for case in group} != set(PARTS):
            raise ValueError('Scene lacks its paired thick/thin instructions: '+scene_id)
        if group[0].scene != group[1].scene or group[0].phrasing != group[1].phrasing:
            raise ValueError('Paired cases differ in scene or phrasing: '+scene_id)
    scenes = [group[0].scene for group in by_scene.values()]
    if len({shape_id(scene) for scene in scenes}) != len(scenes):
        raise ValueError('Duplicate shape instance across scenes')
    if len({(shape_id(scene), layout_id(scene)) for scene in scenes}) != len(scenes):
        raise ValueError('Duplicate scene across splits')
    families = {split: sorted({s.family for s in scenes if s.split == split}) for split in SPLITS}
    unseen = set(families['test_unseen'])
    seen = set(families['train'])|set(families['validation'])|set(families['test_instance'])
    if unseen & seen:
        raise ValueError('Held-out shape family appears in development or instance-test data')
    if set(families['validation'])-set(families['train']) or set(families['test_instance'])-set(families['train']):
        raise ValueError('Validation/instance-test families must be seen in training')
    if any(case.split in DEV_SPLITS and case.phrasing != 'seen' for case in cases):
        raise ValueError('Development data must use seen phrasing only')
    for language in ('en', 'zh'):
        dev = {getattr(case, 'instruction_'+language) for case in cases if case.split in DEV_SPLITS}
        held_out = {getattr(case, 'instruction_'+language) for case in cases if case.phrasing == 'held_out'}
        if dev & held_out:
            raise ValueError('Held-out phrasing leaks into development prompts')
        owner = {}
        for case in cases:
            text = getattr(case, 'instruction_'+language)
            if owner.setdefault(text, case.part) != case.part:
                raise ValueError('One prompt refers to both parts: '+text)
    for control in controls:
        if control.split != 'control' or control.expected != 'blocked_failure':
            raise ValueError('Controls must be separate expected-failure cases')
        if control.scene.scene_id in by_scene:
            raise ValueError('Control reuses an indexed scene id')
    counts = {split: sum(case.split == split for case in cases) for split in SPLITS}
    return dict(families_by_split=families, episodes_by_split=counts, scenes=len(scenes),
                episodes=len(cases), controls=len(controls),
                held_out_phrasing_episodes=sum(case.phrasing == 'held_out' for case in cases))


def plan_document(name, seed=DATASET_SEED):
    if name == 'v1':
        cases, controls = plan_v1(seed)
    else:
        cases, controls = PLANS[name]()
    audit = audit_plan(cases, controls)
    document = dict(schema=PLAN_SCHEMA, plan=name, seed=seed if name == 'v1' else None,
                    ranges=RANGES if name == 'v1' else None,
                    keep_out=dict(bar_margin_m=BAR_MARGIN_M, finger_sweep_half_along_m=FINGER_SWEEP_HALF_ALONG_M,
                                  finger_sweep_half_across_m=FINGER_SWEEP_HALF_ACROSS_M,
                                  obstacle_clearance_m=OBSTACLE_CLEARANCE_M),
                    audit=audit, cases=[case.describe() for case in cases],
                    controls=[case.describe() for case in controls])
    if name == 'v1':
        document['ellipsoid'] = dict(ranges=ELLIPSOID_RANGES, max_height_to_width=ELLIPSOID_MAX_HEIGHT_TO_WIDTH)
    document['plan_sha256'] = canonical_digest(document)
    return document, cases, controls
