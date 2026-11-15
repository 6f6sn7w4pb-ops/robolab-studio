"""Real WSLg check: open a P4 dataset index from the P4 panel in both languages."""
import argparse
import json
import os
from pathlib import Path
import time
import traceback

from PySide6.QtCore import QProcess, Qt
from PySide6.QtTest import QTest

from .config import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT/'outputs'/'p4d_dataset_v1')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs'/'p4d_gui')
    args = parser.parse_args()
    index_path = (args.dataset/'result.json').resolve()
    index = json.loads(index_path.read_text(encoding='utf-8'))
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(ROBOLAB_UI_SETTINGS=str(out/'ui.ini'), ROBOLAB_UI_LANGUAGE='zh')
    from .gui import MainWindow, configure_application

    app = configure_application()
    window = MainWindow()
    window.show()
    started = time.monotonic()
    result = {'passed': False, 'checks': [], 'dataset_index': str(index_path), 'real_vla_tested': False}
    runs = index['runs']
    demos = [run for run in runs if run['split'] != 'control']
    passed_demos = sum(run['files_verified'] and run.get('outcome', {}).get('passed') is True for run in demos)
    controls = [run for run in runs if run['split'] == 'control']
    expected_controls = sum(run['files_verified'] and run.get('expected_outcome_verified') is True for run in controls)
    verified = sum(run['files_verified'] for run in runs)

    def wait(predicate, name, timeout=120):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('Timeout: '+name)

    def passed(name):
        result['checks'].append(name)
        print('PASS '+name, flush=True)

    dialog = None
    try:
        wait(lambda: window.state.get('loaded') and window.frame_count >= 2, 'desktop ready')
        pid = window.client.process.processId()
        window.view_tabs.setCurrentWidget(window.p4_panel)
        dialog = window.p4_panel.open_pilot(index_path)
        assert dialog is not None, 'Dataset index could not be opened'
        wait(dialog.isVisible, 'dataset dialog visible')
        status = dialog.status.text()
        for fragment in (f'演示通过 {passed_demos}/{len(demos)}', f'阻挡对照按预期失败 {expected_controls}/{len(controls)}',
                         f'已复执行校验 {verified}/{len(runs)}'):
            assert fragment in status, (fragment, status)
        assert dialog.table.topLevelItemCount() == len(runs) and dialog.windowTitle() == 'P4 OSC 数据集'
        splits = {dialog.table.topLevelItem(i).text(1) for i in range(dialog.table.topLevelItemCount())}
        assert {'训练划分', '验证划分', '已见形状族新实例', '未见形状族', '物理对照'} <= splits, splits
        control_row = next(i for i, run in enumerate(runs) if run['split'] == 'control')
        dialog.table.setCurrentItem(dialog.table.topLevelItem(control_row))
        app.processEvents()
        assert '未通过原因' in dialog.detail.text() and '最大 qpos 差 0' in dialog.detail.text(), dialog.detail.text()
        dialog.grab().save(str(out/'dataset-summary-zh.png'))
        passed(f'Dataset index opens from the P4 panel with {len(runs)} rows, all split names and replay details')
        QTest.mouseClick(window.language_button, Qt.MouseButton.LeftButton)
        wait(lambda: window.i18n.language == 'en', 'language switch')
        status = dialog.status.text()
        assert f'Demonstrations passed {passed_demos}/{len(demos)}' in status, status
        assert dialog.windowTitle() == 'P4 OSC dataset' and 'Failure reasons' in dialog.detail.text()
        assert window.client.process.processId() == pid
        dialog.grab().save(str(out/'dataset-summary-en.png'))
        passed('English switch updates the dataset summary without restarting the backend')
        QTest.mouseClick(window.language_button, Qt.MouseButton.LeftButton)
        wait(lambda: window.i18n.language == 'zh', 'language switch back')
        dialog.close()
        result['passed'] = True
    except Exception:
        result['traceback'] = traceback.format_exc()
        traceback.print_exc()
        (dialog or window).grab().save(str(out/'failure.png'))
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
        print(json.dumps({key: result[key] for key in ('passed', 'checks', 'backend_stopped')}, ensure_ascii=False),
              flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
