"""Real WSLg verification of live and historical P3 views."""
import argparse
import json
import os
from pathlib import Path
import time
import traceback

from PySide6.QtCore import QPoint, QProcess, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox

from .config import ROOT


def usable_matrix(directory):
    """The passed P3-C backend matrix in `directory` whose history record still exists, else None."""
    try:
        matrix = json.loads((Path(directory)/'result.json').read_text(encoding='utf-8'))
        history = Path(matrix['view']['history_directory'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return matrix if matrix.get('passed') is True and history.is_dir() else None


def find_matrix(outputs):
    """Newest usable backend matrix under outputs/, so the GUI test does not need --matrix-dir."""
    candidates = sorted((path.parent for path in Path(outputs).glob('*/result.json')),
                        key=lambda path: (path/'result.json').stat().st_mtime, reverse=True)
    return next((path for path in candidates if usable_matrix(path)), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix-dir', type=Path, default=None,
                        help='P3-C backend matrix (--p3c-test output); default: newest passed one under outputs/')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs'/'p3c_gui')
    args = parser.parse_args()
    matrix_dir = args.matrix_dir or find_matrix(ROOT/'outputs')
    if matrix_dir is None or usable_matrix(matrix_dir) is None:
        parser.error('No passed P3-C backend matrix found; run first: '
                     'bash scripts/launch.sh --p3c-test --output-dir outputs/p3c_check')
    matrix_dir, out = Path(matrix_dir).resolve(), args.output_dir.resolve()
    matrix = usable_matrix(matrix_dir)
    print('P3-C backend matrix:', matrix_dir, flush=True)
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(ROBOLAB_UI_SETTINGS=str(out/'ui.ini'), ROBOLAB_UI_LANGUAGE='zh',
                      ROBOLAB_P2_OUTPUT_DIR=str(out/'episodes'))
    from .gui import MainWindow, configure_application

    app = configure_application()
    window = MainWindow()
    window.show()
    browser = window.p2_browser
    live = window.contact_controls
    history = browser.contact_controls
    latest_frame = {}
    window.client.message.connect(lambda packet: latest_frame.update(packet) if packet['event'] == 'frame' else None)
    started = time.monotonic()
    result = {'passed': False, 'checks': []}

    def wait(predicate, name, timeout=80):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            if window.error_banner.isVisible():
                raise AssertionError(window.error_banner.text())
            time.sleep(.01)
        raise AssertionError('Timeout: '+name)

    def click(widget):
        point = QPoint(8, widget.height()//2) if isinstance(widget, QCheckBox) else widget.rect().center()
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point)

    def reply(op, callback):
        prior = window.last_reply.get(op, {}).get('id')
        callback()
        wait(lambda: window.last_reply.get(op, {}).get('id') != prior, op)
        packet = window.last_reply[op]
        assert packet['ok'], packet
        return packet['result']

    def show_in_policy(widget):
        window.tabs.setCurrentIndex(2)
        window.tabs.widget(2).ensureWidgetVisible(widget)
        app.processEvents()

    def record_open(directory):
        directory = Path(directory)
        browser.root.setText(str(directory.parent))
        if window.view_tabs.currentIndex() != 3:
            reply('records_list', lambda: window.view_tabs.setCurrentIndex(3))
        else:
            reply('records_list', lambda: click(browser.refresh))
        for i in range(browser.table.topLevelItemCount()):
            item = browser.table.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole)['id'] == directory.name:
                browser.table.setCurrentItem(item)
                return reply('replay_open', lambda: click(browser.open_button))
        raise AssertionError('Record missing from inventory: '+str(directory))

    def observed():
        return latest_frame.get('rendered_contacts') or {}

    def passed(name):
        result['checks'].append(name)
        print('PASS '+name, flush=True)

    try:
        wait(lambda: window.state.get('loaded') and window.frame_count >= 2, 'desktop ready')
        pid = window.client.process.processId()
        reply('contact_view', lambda: click(live.show_contacts))
        assert live.show_contacts.isChecked() and not observed()
        reply('step', lambda: click(window.buttons['step']))
        wait(lambda: observed().get('sample_index') == 0, 'single native sample')
        assert 't=0.018 s' in live.info.text() and '0/0' in live.info.text()
        reply('reset', lambda: click(window.buttons['reset']))
        wait(lambda: not observed(), 'reset clears sample')
        show_in_policy(window.record_contacts)
        click(window.record_contacts)
        assert window.record_contacts.isChecked()
        show_in_policy(window.grasp_test_button)
        reply('grasp_test', lambda: click(window.grasp_test_button))
        wait(lambda: window.state['time'] > .10, 'started physical capture')
        reply('pause', lambda: click(window.buttons['pause']))
        before = window.state['time']
        count = window.state['execution']['contact_recording']['sample_count']
        QTest.qWait(250)
        assert window.state['time'] == before
        reply('step', lambda: click(window.buttons['step']))
        assert abs(window.state['time']-before-.02) < 1e-8
        assert window.state['execution']['contact_recording']['sample_count'] == count+1
        reply('play', lambda: click(window.buttons['play']))
        wait(lambda: observed().get('drawn', 0) >= 2, 'physical target contact')
        reply('pause', lambda: click(window.buttons['pause']))
        frozen = window.state['time']
        reply('contact_view', lambda: live.filter.setCurrentIndex(live.filter.findData('obstacle')))
        wait(lambda: observed().get('drawn') == 0 and observed().get('total', 0) >= 2, 'live obstacle filter')
        assert window.state['time'] == frozen
        reply('contact_view', lambda: live.filter.setCurrentIndex(live.filter.findData('target')))
        reply('contact_view', lambda: click(live.forces))
        wait(lambda: observed().get('drawn', 0) >= 2, 'live contact marker redraw')
        assert window.grab().save(str(out/'live-zh.png'))
        show_in_policy(window.stop_policy_button)
        reply('policy_stop', lambda: click(window.stop_policy_button))
        cancelled = window.state['last_episode']
        assert cancelled['status'] == 'cancelled' and cancelled['contact_recording']['status'] == 'finalized'
        assert cancelled['contact_recording']['sample_count'] > 0
        result['gui_cancelled'] = cancelled
        passed('Live pose, empty step, pause, single step, filters and actual opt-in recording/cancellation')

        original = record_open(matrix['view']['history_directory'])
        assert original['can_view_contacts'] and original['rendered_contacts'] is None
        first = reply('replay_seek', lambda: click(history.show_contacts))
        assert first['rendered_contacts'] is None and '初始 P2 行' in history.info.text()
        index = matrix['view']['history_index']
        packet = reply('replay_seek', lambda: browser.slider.setValue(index))
        assert packet['rendered_time_s'] == matrix['view']['history_solver_time_s']
        assert packet['sim_time'] != packet['rendered_time_s']
        wrist = reply('replay_seek', lambda: history.camera.setCurrentIndex(history.camera.findData('robot0_eye_in_hand')))
        assert wrist['jpeg'] != packet['jpeg'] and wrist['rendered_time_s'] == packet['rendered_time_s']
        close_up = reply('replay_seek', lambda: history.camera.setCurrentIndex(history.camera.findData('gripper')))
        assert close_up['view_camera'] == 'gripper' and close_up['rendered_time_s'] == packet['rendered_time_s']
        reply('replay_seek', lambda: click(history.forces))
        assert browser.image.source.save(str(out/'history-frame.png'))
        assert window.grab().save(str(out/'history-zh.png'))
        click(window.language_button)
        assert window.i18n.language == 'en' and window.client.process.processId() == pid
        assert 'Contact state (reconstructed)' in browser.timeline.text() and 'Native solver' in history.info.text()
        assert history.camera.currentText() == 'Translucent close-up'
        assert window.grab().save(str(out/'history-en.png'))
        plain = reply('replay_seek', lambda: click(history.show_contacts))
        assert plain['rendered_contacts'] is None and plain['rendered_time_s'] == plain['sim_time']
        assert window.state['time'] == frozen
        passed('Historical solver-time view, two cameras, close-up, bilingual labels and integrated P2 fallback')

        legacy = ROOT/'outputs'/'p3b_verification'/'result.json'
        if legacy.is_file():
            legacy_records = json.loads(legacy.read_text())['legacy']
            for schema, record in legacy_records.items():
                packet = record_open(record['directory'])
                assert packet['supported'] == (schema == 'robolab.p2_episode.v2')
                assert not packet['can_view_contacts'] and history.isHidden()
            passed('Real v1 and v2 records without a sidecar keep their original viewing behavior')

        record_open(matrix['view']['history_directory'])
        reply('load_task', lambda: window.command('load_task', suite='libero_object', index=1))
        wait(lambda: not observed(), 'switching task clears original contacts')
        assert browser.packet is None and history.isHidden() and not live.show_contacts.isChecked()
        window.view_tabs.setCurrentIndex(1)
        wait(lambda: window.camera_frame_count >= 1, 'live dual RGB')
        passed('Task switch clears views and keeps the actual dual RGB stream usable')
        result['passed'] = True
    except Exception:
        result['traceback'] = traceback.format_exc()
        traceback.print_exc()
        window.grab().save(str(out/'failure.png'))
    finally:
        window.close()
        deadline = time.monotonic()+12
        while window.client.process.state() != QProcess.ProcessState.NotRunning and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.02)
        result['backend_stopped'] = window.client.process.state() == QProcess.ProcessState.NotRunning
        if not result['backend_stopped']:
            window.client.process.kill()
            result['passed'] = False
        result['elapsed_wall_s'] = round(time.monotonic()-started, 3)
        (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
