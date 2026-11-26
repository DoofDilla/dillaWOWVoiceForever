import copy
import threading
import math
from collections import deque
import json
import queue
import struct
import time
from pathlib import Path
from datetime import datetime
import pvporcupine
import sounddevice as sd
from vosk import SetLogLevel
from core.config import load, output_enabled
from core.audio import beep
from core.recognition import get_model, make_recognizer, unambiguous, MODEL_NAME
from core.dispatcher import Dispatcher, make_backend






def run_engine(args, stop=None, emit=None, event=None, meter=None):
    args = copy.copy(args)
    stop = stop or threading.Event()
    def log(message):
        line = f'[{datetime.now():%H:%M:%S.%f}] {message}'
        if emit is None: print(line, flush=True)
        else: emit(line)
    def notify(kind, **data):
        if event: event(kind, data)
    config, commands = load(args.config)
    args.enable_output = output_enabled(config, args.enable_output)
    for key in ('device','sensitivity'):
        if getattr(args,key) is None:
            setattr(args,key,config[key])
    for key in ('mode','timeout','silence_ms','stable_ms'):
        if getattr(args,key) is None:
            setattr(args,key,config['recognition'][key])
    if not 0 <= args.sensitivity <= 1 or args.timeout <= 0 or args.silence_ms <= 0 or args.stable_ms < 0 or args.mode not in ('normal','lowlatency','fast'):
        raise ValueError('Invalid recognition settings')
    if args.pre_roll_ms is None:
        args.pre_roll_ms = config['recognition'].get('pre_roll_ms',256)
    if not 0 <= args.pre_roll_ms <= 1000:
        raise ValueError('Pre-roll must be between 0 and 1000 ms')
    sounds = config.get('beep',True) and not args.no_beep
    wake_word = config['wake_word']
    feedback = config.get('audio', {})
    SetLogLevel(-1)
    notify('loading')
    model = get_model(args.model)
    if stop.is_set(): return
    engine = pvporcupine.create(keywords=[wake_word],sensitivities=[args.sensitivity])
    pre_roll = deque(maxlen=math.ceil(args.pre_roll_ms * engine.sample_rate / (1000 * engine.frame_length)))
    dispatcher = None
    audio = queue.Queue(maxsize=64)
    dropped = wakes = matches = rejected = timeouts = 0
    recognizer = None
    activated = partial_since = 0.
    partial = ''

    def callback(indata,frames,time_info,status):
        nonlocal dropped
        if meter:
            pcm = struct.unpack(f'<{frames}h', indata)
            meter(min(1., math.sqrt(sum(x*x for x in pcm)/max(1,frames))/32768), min(1.,max(map(abs,pcm),default=0)/32768))
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
        if stop.is_set():
            return
        if text in commands:
            matches += 1
            log(f'MATCH: {text!r} | confidence: {score} | wake-to-result: {elapsed:.2f}s | source: {source}')
            dispatcher.submit(text)
            notify('match',phrase=text,latency=elapsed,source=source,confidence=confidence)
            beep(sounds,True,feedback)
        elif text:
            rejected += 1
            notify('rejected')
            log(f'REJECTED: {text!r} | {elapsed:.2f}s')
        else:
            timeouts += 1
            notify('timeout')
            log('TIMEOUT: no command recognized')
        recognizer = None
        pre_roll.clear()
        partial = ''
        notify('idle')
        log(f'Waiting for {wake_word}...')

    try:
        backend = make_backend(config['output'].get('foreground_title')) if args.enable_output else None
        dispatcher = Dispatcher(commands,backend,log=log)
        ready = fresh()
        if args.mode != 'normal' and not hasattr(ready,'SetEndpointerDelays'):
            log('WARNING: Vosk has no custom endpoint API; default endpoint in use.')
        log(f'Microphone: {sd.query_devices(args.device,"input")["name"]}')
        log(f'Mode: {args.mode} | Timeout: {args.timeout}s | Output: {"ENABLED" if args.enable_output else "DRY RUN"}')
        log('Commands: '+', '.join(commands))
        log(f'Waiting for {wake_word}... Press Ctrl+C to stop.')
        with sd.RawInputStream(device=args.device,samplerate=engine.sample_rate,blocksize=engine.frame_length,channels=1,dtype='int16',callback=callback,latency=None if args.mode=='normal' else 'low'):
            notify('idle')
            while not stop.is_set():
                try:
                    frame,captured = audio.get(timeout=.1)
                except queue.Empty:
                    if recognizer is not None and time.monotonic()-activated >= args.timeout:
                        finish(recognizer.FinalResult())
                        ready = fresh()
                    continue
                if stop.is_set():
                    break
                if recognizer is None:
                    pre_roll.append(frame)
                    pcm = struct.unpack(f'<{engine.frame_length}h',frame)
                    if engine.process(pcm)>=0:
                        wakes += 1
                        notify('wake')
                        activated = captured
                        recognizer = ready
                        partial_since = captured
                        log(f'{wake_word.upper()} detected! (#{wakes}) | pre-roll: {len(pre_roll)*engine.frame_length*1000/engine.sample_rate:.0f} ms')
                        if pre_roll:
                            recognizer.AcceptWaveform(b''.join(pre_roll))
                        pre_roll.clear()
                        if args.wake_beep or config.get('wake_beep',False):
                            beep(sounds,False,feedback)
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
                            notify('partial',phrase=current)
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
        notify('summary',wakes=wakes,matches=matches,rejected=rejected,timeouts=timeouts,dropped=dropped)
        log(f'Wake words: {wakes} | Matches: {matches} | Rejected: {rejected} | Timeouts: {timeouts} | Dropped frames: {dropped}')

