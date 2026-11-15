"""Deterministic scene pairs and family-held-out splits for the P4 pilot."""
from dataclasses import dataclass
from math import cos, radians, sin
import xml.etree.ElementTree as ET

from .p4_parts import DISTRACTOR, TARGET, SteppedBar

OBSTACLE = 'p4_distractor_box'
FAMILIES = ('box_step', 'cylinder_step', 'ellipsoid_step')
TEST_FAMILY = FAMILIES[-1]
TEST_PROMPTS = {
    'thick': ('Lift the object by pinching its wider end.', '捏住较宽的一端，把物体抬起来。'),
    'thin': ('Lift the object by pinching its narrower end.', '捏住较窄的一端，把物体抬起来。'),
}


@dataclass(frozen=True)
class PilotScene:
    scene_id: str
    family: str
    split: str
    bar: SteppedBar
    target_xy: tuple[float, float]
    yaw_deg: float
    obstacle_xy: tuple[float, float]
    obstacle_half_size_m: tuple[float, float, float]
    held_out_phrasing: bool = False

    def install(self, engine):
        """Start from the same local Panda/camera assets used by P4-A."""
        self.bar.install(engine, rebuild=False)
        xml = ET.fromstring(engine.base_xml)
        world = xml.find('worldbody')
        target = world.find(f"body[@name='{TARGET}']")
        angle = radians(self.yaw_deg)/2
        target.set('pos', f'{self.target_xy[0]} {self.target_xy[1]} .001')
        target.set('quat', f'{cos(angle)} 0 0 {sin(angle)}')
        for geom in target.findall('geom'):
            half_length, half_width, half_height = map(float, geom.attrib['size'].split())
            x = float(geom.attrib['pos'].split()[0])
            if self.family == 'cylinder_step':
                geom.attrib.update(type='cylinder', size=f'{half_width} {half_length}',
                                   pos=f'{x} 0 {half_width}', quat='0.7071067811865476 0 0.7071067811865476 0')
            elif self.family == 'ellipsoid_step':
                # The lobes overlap axially, so the visual part boundary belongs to one rigid body.
                geom.attrib.update(type='ellipsoid', size=f'{half_length+.010} {half_width} {half_height}',
                                   pos=f'{x} 0 {half_height}')
        body = world.find(f"body[@name='{DISTRACTOR}']")
        half = self.obstacle_half_size_m
        body.clear()
        body.attrib.update(name=DISTRACTOR, pos=f'{self.obstacle_xy[0]} {self.obstacle_xy[1]} {half[2]+.001}')
        ET.SubElement(body, 'freejoint', name='p4_distractor_joint')
        ET.SubElement(body, 'geom', name=OBSTACLE, type='box', group='1', size=' '.join(map(str, half)),
                      density='420', rgba='.25 .51 .68 1', friction='.9 .02 .001')
        engine.base_xml = ET.tostring(xml, encoding='unicode')
        engine._rebuild()
        engine._set_checkpoint()


def pilot_scenes():
    for family in FAMILIES:
        for index, (xy, yaw, obstacle_xy) in enumerate((
                ((-.12, -.24), 0., (-.035, -.07)),
                ((-.11, -.225), 8., (.01, -.16)),
                ((-.135, -.245), -9., (-.045, -.10)),
                ((-.105, -.255), 12., (.015, -.19)))):
            bar = SteppedBar(segment_length_m=.070+.003*index,
                             thick_width_m=.054+.002*index, thin_width_m=.034+.002*index,
                             thick_height_m=.047+.002*index, thin_height_m=.035+.002*index)
            split = 'test_unseen' if family == TEST_FAMILY else 'validation' if index == 3 else 'train'
            yield PilotScene(f'{family}_{index}', family, split, bar, xy, yaw, obstacle_xy, (.024, .019, .030),
                             held_out_phrasing=split == 'test_unseen' and index >= 2)


def blocked_control():
    return PilotScene('blocked_approach', 'box_step', 'control', SteppedBar(), (-.12, -.24), 0.,
                      (-.084, -.191), (.023, .020, .048))
