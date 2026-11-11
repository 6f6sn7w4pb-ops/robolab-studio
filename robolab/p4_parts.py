"""Minimal part-conditioned synthetic Panda task, separate from LIBERO rewards."""
from dataclasses import dataclass
import math
import xml.etree.ElementTree as ET

import numpy as np


TARGET = 'p4_stepped_bar'
DISTRACTOR = 'milk_1_main'
PARTS = ('thick', 'thin')
INSTRUCTIONS = {
    'thick': ('Pick up the object by the thick end.', '抓住物体的粗端并抬起。'),
    'thin': ('Pick up the object by the thin end.', '抓住物体的细端并抬起。'),
}
GEOM_PART = {f'{TARGET}_{part}': part for part in PARTS}
MIN_FORCE_N = .2
REQUIRED_CONTACT_S = .20
REQUIRED_LIFT_M = .10
REQUIRED_HOLD_S = 1.
CONTROL_PERIOD_S = .02


@dataclass(frozen=True)
class SteppedBar:
    segment_length_m: float = .072
    thick_width_m: float = .056
    thin_width_m: float = .032
    thick_height_m: float = .048
    thin_height_m: float = .035

    def install(self, engine, *, rebuild=True):
        values = (self.segment_length_m, self.thick_width_m, self.thin_width_m,
                  self.thick_height_m, self.thin_height_m)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError('Part dimensions must be positive finite metres')
        if not (.06 <= self.segment_length_m <= .09 and
                .026 <= self.thin_width_m < self.thick_width_m <= .064 and
                .032 <= self.thin_height_m < self.thick_height_m <= .055):
            raise ValueError('Part dimensions exceed the prototype scene envelope')
        root = ET.fromstring(engine.base_xml)
        world = root.find('worldbody')
        soup = world.find("body[@name='alphabet_soup_1_main']")
        if soup is None:
            raise ValueError('The P4 scene requires the default LIBERO Panda layout')
        for body in world.findall('body'):
            if body is not soup and body.get('name') not in ('floor', 'robot0_base', DISTRACTOR):
                world.remove(body)
        soup.clear()
        soup.attrib.update(name=TARGET, pos='-0.12 -0.24 0.001')
        ET.SubElement(soup, 'freejoint', name=TARGET+'_joint')
        for part, side, width, height in (
                ('thick', -1, self.thick_width_m, self.thick_height_m),
                ('thin', 1, self.thin_width_m, self.thin_height_m)):
            length = self.segment_length_m
            ET.SubElement(soup, 'geom', name=f'{TARGET}_{part}', type='box', group='1',
                          pos=f'{side*length/2} 0 {height/2}', size=f'{length/2} {width/2} {height/2}',
                          density='350', rgba='0.76 0.43 0.19 1', friction='1.0 0.02 0.001')
        engine.base_xml = ET.tostring(root, encoding='unicode')
        engine.task = dict(engine.task, suite='p4_step_bar', source='synthetic_part_task',
                           language='Synthetic two-part grasp')
        if rebuild:
            engine._rebuild()
            engine._set_checkpoint()


class PartEvidence:
    """Separate pad identity, physical lift and the combined success criterion."""
    def __init__(self, requested, baseline_z, *, sample_period_s=CONTROL_PERIOD_S):
        self.requested = requested
        self.baseline_z = baseline_z
        self.sample_period_s = sample_period_s
        self.max_lift = 0.
        self.max_lift_hold = 0.
        self.max_part_lift_hold = {part: 0. for part in PARTS}
        self.max_part_contact = {part: 0. for part in PARTS}
        self.last_time = self.lift_start = self.part_start = self.contact_start = None
        self.first_match_index = None
        self.observed = None
        self.obstacle_frames = 0

    def observe(self, sample, target_z):
        per_finger = {finger: set() for finger in ('finger_1', 'finger_2')}
        obstacles = set()
        for contact in sample['contacts']:
            if not contact['load_bearing'] or contact['normal_force_N'] < MIN_FORCE_N:
                continue
            if contact['pair_role'] in ('finger_environment', 'finger_distractor'):
                obstacles.add(contact['pair_role'])
            if contact['pair_role'] == 'finger_target' and contact['surface_region'] == 'pad':
                per_finger[contact['finger']].add(GEOM_PART[contact['other_geom_name']])
        self.obstacle_frames += bool(obstacles)
        left, right = per_finger.values()
        part = next(iter(left)) if len(left) == 1 and left == right else None
        lift = target_z-self.baseline_z
        self.max_lift = max(self.max_lift, lift)
        now = sample['solver_step_start_time_s']
        if self.last_time is not None and abs(now-self.last_time-self.sample_period_s) > 1e-8:
            raise ValueError('Gap in continuous P4 contact evidence')
        self.last_time = now
        if left and right and lift >= REQUIRED_LIFT_M:
            if self.lift_start is None:
                self.lift_start = now
            self.max_lift_hold = max(self.max_lift_hold, now-self.lift_start)
        else:
            self.lift_start = None
        if part is not None:
            if self.contact_start is None or part != self.observed:
                self.contact_start = now
            self.max_part_contact[part] = max(self.max_part_contact[part], now-self.contact_start)
        else:
            self.contact_start = None
        if part and lift >= REQUIRED_LIFT_M:
            if self.part_start is None or part != self.observed:
                self.part_start = now
            self.max_part_lift_hold[part] = max(self.max_part_lift_hold[part], now-self.part_start)
            if part == self.requested and self.first_match_index is None and now-self.part_start+1e-8 >= REQUIRED_HOLD_S:
                self.first_match_index = sample['sample_index']
        else:
            self.part_start = None
        self.observed = part
        return dict(per_finger_parts={key: sorted(parts) for key, parts in per_finger.items()},
                    actual_part=part, lift_m=lift, obstacle_contacts=sorted(obstacles))

    def result(self):
        lifted = self.max_lift_hold+1e-8 >= REQUIRED_HOLD_S
        held = [part for part, seconds in self.max_part_lift_hold.items() if seconds+1e-8 >= REQUIRED_HOLD_S]
        contacted = [part for part, seconds in self.max_part_contact.items() if seconds+1e-8 >= REQUIRED_CONTACT_S]
        matched = self.requested in contacted
        return dict(requested_part=self.requested, held_parts=held, lifted=lifted, part_matched=matched,
                    contacted_parts=contacted, part_contact_hold_s=self.max_part_contact,
                    max_lift_m=self.max_lift, max_lift_hold_s=self.max_lift_hold,
                    part_lift_hold_s=self.max_part_lift_hold, finger_obstacle_frames=self.obstacle_frames,
                    passed=self.requested in held and self.obstacle_frames == 0,
                    first_match_sample_index=self.first_match_index)


class PartWaypoints:
    """Task-space path using known simulated part centers."""
    phases = (('settle', .60), ('approach', 3.), ('descend', 2.5),
              ('close', 1.), ('lift', 2.5), ('hold', 1.5))
    rotation = np.array([[0., 1, 0], [1, 0, 0], [0, 0, -1]])

    def __init__(self, engine, aimed_part, *, align_yaw=False):
        self.engine = engine
        self.aimed_geom = engine.model.geom(f'{TARGET}_{aimed_part}').id
        self.align_yaw = align_yaw
        self.target_body = engine.model.body(TARGET).id
        self.goals = None
        self.segment_from = None

    def commands(self, period):
        for index, (phase, duration) in enumerate(self.phases):
            if phase == 'approach':
                center = self.engine.data.geom_xpos[self.aimed_geom].copy()
                if self.align_yaw:
                    longitudinal = self.engine.data.xmat[self.target_body].reshape(3, 3)[:2, 0]
                    x, y = longitudinal/np.linalg.norm(longitudinal)
                    self.rotation = np.array([[-y, x, 0.], [x, y, 0.], [0., 0., -1.]])
                self.goals = (None, center+[0, 0, .17], center+[0, 0, .004], center+[0, 0, .004],
                              center+[0, 0, .17], center+[0, 0, .17])
            self.segment_from = self.engine.data.site_xpos[self.engine.robot.site].copy()
            for step in range(round(duration/period)):
                position = None
                if index:
                    alpha = min(1., step*period/max(.1, duration-.55))
                    alpha = alpha*alpha*(3.-2.*alpha)
                    position = self.segment_from+(self.goals[index]-self.segment_from)*alpha
                yield phase, position, self.rotation, 1. if index < 3 else 0.


class PartTeacher:
    """Execute the shared task-space path using the original Panda PD controller."""
    def __init__(self, engine, aimed_part, *, align_yaw=False):
        from .adapters import PandaPoseIK
        self.engine = engine
        self.path = PartWaypoints(engine, aimed_part, align_yaw=align_yaw)
        self.ik = PandaPoseIK(engine.robot)

    def segments(self):
        for phase, position, rotation, opening in self.path.commands(CONTROL_PERIOD_S):
            self.engine.robot.opening = opening
            if position is not None:
                self.ik.update(position, rotation)
            self.engine.step(CONTROL_PERIOD_S)
            yield phase
