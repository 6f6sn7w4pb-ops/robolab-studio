"""真实桌面 P2 抓取、记录、暂停/单步、取消与双语验收。"""
import json
import os
from pathlib import Path
import time
import traceback

from PySide6.QtCore import QProcess,QSettings,Qt
from PySide6.QtTest import QTest

from .config import ROOT


def main():
    output=ROOT/'outputs'/'p2_gui_test';output.mkdir(parents=True,exist_ok=True)
    os.environ['ROBOLAB_P2_OUTPUT_DIR']=str(output/'episodes')
    os.environ['ROBOLAB_UI_SETTINGS']=str(output/'ui.ini');os.environ.pop('ROBOLAB_UI_LANGUAGE',None)
    settings=QSettings(str(output/'ui.ini'),QSettings.Format.IniFormat);settings.setValue('ui/language','zh');settings.sync()
    from .gui import configure_application,MainWindow
    app=configure_application();w=MainWindow();w.show()
    evidence=ROOT/'evidence';evidence.mkdir(parents=True,exist_ok=True)
    checks=[];start=time.monotonic();result=1;episode=None

    def wait(predicate,label,timeout=140):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            app.processEvents()
            if predicate():return
            if w.error_banner.isVisible():raise AssertionError(label+': '+w.error_banner.text())
            time.sleep(.01)
        raise AssertionError('超时：'+label)

    def click(widget):QTest.mouseClick(widget,Qt.MouseButton.LeftButton)

    def reply(op,action):
        previous=w.last_reply.get(op,{}).get('id');action()
        wait(lambda:w.last_reply.get(op,{}).get('id')!=previous,op)
        assert w.last_reply[op]['ok'],w.last_reply[op]

    def passed(text):checks.append(text);print('通过：'+text,flush=True)

    try:
        wait(lambda:w.state.get('loaded') and w.frame_count>=2,'真实桌面初始化')
        w.tabs.setCurrentIndex(2);scroll=w.tabs.widget(2)
        scroll.ensureWidgetVisible(w.grasp_test_button);app.processEvents()
        reply('grasp_test',lambda:click(w.grasp_test_button))
        wait(lambda:w.state['time']>.2,'真实规则动作开始')
        reply('pause',lambda:click(w.buttons['pause']))
        frozen=w.state['time'];count=w.state['execution']['sample_count']
        QTest.qWait(300);app.processEvents();assert w.state['time']==frozen
        reply('step',lambda:click(w.buttons['step']))
        assert abs(w.state['time']-frozen-.02)<1e-7 and not w.state['running']
        assert w.state['execution']['sample_count']==count+1
        pid=w.client.process.processId();click(w.language_button)
        assert w.i18n.language=='en' and w.client.process.processId()==pid
        reply('play',lambda:click(w.buttons['play']))
        passed('P2 可以暂停并单步 20ms，轨迹同步记录；切换语言不重启后端')
        wait(lambda:w.state['execution'].get('metrics',{}).get('lift_verified'),'持续提升判定',80)
        scroll.ensureWidgetVisible(w.p2_result_label);app.processEvents()
        w.grab().save(str(evidence/'studio-p2-lift-en.png'))
        wait(lambda:w.state.get('last_episode') is not None,'完成并落盘')
        episode=w.state['last_episode'];assert episode['p2_passed'] and episode['official_task_success']
        metadata=json.loads(Path(episode['metadata']).read_text())
        assert metadata['status']=='succeeded' and metadata['sample_count']>1000
        assert metadata['metrics']['max_stable_lift_s']>=.5
        assert not w.state['policy']['real_inference_verified']
        assert 'P2 record: Passed' in w.p2_result_label.text()
        scroll.ensureWidgetVisible(w.p2_copy_button);QTest.qWait(100);click(w.p2_copy_button)
        assert app.clipboard().text().endswith(Path(episode['directory']).name)
        w.grab().save(str(evidence/'studio-p2-passed-en.png'))
        click(w.language_button);assert 'P2 记录：通过' in w.p2_result_label.text()
        w.grab().save(str(evidence/'studio-p2-passed-zh.png'))
        passed('真实抓取/稳定提升/官方放置分别通过，记录可定位且界面双语结果正确')

        reply('reset',lambda:click(w.buttons['reset']))
        scroll.ensureWidgetVisible(w.grasp_test_button)
        reply('grasp_test',lambda:click(w.grasp_test_button))
        wait(lambda:w.state['time']>.1,'新的独立运行')
        active_path=w.state['execution']['record_dir'];assert active_path!=episode['directory']
        reply('policy_stop',lambda:click(w.stop_policy_button))
        cancelled=w.state['last_episode']
        assert cancelled['status']=='cancelled' and not cancelled['p2_passed']
        assert json.loads(Path(cancelled['metadata']).read_text())['status']=='cancelled'
        assert 'P2 记录：已取消' in w.p2_result_label.text()
        passed('取消形成单独 cancelled 记录，不复用上一轮成功结果')
        result=0
    except Exception:
        traceback.print_exc();w.grab().save(str(evidence/'studio-p2-qa-failed.png'))
    finally:
        w.close();deadline=time.monotonic()+10
        while w.client.process.state()!=QProcess.ProcessState.NotRunning and time.monotonic()<deadline:
            app.processEvents();time.sleep(.02)
        clean=w.client.process.state()==QProcess.ProcessState.NotRunning
        if not clean:w.client.process.kill();result=1
        report={'passed':result==0,'checks':checks,'episode':episode,'backend_stopped':clean,
                'elapsed_wall_s':round(time.monotonic()-start,3),'real_vla_tested':False}
        (evidence/'p2-gui-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':raise SystemExit(main())
