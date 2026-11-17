"""Camera placement study in the measured lab layout (robot table + lower object table).

  study_lab_cameras.py --output-dir outputs/lab_camera_study [--grid 5] [--every 6]

1. Runs the P4 rule teacher (actual robosuite OSC) on a grid of target positions and headings
   over the object table, recording every tick's state. This also maps which part of the object
   table the arm can actually grasp from.
2. Re-renders every candidate camera (external positions and heights, wrist D455 tilts) from the
   recorded states and measures, with segmentation, how much of the requested part is visible:
   in the frame at all, and not hidden by the arm.
3. Writes metrics.json, summary.md and image sheets.
Physics is identical for all cameras (they are only rendered), so they are compared on exactly
the same motions. Each worker owns one simulator (~2.7 GB).
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PHASES = (('settle', 0, 12), ('approach', 12, 72), ('descend', 72, 122), ('close', 122, 142),
          ('lift', 142, 192), ('hold', 192, 222))
PRE_GRASP = (12, 142)          # approach + descend + close: what the policy must see to aim
RENDER_W, RENDER_H = 320, 200  # measurement renders, 16:10 like the D455's 1280 x 800 (above the policy's 224 x 140)
SHEET_W, SHEET_H = 640, 400
POLICY_SCALE = 224/RENDER_W    # pixel areas reported at the policy's 224 x 140 view (1280 x 800 resized with pad)


def _setup():
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('DISPLAY', None)
    os.environ.pop('WAYLAND_DISPLAY', None)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0, str(LIBERO_ROOT))


def candidate_cameras(layout, base_x):
    """(name, position, target) in world metres; heights are above the floor."""
    near, far = layout.object_table_x(base_x)
    centre = layout.workspace_center(base_x)
    half = layout.object_table_length/2
    cams = []
    for h in (1.10, 1.30, 1.50, 1.70):
        tag = f'{round(h*100)}'
        cams.append((f'front_{tag}', (far+.30, 0., h), centre))                 # facing the robot
        cams.append((f'side_right_{tag}', (centre[0], -(half+.30), h), centre))  # robot's right
        cams.append((f'side_left_{tag}', (centre[0], half+.30, h), centre))
    for h in (1.30, 1.50):
        cams.append((f'diag_front_right_{round(h*100)}', (far+.20, -(half+.10), h), centre))
    cams.append(('over_shoulder_145', (base_x-.10, -.30, 1.45), centre))
    cams.append(('base_low_101', (layout.front_edge_x(base_x)-.03, -.30, layout.robot_table_height+.12),
                 centre+[0., 0., 0.]))
    return cams


WRIST_TILTS = (0., 10., 20., 30.)


def scenes(layout, base_x, grid):
    """Target grid over the object table (box family, default size), headings and parts vary."""
    from robolab.p4_dataset import OBSTACLE_CLEARANCE_M, obstacle_clearance
    from robolab.p4_parts import SteppedBar
    from robolab.p4_pilot_scene import PilotScene

    near, far = layout.object_table_x(base_x)
    margin = .10
    xs = [near+margin+(far-near-2*margin)*i/(grid-1) for i in range(grid)]
    half = layout.object_table_length/2-margin
    ys = [-half+2*half*j/(grid-1) for j in range(grid)]
    out = []
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            k = i*grid+j
            yaw = (37.*k) % 360.
            part = ('thick', 'thin')[k % 2]
            bar = SteppedBar()
            obstacle, half_size = None, (.022, .02, .03)
            for attempt in range(72):
                bearing = math.radians(attempt*35.+k*11.)
                cand = (round(x+.16*math.cos(bearing), 4), round(y+.16*math.sin(bearing), 4))
                scene = PilotScene(f'lab_{k:02d}', 'box_step', 'lab', bar, (round(x, 4), round(y, 4)), yaw, cand,
                                   half_size)
                inside = near+.03 <= cand[0] <= far-.03 and abs(cand[1]) <= layout.object_table_length/2-.03
                if inside and obstacle_clearance(scene) >= OBSTACLE_CLEARANCE_M:
                    obstacle = scene
                    break
            out.append((obstacle or PilotScene(f'lab_{k:02d}', 'box_step', 'lab', bar, (round(x, 4), round(y, 4)), yaw,
                                               (far-.05, layout.object_table_length/2-.05), half_size), part,
                        dict(x_from_base=round(x-base_x, 3), y=round(y, 3), yaw=round(yaw, 1))))
    return out


def simulate(args, out):
    import numpy as np
    from robolab.engine import Engine
    from robolab.lab_scene import LabLayout, lab_xml_hook
    from robolab.p4_osc import OSC_PERIOD_S, SyntheticOsc
    from robolab.p4_parts import TARGET, PartEvidence, PartWaypoints

    layout = LabLayout()
    engine = Engine()
    engine.load_task(seed=0, init_index=0)
    import xml.etree.ElementTree as ET
    base_x = float(ET.fromstring(engine.base_xml).find(".//body[@name='robot0_base']").get('pos').split()[0])
    cams = candidate_cameras(layout, base_x)
    centre = layout.workspace_center(base_x)
    cams.append(('overview', (base_x+2.0, -1.75, 1.95), centre+[0., 0., .1]))
    cams.append(('topdown', (centre[0]-.2, 0., 3.2), (centre[0]-.2, 0., 0.)))
    wrists = [dict(name=f'wrist_tilt{round(t)}', offset=.06, height=0., tilt_deg=t) for t in WRIST_TILTS]
    plan = scenes(layout, base_x, args.grid)
    records = []
    for scene, part, where in plan:
        started = time.monotonic()
        engine.load_task(seed=0, init_index=0)
        # One D455 body (drawn at the 20 deg tilt) carries the four wrist cameras at the same point.
        hook = lab_xml_hook(layout, cams, wrist=wrists[2])

        def all_wrists(sc, xml, hook=hook):
            xml = hook(sc, xml)
            for w in wrists[:2]+wrists[3:]:
                xml = _add_wrist_only(xml, w)
            return xml
        scene.install(engine, xml_hook=all_wrists)
        osc = SyntheticOsc(engine)
        model = engine.model
        qadr_z = int(model.joint(TARGET+'_joint').qposadr[0])+2
        evidence = PartEvidence(part, float(engine.data.qpos[qadr_z]), sample_period_s=OSC_PERIOD_S)
        path = PartWaypoints(engine, part, align_yaw=True)
        qpos = [engine.data.qpos.copy()]
        gripper, error = -1., None
        try:
            for _, position, rotation, opening in path.commands(OSC_PERIOD_S):
                action = osc.action_toward(position, rotation, opening)
                sample = osc.step(action)
                evidence.observe(sample, sample['state_at_solver']['qpos'][qadr_z])
                qpos.append(engine.data.qpos.copy())
        except Exception as exc:          # unreachable poses are a result, not a crash
            error = str(exc)[:300]
        result = evidence.result()
        name = scene.scene_id
        np.savez_compressed(out/'scenes'/f'{name}.npz', qpos=np.asarray(qpos))
        (out/'scenes'/f'{name}.xml').write_text(engine.base_xml, encoding='utf-8')
        success = error is None and part in result['held_parts'] and result['finger_obstacle_frames'] == 0
        records.append(dict(scene=name, part=part, **where, success=success, held_parts=result['held_parts'],
                            max_lift_m=round(result['max_lift_m'], 4), obstacle_frames=result['finger_obstacle_frames'],
                            error=error, ticks=len(qpos)-1, wall_s=round(time.monotonic()-started, 1)))
        print(json.dumps(records[-1]), flush=True)
    engine.close()
    return dict(base_x=base_x, cameras=[dict(name=n, position=list(map(float, p)), target=list(map(float, t)))
                                       for n, p, t in cams],
                wrist=wrists, scenes=records)


def _add_wrist_only(xml_text, wrist):
    import xml.etree.ElementTree as ET
    from robolab.lab_scene import D455_RGB, HAND_BODY, _fmt, wrist_camera_pose
    root = ET.fromstring(xml_text)
    hand = next(body for body in root.iter('body') if body.get('name') == HAND_BODY)
    position, xyaxes, _, _ = wrist_camera_pose(wrist['offset'], wrist['height'], wrist['tilt_deg'])
    ET.SubElement(hand, 'camera', name=wrist['name'], mode='fixed', pos=_fmt(position), xyaxes=_fmt(xyaxes),
                  fovy=f"{D455_RGB['vfov_deg']:.6g}")
    return ET.tostring(root, encoding='unicode')


def render(args, out, info):
    import mujoco
    import numpy as np
    from PIL import Image
    from robolab.lab_scene import D455_RGB
    from robolab.p4_parts import TARGET
    from robolab.engine import dispose_renderer

    names = [c['name'] for c in info['cameras'] if c['name'] not in ('overview', 'topdown')]+[w['name'] for w in info['wrist']]
    fovs = {'nominal': D455_RGB['vfov_deg'], 'narrow': D455_RGB['alt_vfov_deg']}
    stats = {fov: {name: [] for name in names} for fov in fovs}
    sheets = {}
    central = min(info['scenes'], key=lambda s: abs(s['x_from_base']-.5)+abs(s['y']))['scene']
    for record in info['scenes']:
        xml = (out/'scenes'/f"{record['scene']}.xml").read_text(encoding='utf-8')
        qpos = np.load(out/'scenes'/f"{record['scene']}.npz")['qpos']
        model = mujoco.MjModel.from_xml_string(xml)
        model.vis.global_.offwidth, model.vis.global_.offheight = 1600, 1000
        data = mujoco.MjData(model)
        robot_root = model.body('robot0_base').id
        for g in range(model.ngeom):
            if model.body_rootid[model.geom_bodyid[g]] == robot_root and model.geom_group[g] == 1:
                model.geom_group[g] = 5
        part_ids = {p: model.geom(f'{TARGET}_{p}').id for p in ('thick', 'thin')}
        fingers = [model.geom(n).id for n in ('gripper0_finger1_visual', 'gripper0_finger2_visual')]
        with_robot, no_robot = mujoco.MjvOption(), mujoco.MjvOption()
        for option in (with_robot, no_robot):
            option.geomgroup[:] = 0
            option.geomgroup[1] = option.geomgroup[2] = 1
            option.sitegroup[:] = 0
        with_robot.geomgroup[5] = 1
        seg = mujoco.Renderer(model, RENDER_H, RENDER_W)
        seg.enable_segmentation_rendering()
        rgb = mujoco.Renderer(model, SHEET_H, SHEET_W)
        cam_ids = {name: model.camera(name).id for name in names}
        ticks = list(range(0, len(qpos), args.every))
        requested = part_ids[record['part']]
        for fov_name, fovy in fovs.items():
            model.cam_fovy[list(cam_ids.values())] = fovy
            for tick in ticks:
                data.qpos[:] = qpos[tick]
                mujoco.mj_forward(model, data)
                for name, cid in cam_ids.items():
                    counts = []
                    for option in (no_robot, with_robot):
                        seg.update_scene(data, camera=cid, scene_option=option)
                        pixels = seg.render()
                        ids = np.where(pixels[..., 1] == mujoco.mjtObj.mjOBJ_GEOM, pixels[..., 0], -1)
                        counts.append(dict(req=int((ids == requested).sum()), thick=int((ids == part_ids['thick']).sum()),
                                           thin=int((ids == part_ids['thin']).sum()),
                                           fingers=int(np.isin(ids, fingers).sum())))
                    bare, seen = counts
                    stats[fov_name][name].append(dict(scene=record['scene'], tick=tick, success=record['success'],
                                                      req_bare=bare['req'], req_seen=seen['req'],
                                                      thick=bare['thick'], thin=bare['thin'], fingers=seen['fingers']))
            if fov_name == 'nominal' and record['scene'] == central:
                for name in names+['overview', 'topdown']:
                    tiles = []
                    for tick in (0, 100, 135, 180):
                        data.qpos[:] = qpos[min(tick, len(qpos)-1)]
                        mujoco.mj_forward(model, data)
                        rgb.update_scene(data, camera=model.camera(name).id, scene_option=with_robot)
                        tiles.append(rgb.render())
                    sheets[name] = tiles
            model.cam_fovy[list(cam_ids.values())] = D455_RGB['vfov_deg']
        dispose_renderer(seg)
        dispose_renderer(rgb)
        print(json.dumps(dict(event='rendered', scene=record['scene'])), flush=True)
    return names, stats, sheets, central


def summarize(names, stats, info):
    import numpy as np
    pre0, pre1 = PRE_GRASP
    ok = {s['scene'] for s in info['scenes'] if s['success']}
    table = {}
    for fov, per in stats.items():
        table[fov] = {}
        for name in names:
            rows = [r for r in per[name] if r['scene'] in ok]
            pre = [r for r in rows if pre0 <= r['tick'] < pre1]
            close = [r for r in rows if 122 <= r['tick'] < 142]
            start = [r for r in rows if r['tick'] == 0]
            vis = [r['req_seen']/r['req_bare'] if r['req_bare'] else 0. for r in pre]
            table[fov][name] = dict(
                pre_grasp_mean_visible=round(float(np.mean(vis)), 3) if vis else None,
                pre_grasp_ticks_half_visible=round(float(np.mean([v >= .5 for v in vis])), 3) if vis else None,
                pre_grasp_out_of_frame=round(float(np.mean([r['req_bare'] == 0 for r in pre])), 3) if pre else None,
                close_mean_visible=round(float(np.mean([r['req_seen']/r['req_bare'] if r['req_bare'] else 0. for r in close])), 3)
                if close else None,
                close_fingers_in_view=round(float(np.mean([r['fingers'] > 0 for r in close])), 3) if close else None,
                start_thin_px_224=round(float(np.median([r['thin'] for r in start]))*POLICY_SCALE**2, 1) if start else None,
                start_thick_px_224=round(float(np.median([r['thick'] for r in start]))*POLICY_SCALE**2, 1) if start else None,
                start_min_part_px_224=round(float(min(min(r['thin'], r['thick']) for r in start))*POLICY_SCALE**2, 1)
                if start else None)
    return table


def sheets_out(out, sheets, names):
    import numpy as np
    from PIL import Image, ImageDraw
    (out/'images').mkdir(exist_ok=True)

    def labelled(tile, text):
        image = Image.fromarray(tile).resize((320, 200))
        ImageDraw.Draw(image).rectangle((0, 0, 320, 16), fill=(0, 0, 0))
        ImageDraw.Draw(image).text((4, 2), text, fill=(255, 255, 255))
        return np.asarray(image)

    def sheet(rows, path):
        grid = np.concatenate([np.concatenate([labelled(t, f'{name}  tick {k}') for t, k in zip(sheets[name], (0, 100, 135, 180))], 1)
                               for name in rows], 0)
        Image.fromarray(grid).save(path, quality=88)

    external = [n for n in names if not n.startswith('wrist')]
    for family in ('front', 'side_right', 'side_left'):
        sheet([n for n in external if n.startswith(family+'_')], out/'images'/f'{family}.jpg')
    sheet([n for n in external if n.startswith(('diag', 'over', 'base'))], out/'images'/'other.jpg')
    sheet([n for n in names if n.startswith('wrist')], out/'images'/'wrist.jpg')
    for name in ('overview', 'topdown'):
        Image.fromarray(sheets[name][0]).save(out/'images'/f'{name}.jpg', quality=90)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--grid', type=int, default=5, help='targets per axis over the object table')
    parser.add_argument('--every', type=int, default=6, help='measure every Nth tick')
    args = parser.parse_args()
    _setup()
    out = args.output_dir.resolve()
    (out/'scenes').mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    info = simulate(args, out)
    names, stats, sheets, central = render(args, out, info)
    table = summarize(names, stats, info)
    sheets_out(out, sheets, names)
    from robolab.lab_scene import D455_RGB, LabLayout
    from dataclasses import asdict
    result = dict(layout=asdict(LabLayout()), d455_rgb=D455_RGB, base_x=info['base_x'], cameras=info['cameras'],
                  wrist=info['wrist'], scenes=info['scenes'], sheet_scene=central, metrics=table,
                  render=dict(width=RENDER_W, height=RENDER_H, every=args.every),
                  elapsed_s=round(time.monotonic()-started, 1))
    (out/'metrics.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps(dict(event='finished', elapsed_s=result['elapsed_s'],
                          success=sum(s['success'] for s in info['scenes']), scenes=len(info['scenes']))), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
