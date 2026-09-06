import json
import subprocess
import sys
import threading
from framework.source import hidden_process_options
from demo.common.environment import demo_environment


class ManualActor:
    def __init__(self, descriptor, environment, emit, worker_module):
        self.emit = emit
        self.stopping = False
        self.process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", worker_module,
                                         json.dumps(descriptor)], env={**demo_environment(), **environment},
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", **hidden_process_options())
        self.thread = threading.Thread(target=self._read, name="demo-manual", daemon=True)
        self.thread.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.emit("controller", "手动业务反馈", controller=json.loads(line))
            code = self.process.wait(timeout=3)
            error = self.process.stderr.read()
            if code and not self.stopping:
                self.emit("environment_error", "手动业务进程失败", detail=error)
        except Exception as exc:
            self.emit("environment_error", "手动业务反馈失败", detail=str(exc))
        finally:
            self.process.stdout.close()
            self.process.stderr.close()

    def stop(self):
        self.stopping = True
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        forced = False
        try:
            self.process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            forced = True
            self.process.kill()
            self.process.wait(timeout=3)
        self.thread.join(timeout=4)
        if forced or self.thread.is_alive():
            raise RuntimeError("Manual worker did not stop cooperatively; rebuild required")
