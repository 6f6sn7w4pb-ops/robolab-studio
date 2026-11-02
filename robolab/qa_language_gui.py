"""真实桌面语言切换：保持场景/编辑值/进程、双 RGB、运行中 P1 与 GIF。"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from PySide6.QtCore import QProcess, QSettings, Qt
from PySide6.QtGui import QAction, QMovie
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QAbstractButton, QComboBox, QTabWidget, QLineEdit

from .config import ROOT


def main():
    out = ROOT/'outputs'/'language_gui_test'; out.mkdir(parents=True,exist_ok=True)
    os.environ['ROBOLAB_UI_SETTINGS'] = str(out/'preferences.ini')
    os.environ.pop('ROBOLAB_UI_LANGUAGE',None)
    initial = QSettings(str(out/'preferences.ini'),QSettings.Format.IniFormat)
    initial.setValue('ui/language','zh'); initial.sync()
    from .gui import MainWindow,configure_application
    app = configure_application(); window = MainWindow(); window.show()
    panel = window.p1_panel; panel.output_root = out/'runs'
    evidence = ROOT/'evidence'; evidence.mkdir(exist_ok=True)
    checks = []; started = time.monotonic(); result = 1; smoke_run = None

    def wait(predicate, message, timeout=160):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            app.processEvents()
            if predicate(): return
            if window.error_banner.isVisible(): raise AssertionError(message+': '+window.error_banner.text())
            time.sleep(.012)
        raise AssertionError('超时：'+message)

    def click(widget): QTest.mouseClick(widget,Qt.MouseButton.LeftButton)

    def reply(op,action):
        previous = window.last_reply.get(op,{}).get('id')
        action(); wait(lambda: window.last_reply.get(op,{}).get('id')!=previous,op)
        assert window.last_reply[op]['ok'],window.last_reply[op]

    def language(code):
        if window.i18n.language!=code: click(window.language_button)
        assert window.i18n.language==code
        app.processEvents()

    def english_coverage():
        leaks = []
        for obj in window.findChildren(QLabel)+window.findChildren(QAbstractButton)+window.findChildren(QAction):
            if obj is window.language_button or obj.property('i18n_literal_text'): continue
            if re.search('[\u3400-\u9fff]',obj.text()): leaks.append(obj.text())
        for combo in window.findChildren(QComboBox):
            for i in range(combo.count()):
                if re.search('[\u3400-\u9fff]',combo.itemText(i)): leaks.append(combo.itemText(i))
        for tabs in window.findChildren(QTabWidget):
            for i in range(tabs.count()):
                if re.search('[\u3400-\u9fff]',tabs.tabText(i)): leaks.append(tabs.tabText(i))
        for edit in window.findChildren(QLineEdit):
            if re.search('[\u3400-\u9fff]',edit.placeholderText()): leaks.append(edit.placeholderText())
        assert not leaks,leaks

    def passed(message): checks.append(message); print('通过：'+message,flush=True)

    try:
        wait(lambda: window.state.get('loaded') and window.frame_count>=2,'真实场景加载')
        backend_pid = window.client.process.processId()
        task = dict(window.scene_data['task']); instruction = window.task_description.text()
        reply('select',lambda: window.command('select',id='alphabet_soup_1_main'))
        field = window.property_fields['position'][0]; field.setValue(field.value()+.1234)
        dirty = field.value()
        window.checkpoint_input.setText('/tmp/中文模型目录')
        sequence = window.client.sequence
        for code in ('en','zh','en','zh','en'):
            language(code)
            assert window.client.sequence==sequence
            assert window.client.process.processId()==backend_pid
            assert window.property_fields['position'][0] is field and field.value()==dirty
            assert window.scene_data['task']==task and window.task_description.text()==instruction
            assert window.checkpoint_input.text()=='/tmp/中文模型目录'
            assert window.state['time']==0
        assert window.buttons['play'].text()=='▶ Start physics'
        assert window.view_tabs.tabText(2)=='P1 offscreen test'
        assert window.mode.currentData()=='edit'
        english_coverage()
        window.grab().save(str(evidence/'studio-language-en.png'))
        passed('一键往返切换菜单/页签/状态/属性，保留任务、输入值和控件；不发送额外 RPC')

        env=dict(os.environ,QT_QPA_PLATFORM='offscreen')
        code="""import json,sys
from robolab.gui import MainWindow,configure_application
app=configure_application();w=MainWindow(auto_load=False)
assert w.i18n.language=='en' and w.buttons['play'].text()=='▶ Start physics'
assert 'mujoco' not in sys.modules and 'torch' not in sys.modules
print(json.dumps({'language':w.i18n.language,'button':w.buttons['play'].text()}));w.close()
"""
        restarted=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
        assert restarted.returncode==0,restarted.stderr
        passed('独立新 GUI 进程读取保存的英文偏好；GUI 不导入模型/仿真依赖')

        reply('play',lambda:click(window.buttons['play']))
        wait(lambda:window.state['time']>.1,'物理运行')
        current_time=window.state['time']; language('zh'); language('en')
        wait(lambda:window.state['time']>current_time,'切换后物理继续运行')
        assert window.client.process.processId()==backend_pid
        reply('pause',lambda:click(window.buttons['pause']))
        window.view_tabs.setCurrentIndex(1)
        wait(lambda:window.camera_frame_count>=3,'英文实时双相机')
        previous=window.camera_frame_count; frozen=window.state['time']
        language('zh'); language('en')
        wait(lambda:window.camera_frame_count>previous,'切换后实时相机继续刷新')
        assert window.state['time']==frozen and window.live_cameras.last_packet['task']==task
        english_coverage(); window.grab().save(str(evidence/'studio-language-cameras-en.png'))
        passed('切换语言不重启或重置物理仿真，双相机流保持正常')

        window.view_tabs.setCurrentIndex(2)
        click(panel.run_button)
        wait(lambda:panel.run_dir is not None,'GUI 启动真实 P1')
        smoke_pid=panel.process.processId(); smoke_run=str(panel.run_dir)
        wait(lambda:'Creating' in panel.status.text() or 'Resetting' in panel.status.text(),'英文自检阶段提示',60)
        language('zh');assert panel.process.processId()==smoke_pid and panel.is_running
        window.grab().save(str(evidence/'studio-language-p1-running-zh.png'))
        language('en');assert panel.process.processId()==smoke_pid and panel.is_running
        assert '正在' not in panel.status.text()
        wait(lambda:not panel.is_running,'英文 P1 真实渲染与导出',220)
        assert panel.result['status']=='passed',panel.result
        assert panel.status.text().startswith('P1 passed')
        assert panel.metadata['requested_steps']==20 and panel.metadata['gif']['encoded_frames']==21
        assert not panel.metadata['claims']['vla_inference_tested']
        english_coverage();QTest.qWait(100)
        window.grab().save(str(evidence/'studio-language-p1-passed-en.png'))
        language('zh');assert panel.status.text().startswith('P1 通过')
        window.grab().save(str(evidence/'studio-language-p1-passed-zh.png'))
        language('en');click(panel.gif_button)
        wait(lambda:panel.movie.currentFrameNumber()>=2,'英文 GIF 播放')
        movie=panel.movie; language('zh');language('en')
        assert panel.movie is movie and movie.state()==QMovie.MovieState.Running
        assert panel.gif_button.text()=='Pause GIF'
        click(panel.gif_button);language('zh')
        assert movie.state()==QMovie.MovieState.Paused and panel.gif_button.text()=='继续播放 GIF'
        passed('P1 初始化/运行中切换不更换进程；真实 20 步、21 帧 GIF，成功提示与播放状态即时双语切换')

        panel.libero_root=Path('/nonexistent-language-test-libero')
        language('en');click(panel.run_button)
        wait(lambda:not panel.is_running,'实际失败状态本地化',30)
        assert panel.result['status']=='failed' and panel.status.text().startswith('P1 failed')
        assert 'Invalid LIBERO source path' in panel.status.text()
        english_coverage();language('zh');assert panel.status.text().startswith('P1 未通过')
        assert not panel.gif_button.isEnabled()
        passed('实际失败原因可双语切换，旧图像不残留；原始日志/metadata 保持原文')
        result=0
    except Exception:
        traceback.print_exc();window.grab().save(str(evidence/'studio-language-failure.png'))
    finally:
        window.close();deadline=time.monotonic()+12
        while time.monotonic()<deadline and (panel.is_running or window.client.process.state()!=QProcess.ProcessState.NotRunning):
            app.processEvents();time.sleep(.02)
        clean=not panel.is_running and window.client.process.state()==QProcess.ProcessState.NotRunning
        if not clean:panel.process.kill();window.client.process.kill();result=1
        report={'passed':result==0,'checks':checks,'children_stopped':clean,'smoke_run':smoke_run,
                'elapsed_seconds':round(time.monotonic()-started,2),'qt_dialog_translation_available':window.i18n.qt_chinese_available,
                'real_vla_tested':False,'user_preferences_modified':False}
        (evidence/'language-gui-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':raise SystemExit(main())
