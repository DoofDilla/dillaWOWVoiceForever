# dillaWOWVoiceForever

Local voice commands using Porcupine 1.9.5 (Jarvis) and Vosk, without a cloud account.
Windows is the initial target; Linux/Steam Deck input support is experimental.

## Quick start

Use a 64-bit Python environment (Python 3.11 recommended).

```shell
python -m pip install -r requirements.txt
python main.py
```

The first start downloads the small English Vosk model. Say "Jarvis open map" as one phrase, or pause after "Jarvis". A 256 ms audio pre-roll helps preserve the command onset. The activation beep is off by default; the success beep is unchanged. Use `--pre-roll-ms 0` for the old handoff, `--pre-roll-ms 384` for a longer buffer, or `--wake-beep` to restore the activation tone. The optional config keys are `recognition.pre_roll_ms` and top-level `wake_beep`. Continuous speech performance needs microphone testing.
The default is dry run: no keys are sent. Edit config.json and restart.
Use `python main.py --list-devices`, `--device 3`, `--mode normal|lowlatency|fast`, `--free`, or `--no-beep`.
Use `--enable-output` explicitly to send keys. On Windows the foreground window title must contain the configured string. On Linux no reliable Gamescope foreground check is implemented; to enable output you must explicitly set foreground_title to null and use --enable-output. Only use this while the intended application is focused.

## Config

Command names and aliases form the Vosk grammar. A binding can use a single key (e.g. ctrl+1) or a sequence of key and wait steps, with optional hold in milliseconds.
Keys are US-layout physical keys on Linux; Windows uses virtual keys. Actual game bindings are examples, not detected from WoW.
Use a single keybind for game commands. External timed multi-action macros may violate game rules; no game-policy approval is implied.

## Linux / Steam Deck

Install PortAudio and the Python dependencies in a virtual environment. The evdev backend requires write access to /dev/uinput; arrange a narrowly scoped device permission with your administrator. Do not run the entire program as root. SteamOS Gaming Mode, Proton input delivery and old Porcupine binary compatibility require real-device testing. No system modifications are performed by this program.

## Design

One audio stream, independent recognition and input modules, a bounded output queue, and explicit opt-in for key output. No audio files are retained. No GUI, push-to-talk or hot reload yet.

```shell
python -m unittest discover -s tests
```
