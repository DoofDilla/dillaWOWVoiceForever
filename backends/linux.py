from .base import InputBackend

class LinuxBackend(InputBackend):
    def __init__(self, title):
        if title:
            raise RuntimeError('Linux foreground guard unavailable; set foreground_title to null to opt in')
        from evdev import UInput, ecodes as e
        from core.config import KEYS
        names = {'ctrl':'LEFTCTRL','shift':'LEFTSHIFT','alt':'LEFTALT',
                 'esc':'ESC','pageup':'PAGEUP','pagedown':'PAGEDOWN'}
        self.codes = {k:getattr(e,'KEY_'+names.get(k,k.upper())) for k in KEYS}
        self.event = e.EV_KEY
        self.ui = UInput({e.EV_KEY:list(self.codes.values())},name='dillaVoice virtual keyboard')

    def press(self,key):
        self.ui.write(self.event,self.codes[key],1)
        self.ui.syn()

    def release(self,key):
        self.ui.write(self.event,self.codes[key],0)
        self.ui.syn()

    def close(self):
        self.ui.close()
