"""Protocol v1: newline JSON on stdin/stdout, diagnostics only on stderr.

Image frames are JPEG/base64, bounded at 15 fps. MuJoCo and GL remain on this
process's main thread. Qt never imports robosuite, OpenCV, numpy or MuJoCo.
"""
import base64
import contextlib
import io
import json
import os
import queue
import sys
import threading
import time
import traceback

OUTPUT = sys.stdout

def emit(event, **payload):
    OUTPUT.write(json.dumps(dict(event=event, **payload), allow_nan=False, separators=(',', ':'))+'\n')
    OUTPUT.flush()

def reader(commands):
    for line in sys.stdin:
        try:
            if len(line) > 2_000_000:
                raise ValueError('Command too large')
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError('Command must be an object')
            commands.put(command)
        except Exception as error:
            commands.put({'id': None, 'op': '_bad_message', 'args': {'reason': str(error)}})
    commands.put({'id': None, 'op': 'shutdown'})

def dispatch(engine, op, args):
    if op == '_bad_message': raise ValueError(args['reason'])
    if op == 'list_tasks': return engine.tasks(args.get('suite', 'libero_object'))
    if op == 'load_task':
        return engine.load_task(args.get('suite', 'libero_object'), args.get('index', 0), args.get('seed', 0), args.get('init_index', 0))
    if op == 'play': engine.play()
    elif op == 'pause': engine.pause()
    elif op in ('stop', 'reset'): engine.reset()
    elif op == 'step': engine.pause(); engine.step()
    elif op == 'mode': engine.set_mode(args['mode'])
    elif op == 'joint': engine.set_joint(args['index'], args['radians'])
    elif op == 'jog_xyz': return engine.jog_xyz(args['delta'], args.get('frame', 'world'))
    elif op == 'contact_configure': return engine.configure_contacts(args.get('enabled', True), args.get('target_body','alphabet_soup_1_main'), args.get('force_epsilon_N',1e-6))
    elif op == 'contact_sample': return {'status': engine.contact_sampling_status(), 'sample': engine.contact_sample()}
    elif op == 'contact_view': return engine.configure_contact_view(args['enabled'], args.get('contact_filter', 'target'), args.get('forces', False))
    elif op == 'records_list':
        from .episode_store import list_episodes
        return list_episodes(args.get('root'))
    elif op == 'replay_open': return engine.open_replay(args['directory'])
    elif op == 'replay_seek':
        return engine.replay_frame(args['index'], contact_view=args.get('contact_view', False),
                                   contact_filter=args.get('contact_filter', 'target'),
                                   forces=args.get('forces', False), camera=args.get('camera', 'agentview'))
    elif op == 'replay_close': engine.close_replay(); return {'closed': True}
    elif op == 'gripper': engine.set_gripper(args['opening'])
    elif op == 'policy_scan':
        from .policy import scan_checkpoints
        return scan_checkpoints()
    elif op == 'policy_configure': return engine.configure_policy(args.get('kind','mock'),args.get('checkpoint',''),args.get('endpoint',''))
    elif op == 'policy_load': return engine.load_policy()
    elif op == 'policy_preview':
        if not engine.vla: raise ValueError('请先配置策略适配器')
        observation=engine.policy_observation()
        result=engine.vla.predict(observation)
        return {'prediction':result,'observation':{'id':observation['observation_id'],'cameras':list(observation['images']),
                                                 'proprio_dim':len(observation['proprio']),'sim_time':observation['sim_time']}}
    elif op == 'policy_run': return engine.start_policy(args.get('max_steps',20))
    elif op == 'policy_stop': return engine.stop_execution()
    elif op == 'grasp_test': return engine.start_grasp_test(record_contacts=args.get('record_contacts', False))
    elif op == 'camera': engine.move_camera(args['action'], args.get('dx', 0), args.get('dy', 0))
    elif op == 'axes': engine.axes = bool(args['visible'])
    elif op == 'select': return engine.select(args['id'])
    elif op == 'pick': return engine.pick(args['x'], args['y'])
    elif op == 'add': engine.add_object(args['kind']); return engine.scene()
    elif op == 'update': engine.update_object(args['id'], args['changes']); return engine.scene()
    elif op == 'delete': engine.delete_object(args['id']); return engine.scene()
    elif op == 'placement': engine.set_placement(args['id'], args['position'], args['rotation']); return engine.scene()
    elif op == 'scene': return engine.scene()
    elif op == 'state': return engine.state()
    elif op == 'save': return {'path': engine.save(args['path'])}
    elif op == 'load': return engine.load(args['path'])
    elif op == 'screenshot': return {'path': engine.screenshot(args['path'])}
    elif op == 'shutdown': return {'closing': True}
    else: raise ValueError(f'Unknown command: {op}')
    return engine.state()

def main():
    commands = queue.Queue(maxsize=128)
    thread = threading.Thread(target=reader, args=(commands,), daemon=True)
    thread.start()
    engine = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from .engine import Engine
            engine = Engine()
            tasks = engine.tasks()
            from .policy import adapter_catalog,load_adapter_plugins
            load_adapter_plugins()
        emit('ready', protocol=1, tasks=tasks, suites=['libero_object', 'libero_spatial', 'libero_goal', 'libero_10'],adapters=adapter_catalog())
        last_wall = time.monotonic()
        accumulator = 0.0
        next_frame = last_wall
        camera_stream = False
        next_camera_frame = last_wall
        camera_sequence = 0
        while True:
            shutting_down = False
            for _ in range(32):
                try: cmd = commands.get_nowait()
                except queue.Empty: break
                op, args, request_id = cmd.get('op'), cmd.get('args', {}), cmd.get('id')
                try:
                    if not isinstance(args, dict): raise ValueError('args must be an object')
                    if op in ('load_task', 'load', 'add', 'update', 'delete','policy_scan','policy_configure','policy_load','policy_run','grasp_test','replay_open'):
                        emit('busy', op=op)
                    with contextlib.redirect_stdout(sys.stderr):
                        if op == 'camera_stream':
                            if not isinstance(args.get('enabled'), bool):
                                raise ValueError('相机刷新开关必须为布尔值')
                            if args['enabled']: engine._require()
                            camera_stream = args['enabled']
                            next_camera_frame = 0
                            result = {'enabled': camera_stream}
                        else:
                            result = dispatch(engine, op, args)
                    emit('reply', id=request_id, op=op, ok=True, result=result, state=engine.state())
                    shutting_down = op == 'shutdown'
                    if op in ('load_task', 'load', 'reset', 'stop', 'mode', 'play', 'pause','policy_run','grasp_test'):
                        last_wall, accumulator = time.monotonic(), 0.0
                except Exception as error:
                    traceback.print_exc(file=sys.stderr)
                    emit('reply', id=request_id, op=op, ok=False, error=str(error), state=engine.state())
                    last_wall, accumulator = time.monotonic(), 0.0
                if shutting_down: break
            if shutting_down: break
            now = time.monotonic()
            elapsed, last_wall = min(now-last_wall, .15), now
            if engine.running:
                accumulator += elapsed
                while accumulator >= engine.control_period:
                    period=engine.control_period
                    try:
                        engine.step()
                        if not engine.running:
                            accumulator=0.; break
                    except Exception as error:
                        emit('fault', error=str(error))
                        break
                    accumulator -= period
            else:
                accumulator = 0.0
            if engine.renderer and now >= next_frame and not os.getenv('ROBOLAB_NO_FRAMES'):
                try:
                    from PIL import Image
                    buf = io.BytesIO()
                    Image.fromarray(engine.render()).save(buf, 'JPEG', quality=85)
                    emit('frame', jpeg=base64.b64encode(buf.getvalue()).decode('ascii'), state=engine.state(),
                         rendered_contacts=engine.rendered_contacts)
                    next_frame = time.monotonic()+1/15
                except Exception as error:
                    engine.running = False
                    emit('fault', error=f'Render failed: {error}')
                    next_frame = time.monotonic()+2
            if camera_stream and engine.renderer and now >= next_camera_frame:
                try:
                    images = engine.camera_images()
                    camera_sequence += 1
                    emit('camera_frame', sequence=camera_sequence, sim_time=float(engine.data.time),
                         task=engine.task, images=images)
                    next_camera_frame = time.monotonic()+.2
                except Exception as error:
                    camera_stream = False
                    traceback.print_exc(file=sys.stderr)
                    emit('camera_fault', error=f'双相机输出失败：{error}')
            time.sleep(.003)
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit('fault', error=str(error))
        return 1
    finally:
        if engine:
            engine.close()
        emit('stopped')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
