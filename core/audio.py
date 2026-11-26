import math
import array
import sounddevice as sd

def beep(enabled, success=False):
    if not enabled:
        return
    rate = 16000
    freq = 1100 if success else 750
    samples = array.array('h',(int(3500*math.sin(2*math.pi*freq*i/rate)) for i in range(1120)))
    # Separate nonblocking output stream; does not stop RawInputStream.
    try:
        import numpy as np
        sd.play(np.frombuffer(samples,dtype=np.int16),rate,blocking=False)
    except Exception as exc:
        print(f'Audio feedback unavailable: {exc}',flush=True)
