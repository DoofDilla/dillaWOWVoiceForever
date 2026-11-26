import json
import string
from pathlib import Path

KEYS = set(string.ascii_lowercase + string.digits) | {
    'ctrl', 'shift', 'alt', 'enter', 'tab', 'esc', 'space', 'backspace',
    'up', 'down', 'left', 'right', 'numlock', 'home', 'end', 'insert',
    'delete', 'pageup', 'pagedown'
} | {f'f{i}' for i in range(1, 13)}

def chord(value):
    keys = value.lower().split('+')
    if not keys or any(k not in KEYS for k in keys) or len(set(keys)) != len(keys):
        raise ValueError(f'Invalid key chord: {value!r}')
    return keys

def ms(value, maximum=10000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'Invalid duration: {value!r}')
    return value

def registry(commands, default_hold=50):
    result = {}
    ms(default_hold)
    for name, binding in commands.items():
        sequence = binding.get('sequence', [binding])
        if not isinstance(sequence, list) or not sequence or len(sequence) > 32:
            raise ValueError(f'Invalid sequence: {name}')
        steps = []
        for step in sequence:
            if 'key' in step and 'wait' not in step:
                steps.append(('key', chord(step['key']), ms(step.get('hold', default_hold))))
            elif 'wait' in step and 'key' not in step:
                steps.append(('wait', ms(step['wait'])))
            else:
                raise ValueError(f'Invalid step: {step}')
        if sum(s[-1] for s in steps) > 10000:
            raise ValueError(f'Sequence exceeds 10 seconds: {name}')
        for alias in [name] + binding.get('aliases', []):
            phrase = ' '.join(alias.lower().split())
            if not phrase or phrase == '[unk]' or phrase in result:
                raise ValueError(f'Invalid or duplicate phrase: {alias}')
            result[phrase] = steps
    if not result:
        raise ValueError('No commands configured')
    return result

def load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return data, registry(data['commands'], data['output'].get('default_hold_ms', 50))
