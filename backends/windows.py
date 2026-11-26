import ctypes as c
from ctypes import wintypes as w
from .base import InputBackend

VK = {'ctrl':0x11,'shift':0x10,'alt':0x12,'enter':0x0D,'tab':9,
      'esc':0x1B,'space':0x20,'backspace':8,'up':0x26,'down':0x28,
      'left':0x25,'right':0x27,'numlock':0x90,'home':0x24,'end':0x23,
      'insert':0x2D,'delete':0x2E,'pageup':0x21,'pagedown':0x22}
VK.update({f'f{i}':0x6F+i for i in range(1,13)})
VK.update({k:ord(k.upper()) for k in 'abcdefghijklmnopqrstuvwxyz0123456789'})
ULONG_PTR = c.c_size_t

class KEYBDINPUT(c.Structure):
    _fields_ = [('wVk',w.WORD),('wScan',w.WORD),('dwFlags',w.DWORD),
                ('time',w.DWORD),('dwExtraInfo',ULONG_PTR)]
class MOUSEINPUT(c.Structure):
    _fields_ = [('dx',w.LONG),('dy',w.LONG),('mouseData',w.DWORD),
                ('dwFlags',w.DWORD),('time',w.DWORD),('dwExtraInfo',ULONG_PTR)]
class HARDWAREINPUT(c.Structure):
    _fields_ = [('uMsg',w.DWORD),('wParamL',w.WORD),('wParamH',w.WORD)]
class INPUTUNION(c.Union):
    _fields_ = [('ki',KEYBDINPUT),('mi',MOUSEINPUT),('hi',HARDWAREINPUT)]
class INPUT(c.Structure):
    _fields_ = [('type',w.DWORD),('u',INPUTUNION)]

class WindowsBackend(InputBackend):
    def __init__(self, title):
        self.title = title
        self.user = c.WinDLL('user32', use_last_error=True)
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.GetWindowTextW.argtypes = [w.HWND,w.LPWSTR,c.c_int]
        self.user.SendInput.argtypes = [w.UINT,c.POINTER(INPUT),c.c_int]
        self.user.SendInput.restype = w.UINT

    def allowed(self):
        if not self.title:
            return True
        buf = c.create_unicode_buffer(1024)
        self.user.GetWindowTextW(self.user.GetForegroundWindow(),buf,len(buf))
        return self.title.casefold() in buf.value.casefold()

    def _send(self, key, up):
        flags = 2 if up else 0
        if key in {'up','down','left','right','home','end','insert','delete','pageup','pagedown','numlock'}:
            flags |= 1
        event = INPUT(1,INPUTUNION(ki=KEYBDINPUT(VK[key],0,flags,0,0)))
        if self.user.SendInput(1,c.byref(event),c.sizeof(event)) != 1:
            raise OSError(c.get_last_error(), 'SendInput failed (check focus and privileges)')

    def press(self,key):
        self._send(key,False)

    def release(self,key):
        self._send(key,True)

    def validate_text(self, text):
        text.encode('utf-16-le')

    def write_text(self, text, stop):
        for char in text:
            if stop.is_set() or not self.allowed():
                raise RuntimeError('Text output interrupted')
            raw = char.encode('utf-16-le')
            events = []
            for i in range(0,len(raw),2):
                unit = int.from_bytes(raw[i:i+2],'little')
                for flags in (4,6):
                    events.append(INPUT(1,INPUTUNION(ki=KEYBDINPUT(0,unit,flags,0,0))))
            batch = (INPUT*len(events))(*events)
            if self.user.SendInput(len(batch),batch,c.sizeof(INPUT)) != len(batch):
                raise OSError(c.get_last_error(),'Unicode SendInput failed')
