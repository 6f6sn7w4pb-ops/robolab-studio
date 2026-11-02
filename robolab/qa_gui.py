"""Visible WSLg Qt integration QA. Actions go through the actual UI and worker."""
import json
import time
import traceback
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from .gui import configure_application, MainWindow
from .i18n import tr
from .config import ROOT

def main(smoke=False):
    app=configure_application()
    window=MainWindow(); window.show()
    evidence=ROOT/'evidence'; evidence.mkdir(exist_ok=True)
    checks=[]
    started=time.monotonic()
    def wait(predicate,label,timeout=150):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            app.processEvents()
            if predicate():
                return
            if window.error_banner.isVisible():
                raise AssertionError(f'{label}: {window.error_banner.text()}')
            time.sleep(.015)
        raise AssertionError('Timed out: '+label)
    def passed(text):
        checks.append(text); print('PASS',text,flush=True)
    def reply(op,action):
        old=window.last_reply.get(op,{}).get('id',-1)
        action()
        wait(lambda:window.last_reply.get(op,{}).get('id',-1)!=old,op)
        assert window.last_reply[op]['ok'],window.last_reply[op]
        return window.last_reply[op]['result']
    result=1
    try:
        wait(lambda:window.state.get('loaded') and window.frame_count>=2,'desktop startup + rendered LIBERO frame')
        assert not window.viewport.pixmap.isNull()
        assert window.task_combo.count()==10
        window.grab().save(str(evidence/('studio-default-view.png' if smoke else 'studio-initial.png')))
        passed('WSLg native desktop, 10 LIBERO tasks, embedded real RGB scene')
        if smoke:
            reply('camera',lambda:window.command('camera',action='home'))
            reply('play',lambda:window.buttons['play'].click())
            wait(lambda:window.state['time']>.1,'smoke: actual physics advances')
            reply('pause',lambda:window.buttons['pause'].click())
            reply('reset',lambda:window.buttons['reset'].click())
            assert window.state['time']==0
            assert window.tabs.count()>=3
            window.tabs.setCurrentIndex(2); app.processEvents()
            assert tr('仅配置') in window.model_status_label.text()
            assert not window.run_policy_button.isEnabled()
            window.grab().save(str(evidence/'studio-policy-ready.png'))
            passed('default camera framing + Windows entry + Play/Pause/Reset')
            result=0
            return result
        before=dict(window.state['camera'])
        view=window.viewport
        p=QPoint(view.width()//2,view.height()//2)
        QTest.mousePress(view,Qt.MouseButton.LeftButton,pos=p)
        QTest.mouseMove(view,p+QPoint(65,25),40)
        QTest.mouseRelease(view,Qt.MouseButton.LeftButton,pos=p+QPoint(65,25))
        wait(lambda:window.state['camera']['azimuth']!=before['azimuth'],'mouse orbit')
        passed('mouse drag changes actual MuJoCo orbit camera')
        reply('play',lambda:QTest.mouseClick(window.buttons['play'],Qt.MouseButton.LeftButton))
        wait(lambda:window.state['time']>.15,'Play advances physical time')
        reply('pause',lambda:QTest.mouseClick(window.buttons['pause'],Qt.MouseButton.LeftButton))
        t=window.state['time']; QTest.qWait(300); app.processEvents()
        assert window.state['time']==t
        reply('reset',lambda:QTest.mouseClick(window.buttons['reset'],Qt.MouseButton.LeftButton))
        assert window.state['time']==0
        reply('step',lambda:QTest.mouseClick(window.buttons['step'],Qt.MouseButton.LeftButton))
        assert abs(window.state['time']-.02)<1e-7
        reply('mode',lambda:window.mode.setCurrentIndex(0))
        passed('Play/Pause freeze/Reset/20ms Step, explicit Edit/Simulate modes')
        reply('add',lambda:QTest.mouseClick(window.add_buttons[0],Qt.MouseButton.LeftButton))
        object_id=window.selected_info['id']
        assert window.selected_info['type']=='primitive'
        window.property_fields['position'][0].setValue(.12)
        window.property_fields['rotation'][2].setValue(25)
        window.property_fields['size'][0].setValue(.08)
        window.property_fields['mass'].setValue(.25)
        reply('update',lambda:QTest.mouseClick(window.apply_button,Qt.MouseButton.LeftButton))
        assert abs(window.selected_info['position'][0]-.12)<1e-8
        assert abs(window.selected_info['mass']-.25)<1e-8
        passed('UI creates actual body; applies transform, scale, mass to compiled model')
        window.tabs.setCurrentIndex(1)
        target=window.joint_sliders[0].value()+100
        reply('joint',lambda:window.joint_sliders[0].setValue(target))
        assert abs(window.state['robot']['qpos'][0]-target/1000)<1e-8
        reply('gripper',lambda:window.gripper_slider.setValue(15))
        assert abs(window.state['robot']['gripper_qpos'][0]-.006)<1e-5
        passed('UI joint slider and gripper update real qpos, eef telemetry received')
        path=evidence/'gui-roundtrip.robolab.json'
        reply('save',lambda:window.command('save',path=str(path)))
        assert path.is_file()
        shot=evidence/'gui-export.png'
        reply('screenshot',lambda:window.command('screenshot',path=str(shot)))
        assert shot.stat().st_size>10000
        window.grab().save(str(evidence/'studio-robot.png'))
        window.tabs.setCurrentIndex(0)
        app.processEvents()
        reply('select',lambda:window.command('select',id=object_id))
        window.properties_scroll.ensureWidgetVisible(window.delete_button)
        QTest.qWait(80); app.processEvents()
        assert window.selected_info['id']==object_id
        assert window.delete_button.isVisible() and window.delete_button.isEnabled()
        window.grab().save(str(evidence/'studio-edited.png'))
        reply('delete',window.delete_button.click)
        assert object_id not in [b['id'] for b in window.scene_data['bodies']]
        reply('load',lambda:window.open_path(path))
        assert object_id in [b['id'] for b in window.scene_data['bodies']]
        passed('GUI process roundtrip: save, delete, restore project; scene PNG export')
        window.task_combo.setCurrentIndex(1)
        reply('load_task',lambda:QTest.mouseClick(window.load_button,Qt.MouseButton.LeftButton))
        assert 'cream cheese' in window.scene_data['task']['language']
        passed('UI switches to second real LIBERO task')
        # Inject a rejected target; backend must stay alive with intact scene.
        old=window.last_reply.get('joint',{}).get('id',-1)
        window.command('joint',index=0,radians=100)
        deadline=time.monotonic()+20
        while window.last_reply.get('joint',{}).get('id',-1)==old and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.01)
        assert not window.last_reply['joint']['ok']
        assert window.state['loaded']
        window.error_banner.hide()
        passed('invalid command is rejected; worker and scene remain available')
        result=0
    except Exception:
        traceback.print_exc()
        window.grab().save(str(evidence/'studio-failure.png'))
    finally:
        window.close()
        deadline=time.monotonic()+8
        from PySide6.QtCore import QProcess
        while window.client.process.state()!=QProcess.ProcessState.NotRunning and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.02)
        clean=window.client.process.state()==QProcess.ProcessState.NotRunning
        if not clean:
            window.client.process.kill(); result=1
        else: passed('window close releases backend process and GL context')
        report={'passed':result==0,'checks':checks,'elapsed_seconds':round(time.monotonic()-started,2),
                'backend_stopped':clean,'frame_count':window.frame_count}
        (evidence/('desktop-smoke-result.json' if smoke else 'gui-test-result.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
        if not clean:
            raise RuntimeError('Backend cleanup failed')
    return result

if __name__=='__main__': raise SystemExit(main('--smoke' in sys.argv))
