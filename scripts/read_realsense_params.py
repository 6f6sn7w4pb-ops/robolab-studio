"""Read-only dump of the factory parameters of every connected Intel RealSense camera (D455).

  python read_realsense_params.py --out outputs/realsense_params
  python read_realsense_params.py --no-snapshot      # parameters only, no streaming
  python read_realsense_params.py --self-test        # virtual camera, no hardware needed

Each run makes a new timestamped folder under --out with:
  params.json          device info (serial, firmware, USB), every stream profile, intrinsics per
                       stream and resolution, extrinsics_to_color[<stream>] = {R, t_m} with
                       p_color = R p_stream + t_m (metres), the sensor options (exposure, gain,
                       white balance...) with their ranges, and the MuJoCo fovy ('sim')
  summary.txt          the numbers the simulation needs, in Chinese
  color_<serial>.png   one colour frame per camera (1280x800 if available), to tell the wrist camera
                       from the external one and to compare with the simulated view

Nothing is written to the cameras: no option is set and the firmware is not touched. The snapshot
streams colour only, no depth or infrared. Needs only pyrealsense2 and the standard library, so it
runs on Windows (where USB cameras are visible without usbipd) as well as on Linux.

MuJoCo 2.3.7 cameras take only fovy (square pixels, principal point at the image centre), so the
summary gives fovy = 2 atan(H / 2 fy) and how far the real camera is from that model.
"""
import argparse
import datetime
import json
import math
from pathlib import Path
import platform
import shutil
import struct
import sys
import tempfile
import time
import zlib

try:
    import pyrealsense2 as rs
except ImportError:
    sys.exit('pyrealsense2 is missing: pip install pyrealsense2')

SCHEMA = 'robolab.realsense_params.v1'
SIM = (1280, 800)              # resolution robolab/lab_scene.py renders (D455_RGB)
SNAPSHOT = (1280, 800, 30)     # D455 colour native mode
VIRTUAL_SERIAL = '000000000000'


def _name(value):
    return getattr(value, 'name', None) or str(value).split('.')[-1]


def _try(function, *args):
    try:
        return function(*args)
    except Exception:  # librealsense raises for whatever a device or backend does not support
        return None


def _plain(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value if isinstance(value, (bool, int, float, str, type(None))) else str(value)


def stream_key(profile):
    return _name(profile.stream_type()) + (str(profile.stream_index()) if profile.stream_index() else '')


def fov_deg(width, height, fx, fy, ppx, ppy):
    """Edge-to-edge field of view, the same formula as librealsense's rs2_fov."""
    return (math.degrees(math.atan2(ppx + .5, fx) + math.atan2(width - (ppx + .5), fx)),
            math.degrees(math.atan2(ppy + .5, fy) + math.atan2(height - (ppy + .5), fy)))


def intrinsics_dict(intrinsics):
    record = dict(width=intrinsics.width, height=intrinsics.height, fx=intrinsics.fx, fy=intrinsics.fy,
                  ppx=intrinsics.ppx, ppy=intrinsics.ppy, model=_name(intrinsics.model),
                  coeffs=list(intrinsics.coeffs))
    record['hfov_deg'], record['vfov_deg'] = fov_deg(record['width'], record['height'], record['fx'], record['fy'],
                                                     record['ppx'], record['ppy'])
    return record


def _same_intrinsics(a, b):
    keys = ('width', 'height', 'fx', 'fy', 'ppx', 'ppy', 'model', 'coeffs')
    return all(a[key] == b[key] for key in keys)


def sim_block(intrinsics):
    """How the colour stream at the simulated resolution maps onto a MuJoCo fovy camera."""
    main = next((r for r in intrinsics if r['stream'] == 'color' and (r['width'], r['height']) == SIM), None)
    if main is None:
        return None
    width, height = SIM
    return dict(width=width, height=height, fovy_deg=math.degrees(2*math.atan(height/(2*main['fy']))),
                fx_over_fy_minus_1=main['fx']/main['fy'] - 1,
                principal_offset_px=[main['ppx'] - (width - 1)/2, main['ppy'] - (height - 1)/2])


def options_dump(sensor):
    options = {}
    for option in _try(sensor.get_supported_options) or ():
        value = _try(sensor.get_option, option)
        if value is None:
            value = getattr(_try(sensor.get_option_value, option), 'value', None)
        record = dict(value=_plain(value), read_only=_try(sensor.is_option_read_only, option))
        option_range = _try(sensor.get_option_range, option)
        if option_range is not None:
            record.update(min=_plain(option_range.min), max=_plain(option_range.max),
                          step=_plain(option_range.step), default=_plain(option_range.default))
        description = _try(sensor.get_option_description, option)
        if description:
            record['description'] = description
        options[_name(option)] = record
    return options


def device_dump(device):
    info = {}
    for name, key in rs.camera_info.__members__.items():
        if _try(device.supports, key):
            value = _try(device.get_info, key)
            if value is not None:
                info[name] = value
    record = dict(info=info, sensors=[], profiles=[], intrinsics=[], extrinsics_to_color={})
    first = {}                               # one profile per stream, for the extrinsics
    for sensor in device.query_sensors():
        sensor_name = _try(sensor.get_info, rs.camera_info.name) or '?'
        streams = []
        for profile in _try(sensor.get_stream_profiles) or ():
            key = stream_key(profile)
            entry = dict(stream=key, format=_name(profile.format()), fps=profile.fps(), default=bool(profile.is_default()))
            if key not in streams:
                streams.append(key)
            first.setdefault(key, profile)   # extrinsics are per stream, not per resolution
            if profile.is_video_stream_profile():
                video = profile.as_video_stream_profile()
                entry.update(width=video.width(), height=video.height())
                intrinsics = _try(video.get_intrinsics)
                if intrinsics is not None:
                    found = intrinsics_dict(intrinsics)
                    known = next((r for r in record['intrinsics'] if r['stream'] == key and _same_intrinsics(r, found)), None)
                    if known is None:
                        record['intrinsics'].append(dict(stream=key, formats=[entry['format']], **found))
                    elif entry['format'] not in known['formats']:
                        known['formats'].append(entry['format'])
            record['profiles'].append(entry)
        depth_scale = _try(lambda: sensor.as_depth_sensor().get_depth_scale()) if _try(sensor.is_depth_sensor) else None
        record['sensors'].append(dict(name=sensor_name, streams=streams, options=options_dump(sensor),
                                      **({'depth_scale_m': depth_scale} if depth_scale else {})))
    color = first.get('color')
    for key, profile in first.items():
        extrinsics = _try(profile.get_extrinsics_to, color) if color is not None and key != 'color' else None
        if extrinsics is not None:
            r = list(extrinsics.rotation)    # librealsense stores the rotation column-major
            record['extrinsics_to_color'][key] = dict(R=[[r[0], r[3], r[6]], [r[1], r[4], r[7]], [r[2], r[5], r[8]]],
                                                      t_m=list(extrinsics.translation))
    record['intrinsics'].sort(key=lambda r: (r['stream'], -r['width']*r['height']))
    record['sim'] = sim_block(record['intrinsics'])
    return record


def write_png(path, width, height, rgb, row_bytes):
    raw = b''.join(b'\0' + rgb[y*row_bytes:y*row_bytes + 3*width] for y in range(height))

    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def read_png(path):
    """(width, height, rgb bytes) of a PNG written by write_png (self-test only)."""
    data, at, idat = Path(path).read_bytes(), 8, b''
    while at < len(data):
        length, kind = struct.unpack('>I4s', data[at:at + 8])
        body = data[at + 8:at + 8 + length]
        if kind == b'IHDR':
            width, height = struct.unpack('>II', body[:8])
        elif kind == b'IDAT':
            idat += body
        at += 12 + length
    raw, row = zlib.decompress(idat), 1 + 3*width
    return width, height, b''.join(raw[y*row + 1:(y + 1)*row] for y in range(height))


def snapshot_mode(record):
    modes = {(p['width'], p['height'], p['fps']) for p in record['profiles'] if p['stream'] == 'color' and p['format'] == 'rgb8'}
    if SNAPSHOT in modes:
        return SNAPSHOT
    return max(modes, key=lambda m: (m[2] <= 30, m[0]*m[1], m[2]), default=None)


def snapshot(context, serial, mode, path, settle):
    """Stream colour only, let auto exposure settle, save one frame as PNG."""
    width, height, fps = mode
    pipeline, config = rs.pipeline(context), rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, width, height, rs.format.rgb8, fps)
    profile = pipeline.start(config)
    try:
        streams = [stream_key(s) for s in profile.get_streams()]
        for _ in range(max(1, settle)):
            frames = pipeline.wait_for_frames(10000)
        color = frames.get_color_frame()
        width, height = color.get_width(), color.get_height()
        rgb = memoryview(color.get_data()).tobytes()
        metadata = {}
        for name, key in rs.frame_metadata_value.__members__.items():
            if _try(color.supports_frame_metadata, key):
                metadata[name] = _plain(_try(color.get_frame_metadata, key))
        streamed = intrinsics_dict(profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics())
    finally:
        pipeline.stop()
    write_png(path, width, height, rgb, len(rgb)//height)
    return dict(file=Path(path).name, width=width, height=height, fps=fps, streams=streams,
                metadata=metadata, intrinsics=streamed)


def summarize(report):
    host = report['host']
    lines = [f"RealSense 相机参数（只读）  {report['created']}",
             f"pyrealsense2 {host['pyrealsense2']} · {host['platform']} · Python {host['python']}",
             f"共 {len(report['devices'])} 台相机。完整参数（所有分辨率、深度和红外、曝光等选项）在 params.json。"]
    for number, record in enumerate(report['devices'], 1):
        info = record['info']
        lines += ['', f"== 相机 {number}：{info.get('name', '?')}，序列号 {info.get('serial_number', '?')} =="]
        firmware, recommended = info.get('firmware_version', '?'), info.get('recommended_firmware_version')
        lines.append(f"固件 {firmware}" + (f"（这个 SDK 推荐 {recommended}。读参数不需要升级，不要擅自升级实验室的相机）"
                                          if recommended and recommended != firmware else ''))
        usb = info.get('usb_type_descriptor', '?')
        lines.append(f"USB {usb}" + ('  ⚠ USB 2 连接：可用的分辨率会变少。换 USB 3 口（蓝色或标 SS 的口）和原装线后再读一次'
                                     if usb.startswith('2') else ''))
        sim, color = record.get('sim'), [r for r in record['intrinsics'] if r['stream'] == 'color']
        if sim is None:
            lines.append(f"⚠ 没有读到 {SIM[0]}×{SIM[1]} 的彩色内参（见下表和 params.json）")
        else:
            main = next(r for r in color if (r['width'], r['height']) == SIM)
            dx, dy = sim['principal_offset_px']
            lines += [f"彩色 {SIM[0]}×{SIM[1]} 内参：fx {main['fx']:.3f}  fy {main['fy']:.3f}  ppx {main['ppx']:.3f}  ppy {main['ppy']:.3f}",
                      f"  畸变模型 {main['model']}，系数 [{', '.join(f'{c:.5f}' for c in main['coeffs'])}]",
                      f"  视场角 {main['hfov_deg']:.2f}° × {main['vfov_deg']:.2f}°（水平 × 垂直，与 rs-enumerate-devices 算法相同）",
                      f"  仿真用 fovy = {sim['fovy_deg']:.3f}°（= 2·atan({SIM[1]}/2 ÷ fy)，对应 robolab/lab_scene.py 里 D455_RGB 的 vfov_deg）",
                      f"  MuJoCo 2.3.7 只认 fovy（正方形像素、主点在画面中心）。真机与它的差别："
                      f"fx/fy − 1 = {sim['fx_over_fy_minus_1']:+.3%}，主点偏离画面中心 ({dx:+.1f}, {dy:+.1f}) 像素"]
        if color:
            lines.append('彩色流各分辨率的内参（像素）和 RGB8 可用帧率：')
            for r in color:
                fps = sorted({p['fps'] for p in record['profiles'] if p['stream'] == 'color' and p['format'] == 'rgb8'
                              and (p['width'], p['height']) == (r['width'], r['height'])})
                fovy = math.degrees(2*math.atan(r['height']/(2*r['fy'])))
                lines.append(f"  {r['width']:>4}×{r['height']:<4}  fx {r['fx']:8.3f}  fy {r['fy']:8.3f}  ppx {r['ppx']:8.3f}"
                             f"  ppy {r['ppy']:8.3f}  fovy {fovy:6.2f}°  帧率 {','.join(map(str, fps)) or '-'}")
        shot = record.get('snapshot')
        if shot and 'file' in shot:
            lines.append(f"照片：{shot['file']}（{shot['width']}×{shot['height']}，只开了彩色流：{', '.join(shot['streams'])}）")
        elif shot:
            lines.append(f"照片没拍成：{shot.get('error')}（参数已经保存，不受影响）")
    if report['devices']:
        lines += ['', '下一步：看照片分清腕部相机和外部相机（腕部的照片里能看到夹爪手指），记下序列号和位置的对应关系。']
    return '\n'.join(lines) + '\n'


def save(folder, report):
    (folder/'params.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (folder/'summary.txt').write_text(summarize(report), encoding='utf-8')


def run(context, devices, folder, snapshots, settle):
    report = dict(schema=SCHEMA, created=datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                  host=dict(platform=platform.platform(), python=platform.python_version(), pyrealsense2=rs.__version__),
                  devices=[device_dump(device) for device in devices])
    folder.mkdir(parents=True, exist_ok=True)
    save(folder, report)                     # parameters are safe on disk before any streaming
    if snapshots:
        for record in report['devices']:
            serial, mode = record['info'].get('serial_number'), snapshot_mode(record)
            if mode is None:
                record['snapshot'] = dict(error='没有 RGB8 彩色流')
                continue
            try:
                record['snapshot'] = snapshot(context, serial, mode, folder/f'color_{serial}.png', settle)
            except Exception as error:
                record['snapshot'] = dict(error=f'{type(error).__name__}: {error}')
        save(folder, report)
    return report


def virtual_camera():
    """A software stand-in for a D455 with known numbers (self-test only)."""
    device = rs.software_device()
    for key, value in ((rs.camera_info.name, 'Virtual D455 (self-test)'), (rs.camera_info.serial_number, VIRTUAL_SERIAL),
                       (rs.camera_info.firmware_version, '5.0.0.0'), (rs.camera_info.usb_type_descriptor, '3.2')):
        device.register_info(key, value)
    profiles, uid = {}, 0
    for sensor_name, stream, fmt, bpp, modes in (
            ('RGB Camera', rs.stream.color, rs.format.rgb8, 3, ((1280, 800, 645.2, 644.8, 641.5, 401.0), (640, 400, 322.6, 322.4, 320.5, 200.25))),
            ('Stereo Module', rs.stream.depth, rs.format.z16, 2, ((640, 480, 390.0, 390.0, 320.0, 240.0),))):
        sensor = device.add_sensor(sensor_name)
        for width, height, fx, fy, ppx, ppy in modes:
            intrinsics = rs.intrinsics()
            intrinsics.width, intrinsics.height, intrinsics.fx, intrinsics.fy = width, height, fx, fy
            intrinsics.ppx, intrinsics.ppy, intrinsics.model = ppx, ppy, rs.distortion.inverse_brown_conrady
            intrinsics.coeffs = [-.055, .065, .0005, .0006, -.021]
            video = rs.video_stream()
            video.type, video.index, video.uid, video.width, video.height = stream, 0, uid, width, height
            video.fps, video.bpp, video.fmt, video.intrinsics = 30, bpp, fmt, intrinsics
            profiles[(_name(stream), width)] = sensor.add_video_stream(video, uid == 0)
            uid += 1
        if stream == rs.stream.color:
            sensor.add_read_only_option(rs.option.exposure, 166.)
            color_sensor = sensor
    extrinsics = rs.extrinsics()
    extrinsics.rotation, extrinsics.translation = [1., 0, 0, 0, 1, 0, 0, 0, 1], [-.059, 0, 0]
    profiles[('depth', 640)].register_extrinsics_to(profiles[('color', 1280)], extrinsics)
    return device, color_sensor, profiles[('color', 1280)]


def _pattern(width, height):
    return b''.join(bytes(v for x in range(width) for v in (x & 255, y & 255, (x + y) & 255)) for y in range(height))


def record_virtual(path, pixels, frames=20):
    """Record a short colour clip of the virtual camera (self-test only)."""
    device, color_sensor, color_profile = virtual_camera()
    recorder = rs.recorder(str(path), device)
    sensor = next(s for s in recorder.query_sensors() if s.get_info(rs.camera_info.name) == 'RGB Camera')
    sensor.open(color_profile)
    sensor.start(lambda frame: None)
    for number in range(frames):             # the frames a real camera would send
        frame = rs.software_video_frame()
        frame.pixels, frame.stride, frame.bpp = pixels, 3*SIM[0], 3
        frame.timestamp, frame.domain, frame.frame_number = number*1000/30, rs.timestamp_domain.hardware_clock, number
        frame.profile = color_profile.as_video_stream_profile()
        color_sensor.on_video_frame(frame)
        time.sleep(1/30)
    sensor.stop()
    sensor.close()                           # the file is complete once the recorder is released


def self_test(out):
    # 1. Parameter dump straight from a virtual camera with known numbers.
    device, _, color_profile = virtual_camera()
    record = device_dump(device)
    color = next(r for r in record['intrinsics'] if r['stream'] == 'color' and r['width'] == 1280)
    got = (color['fx'], color['fy'], color['ppx'], color['ppy'])
    assert all(abs(a - b) < 1e-3 for a, b in zip(got, (645.2, 644.8, 641.5, 401.0))), color   # stored as float32
    if hasattr(rs, 'rs2_fov'):
        theirs = rs.rs2_fov(color_profile.as_video_stream_profile().get_intrinsics())
        assert all(abs(a - b) < 1e-3 for a, b in zip((color['hfov_deg'], color['vfov_deg']), theirs)), (color, theirs)
    assert abs(record['sim']['fovy_deg'] - math.degrees(2*math.atan(400/644.8))) < 1e-4, record['sim']
    assert abs(record['sim']['principal_offset_px'][0] - 2.) < 1e-3, record['sim']
    assert [r['width'] for r in record['intrinsics'] if r['stream'] == 'color'] == [1280, 640], record['intrinsics']
    assert abs(record['extrinsics_to_color']['depth']['t_m'][0] + .059) < 1e-6, record['extrinsics_to_color']
    assert record['sensors'][0]['options']['exposure']['value'] == 166., record['sensors'][0]['options']
    # 2. The whole run, snapshot included, on a recording of it played back as a device of the context.
    #    librealsense 2.59 does not list software devices added with add_to (it keeps only a weak
    #    reference), and its recorder writes SQLite, which cannot lock files on a share such as
    #    \\wsl.localhost, so the clip goes to a local temporary folder.
    pixels = _pattern(*SIM)
    folder = out/('selftest-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    work = Path(tempfile.mkdtemp(prefix='realsense_selftest_'))
    try:
        record_virtual(work/'virtual.db3', pixels)
        context = rs.context()
        playback = context.load_device(str(work/'virtual.db3'))
        devices = [d for d in context.query_devices(int(rs.product_line.any))   # recordings count as non-Intel
                   if _try(d.get_info, rs.camera_info.serial_number) == VIRTUAL_SERIAL]
        assert len(devices) == 1, 'recorded virtual camera not listed by the context'
        report = run(context, devices, folder, snapshots=True, settle=5)
        del playback, devices, context
    finally:
        shutil.rmtree(work, ignore_errors=True)
    shot = report['devices'][0]['snapshot']
    assert 'file' in shot, shot
    assert shot['streams'] == ['color'], shot
    assert read_png(folder/shot['file']) == (SIM[0], SIM[1], pixels), 'snapshot PNG does not match the frame sent'
    loaded = json.loads((folder/'params.json').read_text(encoding='utf-8'))
    assert loaded['devices'][0]['info']['serial_number'] == VIRTUAL_SERIAL
    assert VIRTUAL_SERIAL in (folder/'summary.txt').read_text(encoding='utf-8')
    return folder


def main():
    for stream in (sys.stdout, sys.stderr):  # Chinese output also through a pipe (e.g. run from WSL)
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=Path('realsense_params'),
                        help='parent folder; each run writes a new timestamped subfolder (default: ./realsense_params)')
    parser.add_argument('--no-snapshot', action='store_true', help='read parameters only, do not stream')
    parser.add_argument('--settle-frames', type=int, default=60, help='frames skipped before the snapshot so auto exposure settles')
    parser.add_argument('--self-test', action='store_true', help='check this script against a virtual camera, no hardware needed')
    args = parser.parse_args()
    if args.self_test:
        folder = self_test(args.out)
        print(f'自检通过（pyrealsense2 {rs.__version__}，Python {platform.python_version()}）。示例输出：{folder.resolve()}')
        return 0
    context = rs.context()
    devices = list(context.query_devices())
    if not devices:
        print('没有检测到 RealSense 相机。请检查：\n'
              '  1. 相机接在 USB 3 口（蓝色或标 SS 的口），用原装线或 USB 3 线；\n'
              '  2. Windows 设备管理器的“照相机”或“图像设备”里能看到 Intel RealSense；\n'
              '  3. 没有别的程序（RealSense Viewer、ROS 节点等）占着相机；\n'
              '  4. 两台读不出来时先只插一台；拔插一次，等十秒再运行。', file=sys.stderr)
        return 2
    folder = args.out/datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    report = run(context, devices, folder, not args.no_snapshot, args.settle_frames)
    print(summarize(report))
    print(f'已保存到：{folder.resolve()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
