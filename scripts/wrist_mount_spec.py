"""Numbers for printing the wrist D455 bracket (NumPy only).

  wrist_mount_spec.py --offset-mm 60 --height-mm 20 [--tilt-deg 37.9] [--json out.json]

Frames, all right-handed, millimetres:
  F  Franka flange frame (franka_description panda_link8): origin on the flange face, z out of the
     flange along the hand axis towards the fingertips.
  H  Franka Hand frame (panda_hand): F turned -45 deg about z (panda_hand_joint). Fingers open along
     y, the camera sits on the +x side. robosuite's robot0_right_hand is this frame too.
The simulation's fingers are 6.0 mm closer to the flange than the real hand's (robosuite places
the finger joints at z = 52.4 mm, franka_description at 58.4 mm; same meshes). The real camera
keeps the simulated camera's pose relative to the fingers and the grasp point, so it sits
6.0 mm further from the flange than in the simulation. The tilt defaults to aiming the optical
axis at the simulated grasp point (97.0 mm), which on the real hand is 103.0 mm (Franka's TCP:
103.4 mm).
"""
import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robolab.lab_scene import D455_LENS_FROM_CENTRE_M, D455_RGB, D455_UNITS, d455_body_box, wrist_camera_pose  # noqa: E402

SIM_TCP_MM = 97.0             # robosuite grip site in H
REAL_SHIFT_MM = 6.0           # franka_description finger joints 58.4 mm, robosuite 52.4 mm
FRANKA_TCP_MM = 103.4         # Franka's default end-effector frame (F_T_EE), along z
GLASS_AHEAD_MM = 5.0          # front glass ahead of the RGB optical centre (conservative)
H_IN_F = np.array([[math.cos(-math.pi/4), -math.sin(-math.pi/4), 0.],
                   [math.sin(-math.pi/4), math.cos(-math.pi/4), 0.],
                   [0., 0., 1.]])        # columns: H's axes in F


def spec(offset_mm, height_mm, tilt_deg=None):
    if tilt_deg is None:
        tilt_deg = math.degrees(math.atan2(offset_mm, SIM_TCP_MM-height_mm))
    position, xyaxes, forward, _ = wrist_camera_pose(offset_mm/1000, height_mm/1000, tilt_deg)
    centre, axes, half = d455_body_box(position, xyaxes, forward, front_ahead_m=GLASS_AHEAD_MM/1000)
    shift = np.array([0., 0., REAL_SHIFT_MM])
    lens_sim = position*1000
    lens_real = lens_sim+shift
    centre_real = centre*1000+shift
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
    corners = centre_real+(signs*half*1000)@axes
    right, up = xyaxes[:3], xyaxes[3:]
    tcp = np.array([0., 0., SIM_TCP_MM])+shift
    aim_error = math.degrees(math.acos(float(np.clip(forward@(tcp-lens_real)/np.linalg.norm(tcp-lens_real), -1, 1))))
    to_franka_tcp = np.array([0., 0., FRANKA_TCP_MM])-lens_real

    def both(v, point=True):
        """The same point (mm, 2 decimals) or direction (4 decimals) in H and in F."""
        v = np.asarray(v, float)
        digits = 2 if point else 4
        return dict(H=np.round(v, digits).tolist(), F=np.round(H_IN_F@v, digits).tolist())

    return dict(
        inputs=dict(offset_mm=offset_mm, height_mm_sim=height_mm, tilt_deg=round(tilt_deg, 2)),
        wrist_unit=D455_UNITS['wrist'],
        lens_sim_H_mm=np.round(lens_sim, 2).tolist(),
        lens_real=both(lens_real),
        optical_axis=both(forward, point=False),
        image_right=both(right, point=False),
        image_up=both(up, point=False),
        tilt_from_hand_axis_deg=round(tilt_deg, 2),
        lens_to_real_grasp_point_mm=round(float(np.linalg.norm(tcp-lens_real)), 1),
        optical_axis_miss_real_grasp_point_deg=round(aim_error, 2),
        optical_axis_miss_franka_tcp_deg=round(math.degrees(math.acos(float(forward@to_franka_tcp/np.linalg.norm(to_franka_tcp)))), 2),
        body=dict(size_mm=[round(v*1000, 1) for v in D455_RGB['body_m']],
                  lens_from_centre_mm=round(D455_LENS_FROM_CENTRE_M*1000, 1), glass_ahead_of_lens_mm=GLASS_AHEAD_MM,
                  centre_real=both(centre_real),
                  bottom_face_centre_real=both(centre_real-up*half[1]*1000),
                  bottom_face_normal=both(-up, point=False),
                  back_face_centre_real=both(centre_real-forward*half[2]*1000),
                  back_face_normal=both(-forward, point=False),
                  corners_real_H=np.round(corners, 1).tolist(),
                  extent_real_H=dict(min=np.round(corners.min(0), 1).tolist(), max=np.round(corners.max(0), 1).tolist())),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--offset-mm', type=float, required=True, help='lens distance from the hand axis (x in H)')
    parser.add_argument('--height-mm', type=float, required=True, help='lens z in the simulated hand frame')
    parser.add_argument('--tilt-deg', type=float, help='default: aim at the grasp point')
    parser.add_argument('--json', type=Path, help='also write the numbers here')
    args = parser.parse_args()
    numbers = spec(args.offset_mm, args.height_mm, args.tilt_deg)
    text = json.dumps(numbers, indent=1, ensure_ascii=False)
    print(text)
    if args.json:
        args.json.write_text(text+'\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
