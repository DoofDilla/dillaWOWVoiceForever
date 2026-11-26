import argparse
import json
import queue
import struct
import time
from pathlib import Path
from datetime import datetime
import pvporcupine
import sounddevice as sd
from vosk import SetLogLevel
from core.config import load
from core.audio import beep
from core.recognition import get_model, make_recognizer, unambiguous, MODEL_NAME
from core.dispatcher import Dispatcher, make_backend

ROOT = Path(__file__).resolve().parent

def log(message):
    print(f'[{datetime.now():%H:%M:%S.%f}] {message}',flush=True)

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
    p.add_argument('--free',action='store_true')
    p.add_argument('--no-beep',action='store_true')
    p.add_argument('--enable-output',action='store_true')
    p.add_argument('--model',type=Path,default=ROOT/'models'/MODEL_NAME)
    args = p.parse_args()
    if args.list_devices:
        print(sd.query_devices())
        return
    config, commands = load(args.config)
    for key in ('device','sensitivity'):
        if getattr(args,key) is None:
            setattr(args,key,config[key])
    for key in ('mode','timeout','silence_ms','stable_ms'):
        if getattr(args,key) is None:
            setattr(args,key,config['recognition'][key])
    if not 0 <= args.sensitivity <= 1 or args.timeout <= 0 or args.silence_ms <= 0 or args.stable_ms < 0 or args.mode not in ('normal','lowlatency','fast'):
        p.error('Invalid recognition settings')
    sounds = config.get('beep',True) and not args.no_beep
    wake_word = config['wake_word']
    SetLogLevel(-1)
    model = get_model(args.model)
    engine = pvporcupine.create(keywords=[wake_word],sensitivities=[args.sensitivity])
    dispatcher = None
    audio = queue.Queue(maxsize=64)
    dropped = wakes = matches = rejected = timeouts = 0
    recognizer = None
    activated = partial_since = 0.
    partial = ''

    def callback(indata,frames,time_info,status):
        nonlocal dropped
        if status:
            log(f'Audio warning: {status}')
        try:
            audio.put_nowait((bytes(indata),time.monotonic()))
        except queue.Full:
            dropped += 1

    def fresh():
        return make_recognizer(model,engine.sample_rate,commands,args)

    def finish(result,source='final'):
        nonlocal matches,rejected,timeouts,recognizer,partial
        data = json.loads(result)
        text = data.get('text','').strip()
        elapsed = time.monotonic()-activated
        confidence = min((w['conf'] for w in data.get('result',[])),default=None)
        score = f'{confidence:.2f}' if confidence is not None else 'n/a'
        if text in commands:
            matches += 1
            log(f'MATCH: {text!r} | confidence: {score} | wake-to-result: {elapsed:.2f}s | source: {source}')
            dispatcher.submit(text)
            beep(sounds,True)
        elif text:
            rejected += 1
            log(f'REJECTED: {text!r} | {elapsed:.2f}s')
        else:
            timeouts += 1
            log('TIMEOUT: no command recognized')
        recognizer = None
        partial = ''
        log(f'Waiting for {wake_word}...')

    try:
        backend = make_backend(config['output'].get('foreground_title')) if args.enable_output else None
        dispatcher = Dispatcher(commands,backend)
        ready = fresh()
        if args.mode != 'normal' and not hasattr(ready,'SetEndpointerDelays'):
            print('WARNING: Vosk has no custom endpoint API; default endpoint in use.')
        print(f'Microphone: {sd.query_devices(args.device,"input")["name"]}')
        print(f'Mode: {args.mode} | Timeout: {args.timeout}s | Output: {"ENABLED" if args.enable_output else "DRY RUN"}')
        print('Commands: '+', '.join(commands))
        log(f'Waiting for {wake_word}... Press Ctrl+C to stop.')
        with sd.RawInputStream(device=args.device,samplerate=engine.sample_rate,blocksize=engine.frame_length,channels=1,dtype='int16',callback=callback,latency=None if args.mode=='normal' else 'low'):
            while True:
                try:
                    frame,captured = audio.get(timeout=.1)
                except queue.Empty:
                    if recognizer is not None and time.monotonic()-activated >= args.timeout:
                        finish(recognizer.FinalResult())
                        ready = fresh()
                    continue
                if recognizer is None:
                    pcm = struct.unpack(f'<{engine.frame_length}h',frame)
                    if engine.process(pcm)>=0:
                        wakes += 1
                        activated = captured
                        recognizer = ready
                        partial_since = captured
                        log(f'{wake_word.upper()} detected! (#{wakes}) Speak now.')
                        beep(sounds)
                    continue
                if recognizer.AcceptWaveform(frame):
                    result = recognizer.Result()
                    if json.loads(result).get('text','').strip():
                        finish(result)
                        ready = fresh()
                        continue
                else:
                    current = json.loads(recognizer.PartialResult()).get('partial','')
                    if current != partial:
                        partial_since = captured
                        if current:
                            log(f'Partial: {current}')
                    partial = current
                    if args.mode=='fast' and unambiguous(current,commands) and captured-partial_since>=args.stable_ms/1000:
                        finish(json.dumps({'text':current}),'stable partial')
                        ready = fresh()
                        continue
                if captured-activated>=args.timeout:
                    finish(recognizer.FinalResult())
                    ready = fresh()
    except KeyboardInterrupt:
        pass
    finally:
        if dispatcher:
            dispatcher.close()
        engine.delete()
        print(f'Wake words: {wakes} | Matches: {matches} | Rejected: {rejected} | Timeouts: {timeouts} | Dropped frames: {dropped}')

if __name__ == '__main__':
    main()
