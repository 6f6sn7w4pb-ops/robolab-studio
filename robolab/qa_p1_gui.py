"""实际 WSLg 桌面按钮 → worker 双 RGB / 独立 EGL P1 子进程的集成验收。"""
import json
import os
from pathlib import Path
import time
import traceback

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtGui import QMovie
from PySide6.QtTest import QTest

from .gui import MainWindow, configure_application
from .config import ROOT


def main():
    app = configure_application()
    window = MainWindow(); window.show()
    panel = window.p1_panel
    panel.output_root = ROOT/'outputs'/'p1_gui_test'
    evidence = ROOT/'evidence'; evidence.mkdir(exist_ok=True)
    checks = []; attempts = []; started = time.monotonic(); result = 1
    heartbeat = []
    timer = QTimer(); timer.setInterval(30); timer.timeout.connect(lambda: heartbeat.append(time.monotonic())); timer.start()

    def wait(predicate, description, timeout=150):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate(): return
            if window.error_banner.isVisible(): raise AssertionError(description+': '+window.error_banner.text())
            time.sleep(.012)
        raise AssertionError('超时：'+description)

    def click(widget): QTest.mouseClick(widget, Qt.MouseButton.LeftButton)

    def tab(index):
        bar = window.view_tabs.tabBar()
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(index).center())

    def reply(op, action):
        previous = window.last_reply.get(op, {}).get('id')
        action()
        wait(lambda: window.last_reply.get(op, {}).get('id') != previous, op)
        assert window.last_reply[op]['ok'], window.last_reply[op]
        return window.last_reply[op]['result']

    def passed(text):
        checks.append(text); print('通过：'+text, flush=True)

    try:
        wait(lambda: window.state.get('loaded') and window.frame_count >= 2, '真实桌面场景加载')
        assert panel.task_combo.count() == 10 and panel.run_button.isEnabled()
        tab(1)
        wait(lambda: window.camera_frame_count >= 3, '实时双 RGB')
        packet = window.live_cameras.last_packet
        assert set(packet['images']) == {'agentview', 'robot0_eye_in_hand'}
        assert all(item['width'] == item['height'] == 224 for item in packet['images'].values())
        assert packet['images']['agentview']['data'] != packet['images']['robot0_eye_in_hand']['data']
        assert window.state['time'] == 0 and packet['sim_time'] == 0
        assert window.state['execution']['kind'] is None
        assert not window.state['policy']['real_inference_verified']
        window.grab().save(str(evidence/'studio-dual-rgb-paused.png'))
        passed('双路来自当前真实场景；暂停时刷新不推进物理时间、不启动模型')

        reply('play', lambda: click(window.buttons['play']))
        wait(lambda: window.live_cameras.last_packet['sim_time'] > .15, '实时相机跟随物理时间')
        reply('pause', lambda: click(window.buttons['pause']))
        frozen = window.state['time']
        wait(lambda: window.live_cameras.last_packet['sim_time'] == frozen, '同步暂停时刻')
        old_count = window.camera_frame_count
        wait(lambda: window.camera_frame_count > old_count, '暂停后继续输出相机')
        assert window.state['time'] == frozen
        old_id = window.last_reply.get('camera_stream', {}).get('id')
        click(window.camera_refresh_button)
        wait(lambda: window.last_reply.get('camera_stream', {}).get('id') != old_id, '暂停相机刷新')
        count = window.camera_frame_count; frames = window.frame_count
        QTest.qWait(450); app.processEvents()
        assert window.camera_frame_count == count and window.frame_count > frames
        click(window.camera_refresh_button)
        wait(lambda: window.camera_frame_count > count, '恢复相机刷新')
        reply('reset', lambda: click(window.buttons['reset']))
        passed('物理运行/暂停/重置保持可用；相机可独立暂停和恢复')

        target = next(body for body in window.scene_data['bodies'] if body['id']=='alphabet_soup_1_main')
        new_position = list(target['position']); new_position[0]+=.04; new_position[2]+=.05
        before_images = dict(window.live_cameras.last_packet['images'])
        reply('placement', lambda: window.command('placement', id=target['id'], position=new_position, rotation=target['rotation']))
        wait(lambda: all(window.live_cameras.last_packet['images'][name]['data'] != before_images[name]['data'] for name in before_images), '两路图像显示编辑后的物体')
        assert window.state['time'] == 0
        passed('编辑物体后双路图像均更新；不是默认任务的预存图像')

        tab(2)
        click(panel.run_button)
        wait(lambda: panel.run_dir is not None, '本次真实 P1 进程创建结果目录')
        assert not panel.run_button.isEnabled() and panel.cancel_button.isEnabled()
        assert panel.metadata is None
        assert not panel.process.processEnvironment().contains('DISPLAY')
        assert not panel.process.processEnvironment().contains('WAYLAND_DISPLAY')
        count = len(heartbeat); frames = window.frame_count
        wait(lambda: len(heartbeat) > count+8 and window.frame_count > frames+2, '自检期间 Qt 和原 worker 响应')
        assert panel.is_running
        window.grab().save(str(evidence/'studio-p1-running.png'))
        wait(lambda: not panel.is_running, 'GUI 启动的完整 EGL 自检结束', 200)
        assert panel.result['status'] == 'passed', panel.result
        assert panel.result['exit_code'] == 0
        report = panel.metadata
        assert report['claims']['dual_rgb_verified'] and report['claims']['step_verified']
        assert not report['claims']['grasp_tested'] and not report['claims']['vla_inference_tested']
        assert report['requested_steps'] == 20 and report['gif']['encoded_frames'] == 21
        assert all(not view.pixmap.isNull() and view.pixmap.size().width() == 256 for view in panel.cameras.views.values())
        assert window.state['time'] == 0
        assert not window.state['policy']['real_inference_verified']
        scene = reply('scene', lambda: window.command('scene'))
        actual = next(body for body in scene['bodies'] if body['id']==target['id'])['position']
        assert all(abs(x-y)<1e-9 for x,y in zip(actual,new_position))
        attempts.append(dict(panel.result))
        QTest.qWait(100); app.processEvents()
        window.grab().save(str(evidence/'studio-p1-passed.png'))
        click(panel.copy_button)
        assert app.clipboard().text().endswith(panel.run_dir.name)
        click(panel.gif_button)
        wait(lambda: panel.movie.currentFrameNumber() >= 2, 'GUI GIF 真实解码播放')
        window.grab().save(str(evidence/'studio-p1-gif.png'))
        click(panel.gif_button)
        assert panel.movie.state() == QMovie.MovieState.Paused
        passed('点击运行 → 独立 EGL → 20 步 → PNG/GIF 哈希校验与界面显示；Qt/原场景持续响应')

        valid_python, valid_libero = panel.backend_python, panel.libero_root
        previous_dir = panel.run_dir
        panel.libero_root = Path('/nonexistent-robolab-gui-test')
        click(panel.run_button)
        assert panel.metadata is None and all(view.pixmap.isNull() for view in panel.cameras.views.values())
        assert not panel.gif_button.isEnabled()
        wait(lambda: not panel.is_running, '错误路径独立进程失败', 30)
        assert panel.result['status'] == 'failed' and panel.result['exit_code'] == 1
        assert panel.run_dir != previous_dir and 'LIBERO' in panel.status.text()
        attempts.append(dict(panel.result))
        window.grab().save(str(evidence/'studio-p1-failed.png'))
        passed('实际错误路径失败并展示原因；不残留上一轮成功图像/GIF')

        panel.libero_root = valid_libero
        panel.backend_python = Path('/nonexistent-robolab-backend-python')
        click(panel.run_button)
        wait(lambda: not panel.is_running, 'Python 启动失败', 10)
        assert panel.result['status'] == 'failed' and panel.run_dir is None
        assert panel.run_button.isEnabled()
        panel.backend_python = valid_python
        passed('QProcess 启动失败可恢复，不显示旧结果')

        click(panel.run_button)
        wait(lambda: panel.run_dir is not None, '可取消的真实 P1 进程')
        cancelled_pid = panel.process.processId()
        click(panel.cancel_button)
        wait(lambda: not panel.is_running, '取消自检释放进程', 12)
        assert panel.result['status'] == 'cancelled' and not panel.gif_button.isEnabled()
        attempts.append(dict(panel.result))
        assert window.client.process.state() == QProcess.ProcessState.Running
        passed('取消只停止本次 P1 子进程，原仿真后端保持连接')

        click(panel.run_button)
        wait(lambda: panel.run_dir is not None, '关闭窗口时的真实 P1 进程')
        child_pid = panel.process.processId(); backend_pid = window.client.process.processId()
        window.close()
        wait(lambda: not panel.is_running and window.client.process.state() == QProcess.ProcessState.NotRunning, '窗口关闭清理两个进程', 15)
        for pid in (cancelled_pid, child_pid, backend_pid):
            try: os.kill(pid, 0)
            except ProcessLookupError: pass
            else: raise AssertionError('进程仍存在：'+str(pid))
        passed('运行中关闭窗口会清理本窗口所属的 P1 和仿真进程，无残留')
        result = 0
    except Exception:
        traceback.print_exc()
        window.grab().save(str(evidence/'studio-p1-qa-failure.png'))
    finally:
        timer.stop()
        window.close()
        deadline = time.monotonic()+10
        while time.monotonic() < deadline and (panel.is_running or window.client.process.state() != QProcess.ProcessState.NotRunning):
            app.processEvents(); time.sleep(.02)
        clean = not panel.is_running and window.client.process.state() == QProcess.ProcessState.NotRunning
        if not clean:
            panel.process.kill(); window.client.process.kill(); result = 1
        summary = {'passed': result == 0, 'checks': checks, 'attempts': attempts, 'children_stopped': clean,
                   'elapsed_seconds': round(time.monotonic()-started, 2), 'qt_heartbeat_ticks': len(heartbeat),
                   'real_vla_tested': False, 'grasp_tested': False, 'models_downloaded': False}
        (evidence/'p1-gui-result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__': raise SystemExit(main())
