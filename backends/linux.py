from .base import InputBackend

# Physical US keyboard layout; not a general Unicode input method.
PLAIN = dict(zip('abcdefghijklmnopqrstuvwxyz0123456789','abcdefghijklmnopqrstuvwxyz0123456789'))
PLAIN.update({' ':'space','-':'minus','=':'equal','[':'leftbrace',']':'rightbrace',';':'semicolon',"'":'apostrophe',',':'comma','.':'dot','/':'slash','\\':'backslash','`':'grave'})
SHIFTED = dict(zip('!@#$%^&*()_+{}:\"<>?|~',['1','2','3','4','5','6','7','8','9','0','minus','equal','leftbrace','rightbrace','semicolon','apostrophe','comma','dot','slash','backslash','grave']))
SHIFTED.update({c.upper():c for c in 'abcdefghijklmnopqrstuvwxyz'})

class LinuxBackend(InputBackend):
    def __init__(self, title):
        if title:
            raise RuntimeError('Linux foreground guard unavailable; set foreground_title to null to opt in')
        from evdev import UInput, ecodes as e
        from core.config import KEYS
        names = {'ctrl':'LEFTCTRL','shift':'LEFTSHIFT','alt':'LEFTALT',
                 'esc':'ESC','pageup':'PAGEUP','pagedown':'PAGEDOWN'}
        self.codes = {k:getattr(e,'KEY_'+names.get(k,k.upper())) for k in KEYS | set(PLAIN.values()) | set(SHIFTED.values())}
        self.event = e.EV_KEY
        self.ui = UInput({e.EV_KEY:list(self.codes.values())},name='dillaVoice virtual keyboard')

    def press(self,key):
        self.ui.write(self.event,self.codes[key],1)
        self.ui.syn()

    def release(self,key):
        self.ui.write(self.event,self.codes[key],0)
        self.ui.syn()

    def validate_text(self,text):
        if any(c not in PLAIN and c not in SHIFTED for c in text):
            raise ValueError('Linux text output supports printable US-layout ASCII only')

    def write_text(self,text,stop):
        self.validate_text(text)
        for char in text:
            if stop.is_set():
                raise RuntimeError('Text output interrupted')
            keys = ['shift',SHIFTED[char]] if char in SHIFTED else [PLAIN[char]]
            self.hotkey(keys,10)

    def close(self):
        self.ui.close()
