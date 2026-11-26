import queue
import sys
import threading
import time

def make_backend(title):
    if sys.platform == 'win32':
        from backends.windows import WindowsBackend
        return WindowsBackend(title)
    if sys.platform == 'linux':
        from backends.linux import LinuxBackend
        return LinuxBackend(title)
    raise RuntimeError('Unsupported input platform')

class Dispatcher:
    def __init__(self, registry, backend=None, log=None):
        self.log = log or (lambda message: print(message,flush=True))
        self.registry = registry
        self.backend = backend
        self.queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.run,daemon=True)
        self.worker.start()

    def submit(self, phrase):
        if self.backend is None:
            self.log(f'DRY RUN: {phrase}: {self.registry[phrase]}')
            return
        try:
            self.queue.put_nowait(phrase)
        except queue.Full:
            self.log('Output busy; command discarded')

    def run(self):
        while not self.stop.is_set():
            try:
                phrase = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                for step in self.registry[phrase]:
                    if self.stop.is_set() or not self.backend.allowed():
                        break
                    if step[0] == 'wait':
                        self.stop.wait(step[1]/1000)
                    else:
                        self.backend.hotkey(step[1],step[2])
            except Exception as exc:
                self.log(f'Input error: {exc}')

    def close(self):
        self.stop.set()
        self.worker.join()
        if self.backend:
            self.backend.close()
