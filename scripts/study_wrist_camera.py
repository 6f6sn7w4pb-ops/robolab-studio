"""Wrist D455 mount study with the measured wrist unit (2026-11-25).

  study_wrist_camera.py --output-dir outputs/wrist_camera_study [--every 2]

The wrist camera is the lab's camera1 (lab_scene.D455_UNITS['wrist'], fovy 63.789 deg). A
candidate mount is the RGB lens' offset from the hand axis and its height below the flange (both
in the robosuite hand frame robot0_right_hand: z along the hand axis towards the fingertips,
fingers opening along y, camera on the +x side), with the tilt that puts the fingertip centre
(the grip site) on the optical axis. For every candidate:

1. Static clearance of the D455 body: both fingers from fully open to fully closed, the hand
   housing, link7, and link6 over joint 7's whole range; housing and links also for the real hand,
   whose fingers sit 6 mm further from the flange than robosuite's (see REAL_FINGER_SHIFT_M).
2. Rule-teacher grasps (actual OSC) in the lab layout. "lowest": the lowest grasp the stepped-bar
   family allows (thin end 32 mm high and 26 mm wide, thick end 55 mm high and 64 mm wide, 90 mm
   segments), thin end grasped, the thick segment once under the camera and once on the far side,
   3 x 3 grasp points on the object table. "grid": the 25 scenes of the 2026-11-17 study (default
   bar, an obstacle 16 cm away). Per tick, the D455 body's clearance to both tables, the target
   and the obstacle.
3. Segmentation renders of the wrist camera: where the requested part sits while the fingers
   close, how much of it is visible before the grasp, how much of the frame the gripper covers.

Cameras and the body box are only rendered or measured, never simulated, so every candidate is
judged on exactly the same motions. Writes metrics.json, summary.md and images/.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from study_lab_cameras import _setup, scenes as grid_scenes  # noqa: E402  (the 2026-11-17 grid)

PRE_GRASP = (12, 142)            # approach + descend + close
CLOSE = (122, 142)               # fingers closing
LOW = (60, 200)                  # descend, close and lift: the hand is near the table
RENDER_W, RENDER_H = 320, 200    # 16:10 like the D455's 1280 x 800
SHEET_W, SHEET_H = 640, 400
POLICY_SCALE = 224/RENDER_W      # the policy sees 1280 x 800 resized to 224 x 140
TCP_Z_M = .097                   # robosuite grip site in the hand frame
REAL_FINGER_SHIFT_M = .006       # franka_description finger joints at 0.0584 m, robosuite 0.0524 m
ENOUGH_M = .03                   # clearances beyond 30 mm are reported as a lower bound
GLASS_AHEAD_M = .005             # front glass ahead of the RGB optical centre (conservative)
OFFSETS_MM = (45, 50, 55, 60, 65, 70, 80)
HEIGHTS_MM = (0, 10, 20, 30, 40, 50)
LOWEST_BAR = dict(segment_length_m=.09, thick_width_m=.064, thin_width_m=.026, thick_height_m=.055,
                  thin_height_m=.032)
LIMITS_MM = dict(fingers=5., hand=3., link7=3., link6=5., table=10., target=10.)
CENTRE_BOX = .2                  # |dx|, |dy| <= 0.2 of the half width / height: the middle fifth (reported)


def mounts():
    out = []
    for offset in OFFSETS_MM:
        for height in HEIGHTS_MM:
            tilt = math.degrees(math.atan2(offset/1000, TCP_Z_M-height/1000))
            out.append(dict(name=f'wrist_o{offset}_h{height}', offset_mm=offset, height_mm=height,
                            tilt_deg=round(tilt, 2)))
    return out


def body_box(mount, dz=0., front_ahead_m=GLASS_AHEAD_M):
    """The physical D455 body (hand frame) for clearance checks; dz moves it along the hand axis."""
    from robolab.lab_scene import d455_body_box, wrist_camera_pose
    position, xyaxes, forward, _ = wrist_camera_pose(mount['offset_mm']/1000, mount['height_mm']/1000+dz,
                                                     mount['tilt_deg'])
    return d455_body_box(position, xyaxes, forward, front_ahead_m=front_ahead_m)


def geom_mesh(model, data, g):
    """World vertices and triangles of a box or mesh geom (None for other types)."""
    import mujoco
    import numpy as np
    from robolab.clearance import box_mesh
    rotation = data.geom_xmat[g].reshape(3, 3)
    if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = model.geom_dataid[g]
        vertices = model.mesh_vert[model.mesh_vertadr[mesh]:model.mesh_vertadr[mesh]+model.mesh_vertnum[mesh]]
        faces = model.mesh_face[model.mesh_faceadr[mesh]:model.mesh_faceadr[mesh]+model.mesh_facenum[mesh]]
        return data.geom_xpos[g]+vertices@rotation.T, np.asarray(faces)
    if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX:
        return box_mesh(data.geom_xpos[g], rotation.T, model.geom_size[g])
    return None


def meshes_in_hand(model, data, bodies):
    from robolab.lab_scene import HAND_BODY
    hand = model.body(HAND_BODY).id
    position, rotation = data.xpos[hand], data.xmat[hand].reshape(3, 3)
    ids = {model.body(name).id for name in bodies}
    out = []
    for g in range(model.ngeom):
        if model.geom_bodyid[g] in ids:
            mesh = geom_mesh(model, data, g)
            if mesh is not None:
                out.append(((mesh[0]-position)@rotation, mesh[1]))
    return out


def static_clearances(model, data, candidates):
    """Body clearances in the hand frame; fingers and link6 are swept over their joint ranges."""
    import mujoco
    import numpy as np
    from robolab.clearance import box_mesh_clearance
    saved = data.qpos.copy()
    finger_joints = [model.joint(f'gripper0_finger_joint{i}') for i in (1, 2)]
    fingers = []
    for q in np.linspace(0., .04, 9):
        data.qpos[finger_joints[0].qposadr[0]], data.qpos[finger_joints[1].qposadr[0]] = q, -q
        mujoco.mj_forward(model, data)
        fingers.append(meshes_in_hand(model, data, ('gripper0_leftfinger', 'gripper0_rightfinger',
                                                     'gripper0_finger_joint1_tip', 'gripper0_finger_joint2_tip')))
    hand = meshes_in_hand(model, data, ('gripper0_right_gripper',))
    link7 = meshes_in_hand(model, data, ('robot0_link7',))
    joint7 = model.joint('robot0_joint7')
    angles = np.linspace(*model.jnt_range[joint7.id], 13)
    link6 = []
    for angle in angles:
        data.qpos[joint7.qposadr[0]] = angle
        mujoco.mj_forward(model, data)
        link6.append(meshes_in_hand(model, data, ('robot0_link6',)))
    data.qpos[:] = saved
    mujoco.mj_forward(model, data)

    def clearance(box, meshes):
        return min(box_mesh_clearance(*box, vertices, faces, enough=ENOUGH_M) for vertices, faces in meshes)

    out = {}
    for mount in candidates:
        row = dict(fingers=min(clearance(body_box(mount), f) for f in fingers))
        for label, dz in (('sim', 0.), ('real', REAL_FINGER_SHIFT_M)):
            box = body_box(mount, dz)
            per_angle = [clearance(box, meshes) for meshes in link6]
            row[f'hand_{label}'] = clearance(box, hand)
            row[f'link7_{label}'] = clearance(box, link7)
            row[f'link6_{label}'] = min(per_angle)
            row[f'link6_{label}_close_joint7_deg'] = [round(math.degrees(a), 1) for a, c in zip(angles, per_angle)
                                                       if c < LIMITS_MM['link6']/1000]
        out[mount['name']] = {k: (round(v*1000, 1) if isinstance(v, float) else v) for k, v in row.items()}
    return out


def lowest_scenes(layout, base_x):
    """The lowest grasp of the bar family at 3 x 3 grasp points, thick segment under the camera or not."""
    from robolab.p4_parts import SteppedBar
    from robolab.p4_pilot_scene import PilotScene
    bar = SteppedBar(**LOWEST_BAR)
    near, far = layout.object_table_x(base_x)
    half = layout.object_table_length/2
    out = []
    for i, x_from_base in enumerate((.33, .50, .67)):
        for j, y in enumerate((-.30, 0., .30)):
            # yaw 0: thin end towards the far edge, so the camera (on the far side of the hand) is over
            # the thin end's tail; yaw 180: the thick segment lies under the camera.
            for yaw, case in ((0., 'thick_away'), (180., 'thick_under_camera')):
                grasp = (base_x+x_from_base, y)
                shift = bar.segment_length_m/2
                centre = (round(grasp[0]-shift*math.cos(math.radians(yaw)), 4),
                          round(grasp[1]-shift*math.sin(math.radians(yaw)), 4))
                obstacle = (far-.05, -half+.05 if y >= 0 else half-.05)       # far corner, out of the way
                scene = PilotScene(f'lowest_{i}{j}_{case}', 'box_step', 'lab', bar, centre, yaw, obstacle,
                                   (.022, .02, .03))
                out.append((scene, 'thin', dict(set='lowest', case=case, x_from_base=x_from_base, y=y, yaw=yaw)))
    return out


def add_wrist_cameras(xml_text, candidates):
    import xml.etree.ElementTree as ET
    from robolab.lab_scene import D455_UNITS, HAND_BODY, _fmt, wrist_camera_pose
    root = ET.fromstring(xml_text)
    hand = next(body for body in root.iter('body') if body.get('name') == HAND_BODY)
    for mount in candidates:
        position, xyaxes, _, _ = wrist_camera_pose(mount['offset_mm']/1000, mount['height_mm']/1000, mount['tilt_deg'])
        ET.SubElement(hand, 'camera', name=mount['name'], mode='fixed', pos=_fmt(position), xyaxes=_fmt(xyaxes),
                      fovy=f"{D455_UNITS['wrist']['fovy_deg']:.6g}")
    return ET.tostring(root, encoding='unicode')


def simulate(out, plan, candidates):
    import numpy as np
    import xml.etree.ElementTree as ET
    from robolab.engine import Engine
    from robolab.lab_scene import LabLayout, lab_xml_hook
    from robolab.p4_osc import OSC_PERIOD_S, SyntheticOsc
    from robolab.p4_parts import TARGET, PartEvidence, PartWaypoints

    layout = LabLayout()
    engine = Engine()
    records, static = [], None
    for scene, part, where in plan:
        started = time.monotonic()
        engine.load_task(seed=0, init_index=0)
        hook = lab_xml_hook(layout)
        scene.install(engine, xml_hook=lambda sc, xml, hook=hook: add_wrist_cameras(hook(sc, xml), candidates))
        if static is None:
            static = static_clearances(engine.model, engine.data, candidates)
        osc = SyntheticOsc(engine)
        qadr_z = int(engine.model.joint(TARGET+'_joint').qposadr[0])+2
        evidence = PartEvidence(part, float(engine.data.qpos[qadr_z]), sample_period_s=OSC_PERIOD_S)
        path = PartWaypoints(engine, part, align_yaw=True)
        qpos = [engine.data.qpos.copy()]
        error = None
        try:
            for _, position, rotation, opening in path.commands(OSC_PERIOD_S):
                sample = osc.step(osc.action_toward(position, rotation, opening))
                evidence.observe(sample, sample['state_at_solver']['qpos'][qadr_z])
                qpos.append(engine.data.qpos.copy())
        except Exception as exc:          # an unreachable pose is a result, not a crash
            error = str(exc)[:300]
        result = evidence.result()
        name = scene.scene_id
        np.savez_compressed(out/'scenes'/f'{name}.npz', qpos=np.asarray(qpos))
        (out/'scenes'/f'{name}.xml').write_text(engine.base_xml, encoding='utf-8')
        success = error is None and part in result['held_parts'] and result['finger_obstacle_frames'] == 0
        records.append(dict(scene=name, part=part, **where, success=success, held_parts=result['held_parts'],
                            error=error, ticks=len(qpos)-1, wall_s=round(time.monotonic()-started, 1)))
        print(json.dumps(records[-1]), flush=True)
    base_x = float(ET.fromstring(engine.base_xml).find(".//body[@name='robot0_base']").get('pos').split()[0])
    engine.close()
    return records, static, base_x


def measure(out, records, candidates, every, workers):
    """Per scene (in parallel worker processes): clearances over the motion and the wrist views."""
    import multiprocessing
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[name] = '1'              # one thread per worker; the workers are the parallelism
    jobs = [(out, record, candidates, every) for record in records]
    results = {}
    with multiprocessing.get_context('spawn').Pool(workers) as pool:
        for scene, result in pool.imap_unordered(measure_scene, jobs):
            results[scene] = result
            print(json.dumps(dict(event='measured', scene=scene)), flush=True)
    return {record['scene']: results[record['scene']] for record in records}


def measure_scene(job):
    """One scene: the D455 body's clearances over the motion and every candidate's wrist view."""
    out, record, candidates, every = job
    _setup()
    import mujoco
    import numpy as np
    from robolab.clearance import box_mesh_clearance
    from robolab.engine import dispose_renderer
    from robolab.lab_scene import HAND_BODY
    from robolab.p4_parts import TARGET
    from robolab.p4_pilot_scene import OBSTACLE

    boxes = {m['name']: body_box(m) for m in candidates}
    xml = (out/'scenes'/f"{record['scene']}.xml").read_text(encoding='utf-8')
    qpos = np.load(out/'scenes'/f"{record['scene']}.npz")['qpos']
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    hand = model.body(HAND_BODY).id
    robot_root = model.body('robot0_base').id
    gripper = set()
    for g in range(model.ngeom):
        if model.body_rootid[model.geom_bodyid[g]] == robot_root and model.geom_group[g] == 1:
            model.geom_group[g] = 5                       # the robot, drawn or hidden as one group
            body = model.geom_bodyid[g]
            while body and body != hand:
                body = model.body_parentid[body]
            if body == hand:
                gripper.add(g)
    gripper = np.array(sorted(gripper))
    parts = {p: model.geom(f'{TARGET}_{p}').id for p in ('thick', 'thin')}
    requested = parts[record['part']]
    others = dict(object_table=model.geom('lab_object_table_top').id, robot_table=model.geom('lab_robot_table_top').id,
                  target_thick=parts['thick'], target_thin=parts['thin'], obstacle=model.geom(OBSTACLE).id)
    with_robot, no_robot = mujoco.MjvOption(), mujoco.MjvOption()
    for option in (with_robot, no_robot):
        option.geomgroup[:] = 0
        option.geomgroup[1] = option.geomgroup[2] = 1
        option.sitegroup[:] = 0
    with_robot.geomgroup[5] = 1
    seg = mujoco.Renderer(model, RENDER_H, RENDER_W)
    seg.enable_segmentation_rendering()
    rendered = set(range(PRE_GRASP[0], min(PRE_GRASP[1], len(qpos)), 6)) | set(range(CLOSE[0], min(CLOSE[1], len(qpos)), 2))
    near_table = set(range(LOW[0], min(LOW[1], len(qpos)), every))     # the hand is low: every Nth tick
    ticks = sorted(set(range(0, len(qpos), 6)) | near_table | rendered)
    clear = {m['name']: {k: math.inf for k in others} for m in candidates}
    where = {m['name']: {} for m in candidates}
    view = {m['name']: [] for m in candidates}
    for tick in ticks:
        data.qpos[:] = qpos[tick]
        mujoco.mj_forward(model, data)
        position, rotation = data.xpos[hand], data.xmat[hand].reshape(3, 3)
        local = {}
        for k, g in others.items():
            vertices, faces = geom_mesh(model, data, g)
            local[k] = ((vertices-position)@rotation, faces)
        for mount in candidates:
            name = mount['name']
            for k, (vertices, faces) in local.items():
                c = box_mesh_clearance(*boxes[name], vertices, faces, enough=ENOUGH_M)
                if c < clear[name][k]:
                    clear[name][k], where[name][k] = c, tick
        if tick not in rendered:
            continue
        for mount in candidates:
            camera = model.camera(mount['name']).id
            ids = []
            for option in (no_robot, with_robot):
                seg.update_scene(data, camera=camera, scene_option=option)
                pixels = seg.render()
                ids.append(np.where(pixels[..., 1] == mujoco.mjtObj.mjOBJ_GEOM, pixels[..., 0], -1))
            bare, seen = ids
            row = dict(tick=tick, req_bare=int((bare == requested).sum()), req_seen=int((seen == requested).sum()),
                       gripper=float(np.isin(seen, gripper).mean()))
            for prefix, image in (('', seen), ('bare_', bare)):
                rows, cols = np.nonzero(image == requested)
                if len(rows):
                    row[prefix+'dx'] = float((cols.mean()-(RENDER_W-1)/2)/(RENDER_W/2))
                    row[prefix+'dy'] = float((rows.mean()-(RENDER_H-1)/2)/(RENDER_H/2))
            view[mount['name']].append(row)
    dispose_renderer(seg)
    return record['scene'], dict(clearance_mm={n: {k: round(v*1000, 1) for k, v in c.items()} for n, c in clear.items()},
                                 clearance_tick=where, view=view)


def summarize(candidates, records, static, measured):
    import numpy as np
    table = {}
    ok = {r['scene'] for r in records if r['success']}
    for mount in candidates:
        name = mount['name']
        row = dict(mount, **static[name])
        for subset in ('lowest', 'grid'):
            scenes = [r['scene'] for r in records if r['set'] == subset]
            for k in ('object_table', 'robot_table', 'target_thick', 'target_thin', 'obstacle'):
                row[f'{subset}_{k}_mm'] = min(measured[s]['clearance_mm'][name][k] for s in scenes)
            row[f'{subset}_target_mm'] = min(row[f'{subset}_target_thick_mm'], row[f'{subset}_target_thin_mm'])
            row[f'{subset}_table_mm'] = min(row[f'{subset}_object_table_mm'], row[f'{subset}_robot_table_mm'])
        row['grid_obstacle_scenes_below_10mm'] = sum(measured[r['scene']]['clearance_mm'][name]['obstacle'] < 10.
                                                     for r in records if r['set'] == 'grid')
        views = [v for s in ok for v in measured[s]['view'][name]]
        close = [v for v in views if CLOSE[0] <= v['tick'] < CLOSE[1]]
        pre = [v for v in views if PRE_GRASP[0] <= v['tick'] < PRE_GRASP[1]]
        centred = [v for v in close if 'dx' in v and abs(v['dx']) <= CENTRE_BOX and abs(v['dy']) <= CENTRE_BOX]
        outline = [v for v in close if 'bare_dx' in v and abs(v['bare_dx']) <= CENTRE_BOX and abs(v['bare_dy']) <= CENTRE_BOX]
        seen = [v['req_seen']/v['req_bare'] if v['req_bare'] else 0. for v in pre]
        usable = [v['req_bare'] and v['req_seen']/v['req_bare'] >= .5 and 'dx' in v and abs(v['dx']) <= .8
                  and abs(v['dy']) <= .8 for v in pre]
        row.update(
            close_visible=round(float(np.mean(['dx' in v for v in close])), 3) if close else None,
            close_centred=round(len(centred)/len(close), 3) if close else None,
            close_outline_centred=round(len(outline)/len(close), 3) if close else None,
            close_median_bare_dy=round(float(np.median([v['bare_dy'] for v in close if 'bare_dx' in v])), 3) if close else None,
            close_median_dx=round(float(np.median([v['dx'] for v in close if 'dx' in v])), 3) if close else None,
            close_median_dy=round(float(np.median([v['dy'] for v in close if 'dx' in v])), 3) if close else None,
            close_max_offset=round(float(max(max(abs(v['dx']), abs(v['dy'])) for v in close if 'dx' in v)), 3)
            if close else None,
            close_gripper=round(float(np.mean([v['gripper'] for v in close])), 3) if close else None,
            close_part_px_224=round(float(np.median([v['req_seen'] for v in close]))*POLICY_SCALE**2, 1) if close else None,
            pre_grasp_visible=round(float(np.mean(seen)), 3) if seen else None,
            pre_grasp_usable=round(float(np.mean(usable)), 3) if usable else None,
            pre_grasp_gripper=round(float(np.mean([v['gripper'] for v in pre])), 3) if pre else None)
        fails = []
        for key, limit in (('fingers', 'fingers'), ('hand_sim', 'hand'), ('hand_real', 'hand'), ('link7_sim', 'link7'),
                           ('link7_real', 'link7'), ('link6_sim', 'link6'), ('link6_real', 'link6'),
                           ('lowest_table_mm', 'table'), ('lowest_target_mm', 'target')):
            if row[key] < LIMITS_MM[limit]:
                fails.append(key)
        row['fails'] = fails
        table[name] = row
    feasible = [r for r in table.values() if not r['fails']]
    feasible.sort(key=lambda r: (-round(r['pre_grasp_usable'], 2), r['close_gripper'], r['offset_mm'], r['height_mm']))
    return table, [r['name'] for r in feasible]


def write_summary(out, table, ranking, records, elapsed):
    lines = ['# Wrist D455 mount study', '',
             f"Scenes: {sum(r['set'] == 'lowest' for r in records)} lowest-grasp, {sum(r['set'] == 'grid' for r in records)} grid; "
             f"successful grasps {sum(r['success'] for r in records)}/{len(records)}. Elapsed {elapsed:.0f} s.", '',
             'Limits (mm): ' + ', '.join(f'{k} >= {v:g}' for k, v in LIMITS_MM.items()) +
             '. The optical axis passes through the grasp point; "outline" is the requested part without the robot drawn,'
             ' "visible" with it (fraction of closing ticks whose centroid lies in the middle fifth).', '',
             '| mount | tilt | fingers | hand sim/real | link7 sim/real | link6 sim/real | lowest table | lowest target | '
             'grid obstacle | outline centred | visible centred | median dy outline / visible | close gripper | '
             'pre usable | pre visible | fails |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for name, r in table.items():
        lines.append(f"| {name} | {r['tilt_deg']:.1f} | {r['fingers']} | {r['hand_sim']}/{r['hand_real']} | "
                     f"{r['link7_sim']}/{r['link7_real']} | {r['link6_sim']}/{r['link6_real']} | {r['lowest_table_mm']} | "
                     f"{r['lowest_target_mm']} | {r['grid_obstacle_mm']} | {r['close_outline_centred']} | {r['close_centred']} | "
                     f"{r['close_median_bare_dy']} / {r['close_median_dy']} | {r['close_gripper']} | {r['pre_grasp_usable']} | "
                     f"{r['pre_grasp_visible']} | {', '.join(r['fails'])} |")
    lines += ['', 'Feasible, best first: ' + (', '.join(ranking) if ranking else 'none')]
    (out/'summary.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def sheets(out, records, chosen):
    """RGB views of the chosen mount: wrist camera and a close-up of the D455 on the hand."""
    import mujoco
    import numpy as np
    import xml.etree.ElementTree as ET
    from PIL import Image, ImageDraw
    from robolab.engine import dispose_renderer
    from robolab.lab_scene import HAND_BODY, _fmt, camera_axes
    (out/'images').mkdir(exist_ok=True)
    picks = [r for r in records if r['success'] and r['set'] == 'grid'][:1]
    picks += [r for r in records if r['success'] and r.get('case') == 'thick_under_camera'][:1]
    centre, axes, half = body_box(chosen, front_ahead_m=-.001)     # behind the lens, as rendered in the dataset
    closeup = np.array([.26, .22, -.06])                            # hand frame: beside, in front and above
    for record in picks:
        root = ET.fromstring((out/'scenes'/f"{record['scene']}.xml").read_text(encoding='utf-8'))
        hand = next(body for body in root.iter('body') if body.get('name') == HAND_BODY)
        ET.SubElement(hand, 'geom', name='d455_body', type='box', contype='0', conaffinity='0', group='1',
                      rgba='.12 .12 .13 1', pos=_fmt(centre), xyaxes=_fmt(np.r_[axes[0], axes[1]]), size=_fmt(half))
        ET.SubElement(hand, 'camera', name='closeup', mode='fixed', pos=_fmt(closeup), fovy='40',
                      xyaxes=_fmt(camera_axes(closeup, (.03, 0., .05), up=(0., 0., -1.))))
        model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding='unicode'))
        model.vis.global_.offwidth, model.vis.global_.offheight = 1600, 1000
        data = mujoco.MjData(model)
        qpos = np.load(out/'scenes'/f"{record['scene']}.npz")['qpos']
        renderer = mujoco.Renderer(model, SHEET_H, SHEET_W)
        tiles = []
        for camera in (chosen['name'], 'closeup'):
            row = []
            for tick in (0, 100, 135, 180):
                data.qpos[:] = qpos[min(tick, len(qpos)-1)]
                mujoco.mj_forward(model, data)
                renderer.update_scene(data, camera=model.camera(camera).id)
                image = Image.fromarray(renderer.render()).resize((320, 200))
                ImageDraw.Draw(image).rectangle((0, 0, 320, 16), fill=(0, 0, 0))
                ImageDraw.Draw(image).text((4, 2), f'{camera}  tick {tick}', fill=(255, 255, 255))
                row.append(np.asarray(image))
            tiles.append(np.concatenate(row, 1))
        dispose_renderer(renderer)
        Image.fromarray(np.concatenate(tiles, 0)).save(out/'images'/f"{record['scene']}.jpg", quality=90)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--every', type=int, default=2, help='measure clearances every Nth tick')
    parser.add_argument('--quick', action='store_true', help='4 mounts and 3 scenes, to check the pipeline')
    parser.add_argument('--workers', type=int, default=8, help='processes for the measurement pass')
    args = parser.parse_args()
    _setup()
    out = args.output_dir.resolve()
    (out/'scenes').mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    import xml.etree.ElementTree as ET
    from dataclasses import asdict
    from robolab.engine import Engine
    from robolab.lab_scene import D455_LENS_FROM_CENTRE_M, D455_RGB, D455_UNITS, LabLayout
    layout = LabLayout()
    probe = Engine()
    probe.load_task(seed=0, init_index=0)
    base_x = float(ET.fromstring(probe.base_xml).find(".//body[@name='robot0_base']").get('pos').split()[0])
    probe.close()
    candidates = mounts()
    plan = lowest_scenes(layout, base_x)
    grid = [(scene, part, dict(set='grid', **where)) for scene, part, where in grid_scenes(layout, base_x, 5)]
    if args.quick:
        candidates = [m for m in candidates if m['name'] in ('wrist_o45_h0', 'wrist_o55_h20', 'wrist_o60_h0',
                                                             'wrist_o60_h20')]
        plan, grid = [p for p in plan if p[0].scene_id.startswith('lowest_11')], grid[12:13]
    plan += grid
    records, static, _ = simulate(out, plan, candidates)
    measured = measure(out, records, candidates, args.every, args.workers)
    table, ranking = summarize(candidates, records, static, measured)
    elapsed = time.monotonic()-started
    write_summary(out, table, ranking, records, elapsed)
    if ranking:
        sheets(out, records, table[ranking[0]])
    result = dict(layout=asdict(layout), wrist_unit=D455_UNITS['wrist'], d455_body_m=D455_RGB['body_m'],
                  lens_from_centre_m=D455_LENS_FROM_CENTRE_M, tcp_z_m=TCP_Z_M, real_finger_shift_m=REAL_FINGER_SHIFT_M,
                  enough_m=ENOUGH_M, limits_mm=LIMITS_MM, centre_box=CENTRE_BOX, lowest_bar=LOWEST_BAR,
                  render=dict(width=RENDER_W, height=RENDER_H, every=args.every), scenes=records, mounts=table,
                  ranking=ranking, per_scene=measured, elapsed_s=round(elapsed, 1))
    (out/'metrics.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps(dict(event='finished', elapsed_s=round(elapsed, 1), feasible=len(ranking),
                          best=ranking[:5])), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
