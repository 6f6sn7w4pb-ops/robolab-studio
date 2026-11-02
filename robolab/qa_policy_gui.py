"""Actual WSLg UI -> worker -> mock/official OSC and scripted grasp QA."""
import json,time,traceback
from .gui import configure_application,MainWindow
from .i18n import tr
from .config import ROOT
from PySide6.QtCore import QProcess

def main():
    app=configure_application(); window=MainWindow(); window.show()
    evidence=ROOT/'evidence'; evidence.mkdir(exist_ok=True)
    checks=[]; start=time.monotonic(); result=1
    def wait(predicate,name,timeout=120):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            app.processEvents()
            if predicate(): return
            if window.error_banner.isVisible(): raise AssertionError(name+': '+window.error_banner.text())
            time.sleep(.012)
        raise AssertionError('Timeout: '+name)
    def reply(op,callback):
        old=window.last_reply.get(op,{}).get('id')
        callback(); wait(lambda:window.last_reply.get(op,{}).get('id')!=old,op)
        message=window.last_reply[op]
        if not message['ok']: raise AssertionError(message)
        return message['result']
    def passed(name): checks.append(name); print('PASS',name,flush=True)
    try:
        wait(lambda:window.state.get('loaded') and window.frame_count>=2,'load real scene')
        assert window.buttons['play'].text()==tr('▶ 启动物理仿真')
        reply('play',window.buttons['play'].click)
        wait(lambda:window.state['time']>.1,'physics time')
        assert window.state['execution']['kind'] is None
        reply('pause',window.buttons['pause'].click)
        reply('reset',window.buttons['reset'].click)
        passed('physical simulation does not claim to execute a model or task')
        window.tabs.setCurrentIndex(2); app.processEvents()
        scan=reply('policy_scan',window.scan_button.click)
        assert window.weight_status.text()==tr('扫描完成：{candidates} 个文件齐全候选，{directories} 个已检查目录。未加载权重。',candidates=scan['complete_candidates'],directories=len(scan['checkpoints']))
        reply('policy_configure',window.configure_button.click)
        assert window.state['policy']['stage']=='mock_ready'
        assert not window.state['policy']['model_loaded']
        t=window.state['time']
        preview=reply('policy_preview',window.preview_button.click)
        assert preview['prediction']['is_mock']
        assert preview['observation']['proprio_dim']==8
        assert window.state['time']==t
        window.policy_steps.setValue(8)
        reply('policy_run',window.run_policy_button.click)
        wait(lambda:window.state['execution']['status']=='completed','Mock actual action loop')
        assert window.state['execution']['steps']==8
        assert not window.state['policy']['real_inference_verified']
        window.grab().save(str(evidence/'studio-mock-policy.png'))
        passed('scan/configure/observe/predict/execute Mock through UI; no real-model flags')
        reply('reset',window.buttons['reset'].click)
        window.policy_combo.setCurrentIndex(1)
        reply('policy_configure',window.configure_button.click)
        assert window.state['policy']['stage']=='configured'
        assert not window.run_policy_button.isEnabled()
        assert not window.preview_button.isEnabled()
        window.grab().save(str(evidence/'studio-vla-not-loaded.png'))
        passed('OpenVLA-OFT stays configured; absent model cannot run or show inference passed')
        reply('grasp_test',window.grasp_test_button.click)
        wait(lambda:window.state['execution']['phase']=='抬起验证','scripted physical lift')
        window.grab().save(str(evidence/'studio-grasp-lift.png'))
        wait(lambda:window.state['execution']['status']=='succeeded','official task success',90)
        assert window.state['execution']['official_success'] is True
        assert window.state['execution']['is_vla'] is False
        assert window.state['policy']['stage']=='configured'
        window.grab().save(str(evidence/'studio-grasp-success.png'))
        passed('rule self-test runs real pick/place and LIBERO success, separately from VLA state')
        result=0
    except Exception:
        traceback.print_exc(); window.grab().save(str(evidence/'studio-policy-failure.png'))
    finally:
        window.close(); deadline=time.monotonic()+8
        while window.client.process.state()!=QProcess.ProcessState.NotRunning and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.02)
        clean=window.client.process.state()==QProcess.ProcessState.NotRunning
        if not clean: window.client.process.kill(); result=1
        report={'passed':result==0,'checks':checks,'backend_stopped':clean,'elapsed_seconds':round(time.monotonic()-start,2),
                'real_vla_tested':False,'models_downloaded':False}
        (evidence/'policy-gui-result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    return result

if __name__=='__main__': raise SystemExit(main())
