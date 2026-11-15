"""Real WSLg check: open P5 evaluation summaries from the P4 panel in both languages."""
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
    parser.add_argument('--evals', nargs='+', type=Path,
                        default=[ROOT/'outputs'/name for name in ('p5_eval_teacher', 'p5_eval_wrong_part', 'p5_eval_mock')])
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs'/'p5_gui')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(ROBOLAB_UI_SETTINGS=str(out/'ui.ini'), ROBOLAB_UI_LANGUAGE='zh')
    from .gui import MainWindow, configure_application

    app = configure_application()
    window = MainWindow()
    window.show()
    started = time.monotonic()
    result = {'passed': False, 'checks': [], 'summaries': [str(path) for path in args.evals], 'real_vla_tested': False}

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
        for evaluation in args.evals:
            path = (evaluation/'summary.json').resolve()
            summary = json.loads(path.read_text(encoding='utf-8'))
            name = summary['policy']['policy_id']
            dialog = window.p4_panel.open_pilot(path)
            assert dialog is not None and dialog.windowTitle() == 'P5 闭环评估', 'Summary could not be opened'
            wait(dialog.isVisible, 'evaluation dialog visible')
            status = dialog.status.text()
            assert f"已完成 {summary['finished']}/{summary['planned']}" in status and name in status, status
            assert dialog.table.topLevelItemCount() == len(summary['runs'])
            conditions = summary.get('conditions') or {}
            expected = '扩展测试场景' if conditions.get('scene_set') == 'extended' else '数据集测试场景'
            assert expected in dialog.scene_line.text() and (
                ('圆柱防滚 开' in dialog.scene_line.text()) == bool(conditions.get('anti_roll'))), dialog.scene_line.text()
            overall = summary['results']['overall']['success']
            first_group = dialog.groups.topLevelItem(0)
            assert first_group.text(0) == '全部测试' and first_group.text(2).startswith(f"{overall['count']}/"), \
                first_group.text(2)
            # Select a failed episode when there is one, so the reasons and keyframes are shown.
            row = next((i for i, run in enumerate(summary['runs']) if not run['metrics']['success']), 0)
            dialog.table.setCurrentItem(dialog.table.topLevelItem(row))
            app.processEvents()
            run = summary['runs'][row]
            if run['keyframes']:
                assert not dialog.views['agentview'].pixmap.isNull(), 'keyframe not shown'
                assert f"第 {run['keyframes'][-1]['tick']} 步" in dialog.frame_label.text(), dialog.frame_label.text()
                dialog.slider.setValue(0)
                app.processEvents()
                assert '第 0 步' in dialog.frame_label.text()
                dialog.slider.setValue(dialog.slider.maximum())
            if run['metrics']['failure_reasons']:
                assert '未通过原因' in dialog.detail.text(), dialog.detail.text()
            app.processEvents()
            dialog.grab().save(str(out/f"{evaluation.name}-zh.png"))
            passed(f"{name}: summary with {len(summary['runs'])} episodes, rate groups and keyframes")
            QTest.mouseClick(window.language_button, Qt.MouseButton.LeftButton)
            wait(lambda: window.i18n.language == 'en', 'language switch')
            assert dialog.windowTitle() == 'P5 closed-loop evaluation' and 'Finished' in dialog.status.text()
            assert dialog.groups.topLevelItem(0).text(0) == 'All test episodes'
            assert 'Scene conditions' in dialog.scene_line.text(), dialog.scene_line.text()
            dialog.grab().save(str(out/f"{evaluation.name}-en.png"))
            QTest.mouseClick(window.language_button, Qt.MouseButton.LeftButton)
            wait(lambda: window.i18n.language == 'zh', 'language switch back')
            assert window.client.process.processId() == pid
            passed(f'{name}: English switch without restarting the backend')
            dialog.close()
            dialog = None
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
