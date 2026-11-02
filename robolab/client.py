"""Qt process transport. No simulation dependency is imported in the GUI."""
import json
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal, QTimer
from .config import BACKEND_PYTHON, LIBERO_ROOT, ROOT, backend_environment

class BackendClient(QObject):
    message = Signal(dict)
    diagnostic = Signal(str)
    disconnected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.errorOccurred.connect(self._error)
        self.process.finished.connect(self._finished)
        self.buffer, self.stderr_buffer = bytearray(), bytearray()
        self.sequence = 0
        self.closing = False
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self.process.kill)

    def start(self):
        if not BACKEND_PYTHON.is_file() or not LIBERO_ROOT.is_dir():
            self.disconnected.emit('Backend Python or LIBERO path missing; check ROBOLAB_BACKEND_PYTHON / ROBOLAB_LIBERO_ROOT')
            return
        self.closing = False
        self.buffer.clear(); self.stderr_buffer.clear()
        env = QProcessEnvironment()
        for key, value in backend_environment().items(): env.insert(key, value)
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.start(str(BACKEND_PYTHON), ['-m', 'robolab.worker'])

    def send(self, op, **args):
        if self.process.state() != QProcess.ProcessState.Running:
            self.diagnostic.emit('Backend is not running')
            return None
        self.sequence += 1
        self.process.write((json.dumps({'id': self.sequence, 'op': op, 'args': args}, allow_nan=False)+'\n').encode())
        return self.sequence

    def read_output(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.buffer) > 16_000_000:
            self.diagnostic.emit('Protocol buffer exceeded 16 MB; terminating backend')
            self.process.kill()
            return
        while b'\n' in self.buffer:
            line, _, rest = self.buffer.partition(b'\n')
            self.buffer = bytearray(rest)
            try:
                value = json.loads(line)
                if isinstance(value, dict): self.message.emit(value)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.diagnostic.emit('Invalid backend output: '+line[:180].decode(errors='replace'))

    def read_error(self):
        self.stderr_buffer.extend(bytes(self.process.readAllStandardError()))
        while b'\n' in self.stderr_buffer:
            line, _, rest = self.stderr_buffer.partition(b'\n')
            self.stderr_buffer = bytearray(rest)
            self.diagnostic.emit(line.decode(errors='replace'))
        if len(self.stderr_buffer) > 10000:
            self.diagnostic.emit(self.stderr_buffer.decode(errors='replace'))
            self.stderr_buffer.clear()

    def close(self):
        self.closing = True
        if self.process.state() == QProcess.ProcessState.Running:
            self.send('shutdown')
            self.kill_timer.start(4000)

    def _finished(self, code, status):
        self.kill_timer.stop()
        self.disconnected.emit(f'Backend exited: {code} ({status.name})')

    def _error(self, error):
        self.diagnostic.emit(self.process.errorString())
        if error == QProcess.ProcessError.FailedToStart:
            self.disconnected.emit('Backend failed to start: '+self.process.errorString())
