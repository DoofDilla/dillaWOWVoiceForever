import math
import wave
import numpy as np
import sounddevice as sd

def beep(enabled, success=False, settings=None):
    if not enabled:
        return
    settings = settings or {}
    rate = 16000
    freq = 1100 if success else 750
    try:
        path = settings.get('success_sound' if success else 'wake_sound')
        if path:
            with wave.open(path,'rb') as wav:
                if wav.getsampwidth() != 2:
                    raise ValueError('Feedback WAV must be 16-bit PCM')
                rate = wav.getframerate()
                samples = np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').reshape(-1,wav.getnchannels()).astype(np.float32)/32768
        else:
            samples = np.array([int(3500*math.sin(2*math.pi*freq*i/rate)) for i in range(1120)],dtype=np.float32)/32768
        samples *= float(settings.get('volume',1.0))
        sd.play(samples,rate,device=settings.get('output_device'),blocking=False)
    except Exception as exc:
        print(f'Audio feedback unavailable: {exc}',flush=True)
