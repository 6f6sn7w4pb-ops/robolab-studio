"""Versioned project format. Values are SI units, rotations XYZ degrees, wxyz quaternions."""
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path

SCHEMA_VERSION = 1
SUITES = ('libero_object', 'libero_spatial', 'libero_goal', 'libero_10')

def number(value, low, high, label):
    if isinstance(value, bool):
        raise ValueError(f'{label}: expected a finite number')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}: must be within [{low}, {high}]')
    return value

def vector(value, count, low, high, label):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(f'{label}: expected {count} numbers')
    return [number(x, low, high, label) for x in value]

def validate_object(obj):
    if not isinstance(obj, dict):
        raise ValueError('Object must be a dictionary')
    obj = deepcopy(obj)
    if not re.fullmatch(r'rl_[a-zA-Z0-9_]{1,60}', obj.get('id', '')):
        raise ValueError('Invalid object id')
    if obj.get('kind') not in ('box', 'sphere', 'cylinder'):
        raise ValueError('Unsupported primitive')
    obj['position'] = vector(obj.get('position'), 3, -10, 10, 'Position / m')
    obj['rotation'] = vector(obj.get('rotation'), 3, -360, 360, 'Rotation / deg')
    obj['size'] = vector(obj.get('size'), 3, .002, 2, 'Dimensions / m')
    obj['mass'] = number(obj.get('mass'), .001, 100, 'Mass / kg')
    obj['friction'] = vector(obj.get('friction'), 3, 0, 10, 'Friction')
    if not isinstance(obj.get('collision'), bool):
        raise ValueError('Collision must be a boolean')
    obj['color'] = vector(obj.get('color', [.18, .65, .88, 1]), 4, 0, 1, 'Color')
    return obj

def validate_project(project):
    if not isinstance(project, dict) or project.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Unsupported RoboLab project schema')
    project = deepcopy(project)
    task = project.get('task', {})
    if task.get('suite') not in SUITES:
        raise ValueError('Unsupported LIBERO suite')
    for key, high in [('index', 99), ('seed', 2**31-1), ('init_index', 10000)]:
        value = task.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
            raise ValueError(f'Invalid task {key}')
    objects = project.get('objects', [])
    if not isinstance(objects, list) or len(objects) > 100:
        raise ValueError('Maximum 100 editor objects')
    project['objects'] = [validate_object(o) for o in objects]
    if len({o['id'] for o in objects}) != len(objects):
        raise ValueError('Duplicate object ids')
    placements = project.get('placements', {})
    if not isinstance(placements, dict) or len(placements) > 200:
        raise ValueError('Invalid placements')
    for key, value in placements.items():
        q = vector(value, 7, -10, 10, 'Free joint pose')
        norm = math.sqrt(sum(x*x for x in q[3:]))
        if norm < 1e-8:
            raise ValueError('Zero quaternion')
        placements[key] = q[:3] + [x/norm for x in q[3:]]
    project['placements'] = placements
    camera = project.get('camera', {})
    project['camera'] = {
        'lookat': vector(camera.get('lookat', [0, 0, .85]), 3, -10, 10, 'Camera lookat'),
        'distance': number(camera.get('distance', 1.4), .15, 10, 'Camera distance'),
        'azimuth': number(camera.get('azimuth', 90), -100000, 100000, 'Camera azimuth'),
        'elevation': number(camera.get('elevation', -35), -89, 89, 'Camera elevation'),
    }
    joints = project.get('robot_qpos', [])
    if not isinstance(joints, list) or not 1 <= len(joints) <= 32:
        raise ValueError('Robot must have between 1 and 32 joint coordinates')
    project['robot_qpos'] = vector(joints, len(joints), -100, 100, 'Robot joint position')
    project['gripper'] = number(project.get('gripper', 1), 0, 1, 'Gripper opening')
    return project

def read_project(path):
    path = Path(path).expanduser()
    if path.stat().st_size > 2_000_000:
        raise ValueError('Project file exceeds 2 MB')
    return validate_project(json.loads(path.read_text(encoding='utf-8')))

def write_project(path, project):
    project = validate_project(project)
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            tmp = Path(stream.name)
            json.dump(project, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()
    return str(path)
