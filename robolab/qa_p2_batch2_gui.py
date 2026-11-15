"""Real WSLg UI acceptance for jogging, inventory, v1/v2 viewing and languages."""
import copy
import json
import os
from pathlib import Path
import shutil
import time
import traceback

from PySide6.QtCore import QProcess, Qt
from PySide6.QtTest import QTest

from .config import ROOT


def main():
    out=ROOT/'outputs'/'p2_batch2'; out.mkdir(parents=True,exist_ok=True)
    os.environ['ROBOLAB_P2_OUTPUT_DIR']=str(out/'gui_episodes')
    os.environ['ROBOLAB_UI_SETTINGS']=str(out/'gui-ui.ini'); os.environ['ROBOLAB_UI_LANGUAGE']='zh'
    from .gui import configure_application, MainWindow
    app=configure_application(); w=MainWindow(); w.show()
    checks=[]; started=time.monotonic(); result=1

    def wait(predicate, name, timeout=150, expected_error=False):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            app.processEvents()
            if predicate(): return
            if w.error_banner.isVisible() and not expected_error: raise AssertionError(name+': '+w.error_banner.text())
            time.sleep(.01)
        raise AssertionError('Timeout: '+name)

    def click(widget): QTest.mouseClick(widget, Qt.MouseButton.LeftButton)

    def reply(op, callback, ok=True):
        old=w.last_reply.get(op,{}).get('id'); callback()
        wait(lambda:w.last_reply.get(op,{}).get('id')!=old,op,expected_error=not ok)
        message=w.last_reply[op]
        assert message['ok'] is ok,message
        w.error_banner.hide()
        return message.get('result')

    def passed(name): checks.append(name); print('PASS '+name,flush=True)

    try:
        wait(lambda:w.state.get('loaded') and w.frame_count>=2,'loaded desktop')
        w.tabs.setCurrentIndex(1); scroll=w.tabs.widget(1)
        scroll.ensureWidgetVisible(w.jog_buttons[0]); app.processEvents()
        for axis in range(3):
            for direction in (-1,1):
                position=w.state['robot']['eef_position'][:]
                scroll.ensureWidgetVisible(w.jog_buttons[2*axis+(direction>0)]); app.processEvents()
                reply('jog_xyz',lambda:click(w.jog_buttons[2*axis+(direction>0)]))
                for i in range(3):
                    assert abs(w.state['robot']['eef_position'][i]-position[i]-(direction*.005 if i==axis else 0))<.0003
        pid=w.client.process.processId(); click(w.language_button)
        assert w.i18n.language=='en' and 'Jog target' in w.jog_feedback.text()
        assert w.client.process.processId()==pid
        scroll.ensureWidgetVisible(w.jog_feedback); app.processEvents()
        w.grab().save(str(out/'gui-jog-en.png')); click(w.language_button)
        passed('Six XYZ jog buttons move the real end effector; language toggle preserves backend')
        reply('mode',lambda:w.mode.setCurrentIndex(1))
        reply('jog_xyz',lambda:click(w.jog_buttons[5]))
        assert w.state['jog']['status']=='pending'
        assert all(not b.isEnabled() for b in w.jog_buttons)
        reply('play',lambda:click(w.buttons['play']))
        wait(lambda:w.state.get('jog',{}).get('status')=='reached','physical jog completion',10)
        reply('pause',lambda:click(w.buttons['pause']))
        reply('reset',lambda:click(w.buttons['reset']))
        w.tabs.setCurrentIndex(2); w.tabs.widget(2).ensureWidgetVisible(w.grasp_test_button)
        reply('grasp_test',lambda:click(w.grasp_test_button))
        reply('pause',lambda:click(w.buttons['pause']))
        assert all(not b.isEnabled() for b in w.jog_buttons)
        reply('jog_xyz',lambda:w.client.send('jog_xyz',delta=[.005,0,0],frame='world'),ok=False)
        reply('policy_stop',lambda:click(w.stop_policy_button))
        cancelled=w.state['last_episode']; assert cancelled['status']=='cancelled'
        passed('Physical PD jog completes; paused scripted execution blocks manual jog in UI and backend')

        w.tabs.widget(2).ensureWidgetVisible(w.configure_button)
        reply('policy_configure',lambda:click(w.configure_button))
        w.policy_steps.setValue(8)
        qpos=w.state['robot']['qpos'][:]
        w.tabs.widget(2).ensureWidgetVisible(w.run_policy_button)
        reply('policy_run',lambda:click(w.run_policy_button))
        wait(lambda:w.state['execution'].get('status')=='completed','Mock official OSC actions',30)
        assert w.state['execution']['steps']==8 and w.state['robot']['qpos']!=qpos
        assert not w.state['policy']['real_inference_verified']
        frames=w.camera_frame_count; w.view_tabs.setCurrentIndex(1)
        wait(lambda:w.camera_frame_count>frames,'two real RGB previews',20)
        passed('Regression: 8 Mock actions pass through official OSC and move real physics; both live RGB previews work; no real-model flag')

        # Real records are copied intact. Only the two explicitly named fixture
        # folders below are synthetic incomplete/corrupt reader test cases.
        matrix=json.loads((out/'matrix.json').read_text())['runs']
        old=[p.parent for p in (ROOT/'outputs'/'p2_verification'/'episodes').glob('*/metadata.json')
             if json.loads(p.read_text()).get('schema')=='robolab.p2_episode.v1']
        v1=next(p for p in old if json.loads((p/'metadata.json').read_text())['status']=='succeeded')
        failed=next(p for p in old if json.loads((p/'metadata.json').read_text())['status']=='failed')
        inventory=out/('gui_inventory_'+str(time.time_ns())); inventory.mkdir()
        for name,source in [('v2_success',matrix[0]['directory']),('v1_success',v1),('v1_failed',failed),('v2_cancelled',cancelled['directory'])]:
            shutil.copytree(source,inventory/name)
        (inventory/'fixture_incomplete').mkdir(); (inventory/'fixture_incomplete'/'metadata.json').write_text(json.dumps(dict(schema='robolab.p2_episode.v2',status='recording')))
        (inventory/'fixture_corrupt').mkdir(); (inventory/'fixture_corrupt'/'metadata.json').write_text('{')
        browser=w.p2_browser; browser.root.setText(str(inventory)); w.view_tabs.setCurrentIndex(3)
        reply('records_list',lambda:click(browser.refresh))
        assert {r['classification'] for r in browser.records}=={'success','failed','cancelled','incomplete','corrupt'}

        def select(name):
            item=next(browser.table.topLevelItem(i) for i in range(browser.table.topLevelItemCount())
                      if browser.table.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)['id']==name)
            browser.table.setCurrentItem(item); app.processEvents()

        for name in ('fixture_incomplete','fixture_corrupt'):
            select(name); assert not browser.open_button.isEnabled()
        select('v2_success')
        before=copy.deepcopy(w.state)
        packet=reply('replay_open',lambda:click(browser.open_button)); assert packet['supported']
        middle=packet['count']//2
        reply('replay_seek',lambda:browser.slider.setValue(middle))
        assert browser.packet['index']==middle and not browser.image.pixmap().isNull()
        assert browser.image.pixmap().width()<=browser.image.width() and browser.image.pixmap().height()<=browser.image.height()
        click(browser.play_button)
        wait(lambda:browser.last_index>middle,'state sequence playback',10)
        click(w.language_button); assert w.i18n.language=='en'
        assert 'State view (reconstructed RGB)' in browser.timeline.text()
        assert w.client.process.processId()==pid
        w.grab().save(str(out/'gui-records-en.png'))
        browser.stop_play(); click(w.language_button)
        assert '状态查看（重建 RGB）' in browser.timeline.text()
        w.grab().save(str(out/'gui-records-zh.png'))
        assert w.state['time']==before['time'] and w.state['robot']==before['robot']
        passed('Browser distinguishes five outcomes; v2 scrubbing/playback renders real states with bilingual labels and editor unchanged')
        select('v1_success'); packet=reply('replay_open',lambda:click(browser.open_button))
        assert not packet['supported'] and not browser.slider.isEnabled()
        assert 'v1 缺少' in browser.timeline.text()
        w.grab().save(str(out/'gui-v1-limit-zh.png'))
        passed('Genuine v1 record displays original verdict and explicit no-scene-replay limitation')
        reply('replay_close',lambda:click(browser.close_button))
        w.view_tabs.setCurrentIndex(0); count=w.frame_count
        wait(lambda:w.frame_count>count+1,'editor rendering after closing view',10)
        reply('step',lambda:click(w.buttons['step']))
        passed('Closing state view releases resources; editor rendering and physical stepping still work')
        result=0
    except Exception:
        traceback.print_exc(); w.grab().save(str(out/'gui-failure.png'))
    finally:
        w.close(); end=time.monotonic()+12
        while w.client.process.state()!=QProcess.ProcessState.NotRunning and time.monotonic()<end:
            app.processEvents(); time.sleep(.02)
        clean=w.client.process.state()==QProcess.ProcessState.NotRunning
        if not clean: w.client.process.kill(); result=1
        report=dict(passed=result==0,checks=checks,backend_stopped=clean,real_vla_tested=False,
                    elapsed_wall_s=round(time.monotonic()-started,3))
        (out/'gui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps(report,ensure_ascii=False),flush=True)
    return result


if __name__=='__main__': raise SystemExit(main())
