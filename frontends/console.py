import argparse
from pathlib import Path
import sounddevice as sd
from core.engine import run_engine
from core.recognition import MODEL_NAME
ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description='dillaWOWVoiceForever')
    p.add_argument('--config',type=Path,default=ROOT/'config.json')
    p.add_argument('--list-devices',action='store_true')
    p.add_argument('--device',type=int)
    p.add_argument('--sensitivity',type=float)
    p.add_argument('--timeout',type=float)
    p.add_argument('--mode',choices=['normal','lowlatency','fast'])
    p.add_argument('--silence-ms',type=int)
    p.add_argument('--stable-ms',type=int)
    p.add_argument('--pre-roll-ms',type=int)
    p.add_argument('--wake-beep',action='store_true')
    p.add_argument('--free',action='store_true')
    p.add_argument('--no-beep',action='store_true')
    p.add_argument('--enable-output',action='store_true')
    p.add_argument('--model',type=Path,default=ROOT/'models'/MODEL_NAME)
    p.add_argument('--gui',action='store_true')
    args = p.parse_args()
    if args.gui:
        from frontends.gui import launch
        return launch(args)
    if args.list_devices:
        print(sd.query_devices())
        return
    try:
        run_engine(args)
    except ValueError as exc:
        p.error(str(exc))
