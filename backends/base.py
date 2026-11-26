import time

class InputBackend:
    def allowed(self):
        return True

    def press(self, key):
        raise NotImplementedError

    def release(self, key):
        raise NotImplementedError

    def hotkey(self, keys, hold_ms):
        pressed = []
        try:
            for key in keys:
                if not self.allowed():
                    raise RuntimeError('Foreground guard blocked input')
                self.press(key)
                pressed.append(key)
            time.sleep(hold_ms / 1000)
        finally:
            for key in reversed(pressed):
                self.release(key)

    def validate_text(self, text):
        raise NotImplementedError('Text output unavailable on this backend')

    def write_text(self, text, stop):
        raise NotImplementedError('Text output unavailable on this backend')

    def close(self):
        pass
