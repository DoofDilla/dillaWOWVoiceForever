import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from vosk import Model, KaldiRecognizer

MODEL_NAME = 'vosk-model-small-en-us-0.15'

def get_model(path):
    if not path.exists():
        print('Downloading Vosk English model (~40 MB)...',flush=True)
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            tmp = Path(tmp)
            archive = tmp/'model.zip'
            urllib.request.urlretrieve(f'https://alphacephei.com/vosk/models/{MODEL_NAME}.zip',archive)
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
            shutil.move(str(tmp/MODEL_NAME),str(path))
    return Model(str(path))

def make_recognizer(model,rate,commands,args):
    r = KaldiRecognizer(model,rate) if args.free else KaldiRecognizer(model,rate,json.dumps(list(commands)+['[unk]']))
    r.SetWords(True)
    if args.mode != 'normal':
        setter = getattr(r,'SetEndpointerDelays',None)
        if setter:
            setter(args.timeout,args.silence_ms/1000,max(5.,args.timeout+1))
    return r

def unambiguous(phrase,commands):
    return phrase in commands and not any(c.startswith(phrase+' ') for c in commands)
