"""P5 evaluation-only scene variants: cylinder anti-roll contacts and seeded appearance changes.

Neither variant touches the recorded P4-D dataset; both are recorded in the evaluation config
and in every episode result.

Anti-roll
    A cylinder-family bar lying on the floor slowly rolls about its long axis (about 10 degrees
    after 7-11 s with the arm still) because the two plain sliding-friction line contacts give
    almost no resistance to rolling. The variant replaces the dynamic target-floor contacts by
    explicit contact pairs with the same mixed friction, solref and solimp plus rolling and
    torsional friction (condim 6). Finger, obstacle and robot contacts are unchanged.

Appearance
    Seeded per scene (both part instructions of a scene see the same appearance), from three
    independent factor groups. All changes are MjModel rendering fields read at every frame, so
    physics is unchanged: a privileged teacher produces byte-identical actions and qpos.
    - lighting:   light intensity (x0.55-1.15), colour temperature, position, ambient, headlight, shadows
    - background: floor texture, tiling and tint; wall tint
    - objects:    target colour (both parts share it) and obstacle colour (hue-separated)
"""
import colorsys
import xml.etree.ElementTree as ET

from .p4_dataset import uniform
from .p4_parts import TARGET
from .p4_pilot_scene import OBSTACLE

APPEARANCE_FACTORS = ('lighting', 'background', 'objects')
APPEARANCE_SEED = 20261112
ANTI_ROLL_FAMILIES = ('cylinder_step',)
FLOOR_GEOM = 'floor'
FLOOR_BODY = 'floor'
# Values MuJoCo mixes for the dynamic target-floor contact (both geoms have solmix 1, so the
# friction is the element-wise maximum and solref/solimp the mean; both are the defaults here).
# Pair friction order: tangent1, tangent2, torsional, rolling1, rolling2.
ANTI_ROLL_PAIR = dict(condim='6', friction='1 1 0.02 0.001 0.001', solref='0.02 1',
                      solimp='0.9 0.95 0.001 0.5 2', margin='0', gap='0')
# Wood planks or the plain wall plaster, both tinted. 'tex-living_room' (a camouflage-like collage
# whose orange patches mimic the target) was tried and left out.
FLOOR_TEXTURES = ('texplane', 'tex-wall')
WARMTH_SPAN = .12


def anti_roll_applies(scene):
    return scene.family in ANTI_ROLL_FAMILIES


def anti_roll_xml(scene, xml_text):
    """Explicit rolling-resistant target-floor contacts for the scenes that need them."""
    if not anti_roll_applies(scene):
        return xml_text
    root = ET.fromstring(xml_text)
    names = {geom.get('name') for geom in root.iter('geom')}
    parts = [f'{TARGET}_thick', f'{TARGET}_thin']
    if FLOOR_GEOM not in names or not set(parts) <= names:
        raise ValueError('anti-roll needs the P4 target and the floor geom')
    contact = root.find('contact')
    if contact is None:
        contact = ET.SubElement(root, 'contact')
    ET.SubElement(contact, 'exclude', body1=TARGET, body2=FLOOR_BODY)
    for geom in parts:
        ET.SubElement(contact, 'pair', geom1=geom, geom2=FLOOR_GEOM, **ANTI_ROLL_PAIR)
    return ET.tostring(root, encoding='unicode')


def parse_appearance(text):
    """'' or 'none' -> (); 'all' -> every factor; else a comma list of factor names."""
    text = (text or '').strip()
    if text in ('', 'none', 'nominal'):
        return ()
    if text in ('all', 'full'):
        return APPEARANCE_FACTORS
    factors = tuple(name for name in APPEARANCE_FACTORS if name in text.split(','))
    if set(text.split(','))-set(APPEARANCE_FACTORS):
        raise ValueError('appearance factors: '+', '.join(APPEARANCE_FACTORS)+' (or all / none)')
    return factors


def _hsv(h, s, v):
    return [round(x, 4) for x in colorsys.hsv_to_rgb(h % 1., s, v)]


def appearance_params(scene_id, factors, seed=APPEARANCE_SEED):
    """JSON-serializable appearance for one scene; empty for the nominal look."""
    def draw(field, low, high):
        return uniform(seed, scene_id, field, low, high)

    params = {}
    if 'lighting' in factors:
        lights = []
        for index in range(2):
            key = f'light{index}'
            # Relative to the scene's own light: brightness and colour temperature, a horizontal
            # offset and a new height. The light keeps its direction (aiming it at the workspace
            # over-exposes the 224 px views).
            lights.append(dict(intensity=round(draw(key+'_intensity', .55, 1.15), 4),
                               warmth=round(draw(key+'_warmth', -1., 1.), 4),
                               offset_m=[round(draw(key+'_offset_x_m', -1., 1.), 4),
                                         round(draw(key+'_offset_y_m', -1., 1.), 4)],
                               height_m=round(draw(key+'_height_m', 3., 5.), 4),
                               ambient=round(draw(key+'_ambient', 0., .12), 4),
                               specular=round(draw(key+'_specular', .1, .4), 4),
                               castshadow=draw(key+'_shadow', 0., 1.) < .5))
        params['lighting'] = dict(lights=lights, headlight=dict(
            diffuse=round(draw('headlight_diffuse', .2, .55), 4), ambient=round(draw('headlight_ambient', .05, .2), 4)))
    if 'background' in factors:
        texture = FLOOR_TEXTURES[min(int(draw('floor_texture', 0, len(FLOOR_TEXTURES))), len(FLOOR_TEXTURES)-1)]
        repeat = round(draw('floor_repeat', 1.5, 6.), 3)
        params['background'] = dict(
            floor_texture=texture, floor_repeat=[repeat, repeat],
            floor_rgba=_hsv(draw('floor_hue', 0., 1.), draw('floor_saturation', 0., .35),
                            draw('floor_value', .55, 1.))+[1.],
            wall_rgba=_hsv(draw('wall_hue', 0., 1.), draw('wall_saturation', 0., .3), draw('wall_value', .5, 1.))+[1.])
    if 'objects' in factors:
        hue = draw('target_hue', 0., 1.)
        # Keep the obstacle at least 60 degrees of hue away so the two objects stay distinct.
        obstacle_hue = hue+draw('obstacle_hue_offset', 1/6, 5/6)
        params['objects'] = dict(
            target_rgba=_hsv(hue, draw('target_saturation', .35, .85), draw('target_value', .45, .9))+[1.],
            obstacle_rgba=_hsv(obstacle_hue, draw('obstacle_saturation', .35, .85),
                               draw('obstacle_value', .45, .9))+[1.])
    return params


def apply_appearance(model, params):
    """Write the parameters into rendering fields of a freshly compiled scene model."""
    lighting = params.get('lighting')
    if lighting:
        if model.nlight < len(lighting['lights']):
            raise ValueError('scene has fewer lights than the appearance specifies')
        for index, light in enumerate(lighting['lights']):
            base = model.light_diffuse[index].copy()
            warmth = WARMTH_SPAN*light['warmth']
            model.light_diffuse[index] = base*light['intensity']*[1+warmth, 1., 1-warmth]
            model.light_ambient[index] = [light['ambient']]*3
            model.light_specular[index] = [light['specular']]*3
            model.light_pos[index, :2] += light['offset_m']
            model.light_pos[index, 2] = light['height_m']
            model.light_castshadow[index] = light['castshadow']
        model.vis.headlight.diffuse[:] = [lighting['headlight']['diffuse']]*3
        model.vis.headlight.ambient[:] = [lighting['headlight']['ambient']]*3
    background = params.get('background')
    if background:
        floor = model.geom(FLOOR_GEOM)
        material = int(floor.matid[0])
        if material < 0:
            raise ValueError('floor has no material to vary')
        model.mat_texid[material] = model.texture(background['floor_texture']).id
        model.mat_texrepeat[material] = background['floor_repeat']
        model.mat_rgba[material] = background['floor_rgba']
        walls = {int(model.geom_matid[i]) for i in range(model.ngeom) if model.geom(i).name.startswith('wall_')}
        for wall in walls - {-1}:
            model.mat_rgba[wall] = background['wall_rgba']
    objects = params.get('objects')
    if objects:
        for part in ('thick', 'thin'):
            model.geom(f'{TARGET}_{part}').rgba[:] = objects['target_rgba']
        model.geom(OBSTACLE).rgba[:] = objects['obstacle_rgba']


def load_eval_scene(engine, scene, *, anti_roll=False, appearance=()):
    """Install a planned scene for evaluation; returns (SyntheticOsc, applied variant record)."""
    from .p4_osc import SyntheticOsc

    engine.load_task(seed=0, init_index=0)
    rolled = anti_roll and anti_roll_applies(scene)
    scene.install(engine, xml_hook=anti_roll_xml if rolled else None)
    params = appearance_params(scene.scene_id, appearance) if appearance else {}
    apply_appearance(engine.model, params)
    return SyntheticOsc(engine), dict(anti_roll=bool(rolled), appearance=params)
