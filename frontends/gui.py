import copy
import json
import threading
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets as W
import sounddevice as sd
import pvporcupine
from core.config import registry, output_enabled
from core.engine import run_engine
from core.audio import beep

STYLE = """
QWidget { background:#171b23; color:#e3eaf2; font-size:14px; }
QFrame#panel { background:#222935; border:1px solid #303b48; border-radius:12px; }
QLabel#heading { font-size:27px; font-weight:700; color:#eaf2f8; }
QLabel#metric { font-size:30px; font-weight:700; color:#70dac8; }
QLabel#muted { color:#9caec0; }
QPushButton { background:#2b3543; border:1px solid #3a4959; border-radius:7px; padding:9px 15px; }
QPushButton:hover { background:#364958; }
QPushButton#primary { background:#237b70; color:white; border:1px solid #329c8e; font-weight:600; }
QPushButton:disabled { color:#667483; background:#222935; }
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit,QTableWidget { background:#111720; border:1px solid #394554; border-radius:5px; padding:6px; selection-background-color:#276f65; }
QListWidget { background:#111720; border:none; outline:none; }
QListWidget::item { padding:15px 12px; }
QListWidget::item:selected { background:#24443f; color:#8de6d6; border-left:3px solid #70dac8; }
QHeaderView::section { background:#293442; padding:8px; border:none; }
QProgressBar { background:#111720; border:none; border-radius:5px; height:12px; text-align:center; }
QProgressBar::chunk { background:#60cdb8; border-radius:5px; }
QCheckBox { spacing:9px; }
"""

class Bridge(QtCore.QObject):
    log = QtCore.Signal(str)
    event = QtCore.Signal(str,dict)
    finished = QtCore.Signal()

def label(text, kind=None):
    w=W.QLabel(text)
    if kind: w.setObjectName(kind)
    return w

def spin(lo,hi,value,decimal=False):
    w=W.QDoubleSpinBox() if decimal else W.QSpinBox()
    w.setRange(lo,hi)
    if decimal: w.setDecimals(2); w.setSingleStep(.05)
    w.setValue(value)
    return w

class KeyCapture(W.QLineEdit):
    def keyPressEvent(self,e):
        k=e.key()
        if k in (QtCore.Qt.Key.Key_Control,QtCore.Qt.Key.Key_Shift,QtCore.Qt.Key.Key_Alt,QtCore.Qt.Key.Key_Meta): return
        special={QtCore.Qt.Key.Key_Return:'enter',QtCore.Qt.Key.Key_Enter:'enter',QtCore.Qt.Key.Key_Escape:'esc',QtCore.Qt.Key.Key_Space:'space',QtCore.Qt.Key.Key_Backspace:'backspace',QtCore.Qt.Key.Key_Tab:'tab'}
        key=special.get(k,QtGui.QKeySequence(k).toString().lower())
        m=e.modifiers()
        parts=[n for flag,n in [(QtCore.Qt.KeyboardModifier.ControlModifier,'ctrl'),(QtCore.Qt.KeyboardModifier.ShiftModifier,'shift'),(QtCore.Qt.KeyboardModifier.AltModifier,'alt')] if m & flag]
        self.setText('+'.join(parts+[key]))

class CommandDialog(W.QDialog):
    def __init__(self,parent,name='',binding=None):
        super().__init__(parent)
        self.setWindowTitle('Edit voice command')
        self.resize(600,420)
        binding=binding or {'key':'m'}
        layout=W.QVBoxLayout(self); form=W.QFormLayout(); layout.addLayout(form)
        self.name=W.QLineEdit(name); self.aliases=W.QLineEdit(', '.join(binding.get('aliases',[])))
        form.addRow('Voice phrase',self.name); form.addRow('Aliases (comma separated)',self.aliases)
        self.steps=W.QTableWidget(0,3); self.steps.setHorizontalHeaderLabels(['Action','Value','Hold (ms)'])
        self.steps.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.steps)
        for step in binding.get('sequence',[binding]): self.add_step(step)
        row=W.QHBoxLayout(); layout.addLayout(row)
        for title,fn in [('Add key',lambda:self.add_step({'key':'m'})),('Add wait',lambda:self.add_step({'wait':250})),('Add text',lambda:self.add_step({'text':'/thank'})),('Remove',self.remove),('Up',lambda:self.move(-1)),('Down',lambda:self.move(1))]:
            b=W.QPushButton(title); b.clicked.connect(fn); row.addWidget(b)
        layout.addWidget(label('Record chords in key fields; type literal strings in text fields. Blank key hold uses the default.','muted'))
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_checked);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def add_step(self,step):
        r=self.steps.rowCount();self.steps.insertRow(r)
        kind='text' if 'text' in step else 'wait' if 'wait' in step else 'key'
        action=W.QTableWidgetItem(kind)
        action.setFlags(action.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.steps.setItem(r,0,action)
        field=KeyCapture(step['key']) if kind=='key' else W.QLineEdit(str(step[kind]))
        self.steps.setCellWidget(r,1,field)
        hold=W.QTableWidgetItem(str(step.get('hold','')))
        if kind!='key':hold.setFlags(hold.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.steps.setItem(r,2,hold)
        self.steps.setCurrentCell(r,0)
    def remove(self):
        if self.steps.currentRow()>=0:self.steps.removeRow(self.steps.currentRow())
    def read_step(self,r):
        kind=self.steps.item(r,0).text()
        val=self.steps.cellWidget(r,1).text()
        if kind=='wait':return {'wait':int(val)}
        if kind=='text':return {'text':val}
        step={'key':val};hold=self.steps.item(r,2).text()
        if hold.strip():step['hold']=int(hold)
        return step
    def move(self,delta):
        r=self.steps.currentRow();target=r+delta
        if r<0 or not 0<=target<self.steps.rowCount():return
        try: steps=[self.read_step(i) for i in range(self.steps.rowCount())]
        except ValueError:return
        steps[r],steps[target]=steps[target],steps[r]
        self.steps.setRowCount(0)
        for step in steps:self.add_step(step)
        self.steps.setCurrentCell(target,0)
    def accept_checked(self):
        try:
            steps=[self.read_step(i) for i in range(self.steps.rowCount())]
            binding=steps[0].copy() if len(steps)==1 and 'key' in steps[0] else {'sequence':steps}
            aliases=[x.strip() for x in self.aliases.text().split(',') if x.strip()]
            if aliases:binding['aliases']=aliases
            self.result_data=(self.name.text().strip().lower(),binding)
            registry(dict([self.result_data]))
        except Exception as exc:
            W.QMessageBox.warning(self,'Invalid command',str(exc));return
        self.accept()

class Window(W.QMainWindow):
    def __init__(self,args):
        super().__init__()
        self.args=copy.copy(args);self.path=Path(args.config)
        self.config=json.loads(self.path.read_text(encoding='utf-8'))
        self.thread=None;self.stop_event=None;self.level=(0,0);self.count=0;self.rejects=0;self.total=0.;self.closing=False;self.restart_pending=False;self.stopping=False;self.applied_config=None
        self.bridge=Bridge();self.bridge.log.connect(self.append_log);self.bridge.event.connect(self.on_event);self.bridge.finished.connect(self.finished)
        self.setWindowTitle('dillaWOWVoiceForever');self.resize(1080,740);self.setMinimumSize(760,540)
        root=W.QWidget();self.setCentralWidget(root);outer=W.QVBoxLayout(root);outer.setContentsMargins(20,18,20,18);outer.setSpacing(15)
        header=W.QHBoxLayout();outer.addLayout(header);header.addWidget(label('dillaWOWVoiceForever','heading'));header.addStretch();header.addWidget(label('OFFLINE  /  VOICE CONTROL','muted'))
        body=W.QHBoxLayout();outer.addLayout(body,1);body.setSpacing(18)
        self.nav=W.QListWidget();self.nav.setFixedWidth(175);self.stack=W.QStackedWidget();body.addWidget(self.nav);body.addWidget(self.stack,1)
        self.pages={}
        for name in ['Dashboard','Commands','Recognition','Audio','Output','Activity Log']:
            self.nav.addItem(name);page=W.QWidget();layout=W.QVBoxLayout(page);layout.setContentsMargins(8,4,8,4);layout.setSpacing(14);layout.addWidget(label(name,'heading'));self.pages[name]=layout;self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.dashboard();self.commands_page();self.recognition_page();self.audio_page();self.output_page();self.log_page()
        bottom=W.QHBoxLayout();outer.addLayout(bottom);self.status=label('Stopped','muted');bottom.addWidget(self.status);bottom.addStretch()
        self.save_btn=W.QPushButton('Save && Apply');self.save_btn.clicked.connect(self.save_and_apply);bottom.addWidget(self.save_btn)
        self.start_btn=W.QPushButton('Start engine');self.start_btn.setObjectName('primary');self.start_btn.clicked.connect(self.toggle);bottom.addWidget(self.start_btn)
        self.nav.setCurrentRow(0)
        self.timer=QtCore.QTimer(self);self.timer.timeout.connect(self.update_meter);self.timer.start(80)
        self.tray=None
        if W.QSystemTrayIcon.isSystemTrayAvailable():
            icon=self.style().standardIcon(W.QStyle.StandardPixmap.SP_MediaVolume)
            self.setWindowIcon(icon);self.tray=W.QSystemTrayIcon(icon,self);menu=W.QMenu()
            menu.addAction('Open dashboard',self.showNormal);menu.addAction('Start / stop engine',self.toggle);menu.addAction('Exit',self.close)
            self.tray.setContextMenu(menu);self.tray.activated.connect(lambda reason:self.showNormal() if reason==W.QSystemTrayIcon.ActivationReason.Trigger else None);self.tray.show()
    def panel(self,layout):
        frame=W.QFrame();frame.setObjectName('panel');layout.addWidget(frame);box=W.QVBoxLayout(frame);box.setContentsMargins(20,18,20,18);return box
    def dashboard(self):
        l=self.pages['Dashboard'];p=self.panel(l);self.wake_title=label(self.config['wake_word'].upper(),'metric');p.addWidget(self.wake_title,alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.state=label('Ready to listen','muted');p.addWidget(self.state,alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        p.addWidget(label('Microphone input','muted'));self.bar=W.QProgressBar();self.bar.setRange(0,100);self.bar.setTextVisible(False);p.addWidget(self.bar)
        self.peak=label('Peak: 0%','muted');p.addWidget(self.peak)
        row=W.QHBoxLayout();l.addLayout(row);self.metrics=[]
        for title in ['Recognized','Average latency','Rejected / timeout']:
            col=W.QVBoxLayout();row.addLayout(col);col.addWidget(label(title,'muted'));v=label('0','metric');col.addWidget(v);self.metrics.append(v)
        p=self.panel(l);p.addWidget(label('Last command','muted'));self.last=label('—','metric');p.addWidget(self.last);self.detail=label('No recognition yet','muted');p.addWidget(self.detail);l.addStretch()
        if W.QSystemTrayIcon.isSystemTrayAvailable():
            b=W.QPushButton('Minimize to tray');b.clicked.connect(self.hide);l.addWidget(b)
    def commands_page(self):
        l=self.pages['Commands'];l.addWidget(label('Voice phrases and aliases define the recognition vocabulary.','muted'))
        self.table=W.QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Voice phrase','Key / sequence','Aliases']);self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch);self.table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);l.addWidget(self.table,1)
        self.table.cellDoubleClicked.connect(lambda *_:self.edit_command())
        row=W.QHBoxLayout();l.addLayout(row)
        for title,fn in [('Add command',lambda:self.edit_command(True)),('Edit selected',self.edit_command),('Delete selected',self.delete_command)]:
            b=W.QPushButton(title);b.clicked.connect(fn);row.addWidget(b)
        self.refresh_commands()
    def refresh_commands(self):
        self.table.setRowCount(0)
        for name,b in self.config['commands'].items():
            r=self.table.rowCount();self.table.insertRow(r)
            for c,v in enumerate([name,b.get('key',str(len(b.get('sequence',[])))+' steps'),', '.join(b.get('aliases',[]))]):self.table.setItem(r,c,W.QTableWidgetItem(v))
    def edit_command(self,new=False):
        r=self.table.currentRow()
        if not new and r<0:return
        old=None if new else self.table.item(r,0).text();d=CommandDialog(self,old or '',self.config['commands'].get(old))
        if d.exec()!=W.QDialog.DialogCode.Accepted:return
        name,b=d.result_data;commands=dict(self.config['commands'])
        if old:commands.pop(old)
        if name in commands:W.QMessageBox.warning(self,'Duplicate','Command already exists');return
        commands[name]=b
        try:registry(commands)
        except Exception as exc:W.QMessageBox.warning(self,'Invalid command',str(exc));return
        self.config['commands']=commands;self.refresh_commands()
    def delete_command(self):
        r=self.table.currentRow()
        if r>=0 and len(self.config['commands'])>1:
            self.config['commands'].pop(self.table.item(r,0).text());self.refresh_commands()
    def settings_scroll(self, page):
        scroll=W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        content=W.QWidget()
        layout=W.QVBoxLayout(content)
        layout.setContentsMargins(0,0,10,0)
        layout.setSpacing(12)
        scroll.setWidget(content)
        self.pages[page].addWidget(scroll,1)
        return layout

    def setting(self, form, title, control, explanation):
        field=W.QWidget()
        layout=W.QVBoxLayout(field)
        layout.setContentsMargins(0,0,0,8)
        layout.setSpacing(5)
        layout.addWidget(control)
        hint=label(explanation,'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        control.setToolTip(explanation)
        form.addRow(title,field)

    def recognition_page(self):
        l=self.settings_scroll('Recognition')
        r=self.config['recognition']
        l.addWidget(label('Tune response speed and recognition reliability. Use Save & Apply to test changes without closing the app.','muted'))
        p=self.panel(l)
        p.addWidget(label('Wake word detection'))
        f=W.QFormLayout();f.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow);p.addLayout(f)
        self.word=W.QComboBox();self.word.addItems(sorted(pvporcupine.KEYWORDS));self.word.setCurrentText(self.config['wake_word'])
        self.setting(f,'Wake word',self.word,'The word that activates command recognition. All choices are built-in Porcupine models.')
        self.sensitivity=spin(0,1,self.config['sensitivity'],True)
        self.setting(f,'Sensitivity',self.sensitivity,'Default: 0.60. Increase if the wake word is missed; decrease if background speech triggers it. Higher is not always better.')
        p=self.panel(l);p.addWidget(label('Recognition strategy'))
        f=W.QFormLayout();f.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow);p.addLayout(f)
        self.mode=W.QComboBox()
        for title,value in [('Normal — final results','normal'),('Low latency — shorter endpoint','lowlatency'),('Fast — stable partial results','fast')]:
            self.mode.addItem(title,value)
        self.mode.setCurrentIndex(max(0,self.mode.findData(r['mode'])))
        self.mode_help=label('','muted');self.mode_help.setWordWrap(True)
        f.addRow('Mode',self.mode);f.addRow('',self.mode_help)
        self.timeout=spin(.1,60,r['timeout'],True)
        self.setting(f,'Command timeout',self.timeout,'Default: 3 s. Maximum listening window after the wake word. Increase for longer commands; reducing it can cut off speech.')
        self.timeout.setSuffix(' s')
        self.silence=spin(1,10000,r['silence_ms']);self.silence.setSuffix(' ms')
        self.setting(f,'Endpoint silence',self.silence,'Default: 300 ms. Requested silence after speech before Vosk finalizes a result. Smaller values can finish earlier but may cut off pauses. Only effective in optimized modes when the installed Vosk exposes SetEndpointerDelays.')
        self.stable=spin(0,10000,r['stable_ms']);self.stable.setSuffix(' ms')
        self.setting(f,'Partial stability',self.stable,'Default: 200 ms. In Fast mode, an exact, unambiguous partial command must remain unchanged this long. Try 100–200 ms; lower values can cause premature matches.')
        self.preroll=spin(0,1000,r.get('pre_roll_ms',256));self.preroll.setSuffix(' ms')
        self.setting(f,'Audio pre-roll',self.preroll,'Default: 256 ms. Replays recent audio when the wake word fires, allowing “Jarvis open map” in one phrase. Try 384 ms if the first word is clipped; too much may include the wake word itself.')
        self.mode.currentIndexChanged.connect(self.update_mode_help)
        self.update_mode_help()
        p=self.panel(l);p.addWidget(label('Tuning shortcuts'))
        row=W.QHBoxLayout();p.addLayout(row)
        for title,mode,stable in [('Normal baseline','normal',200),('Fast balanced','fast',200),('Fast responsive','fast',100)]:
            b=W.QPushButton(title);b.clicked.connect(lambda _,m=mode,t=stable:self.timing_preset(m,t));row.addWidget(b)
        hint=label('Presets set mode, timeout (3 s), endpoint silence (300 ms), stability and pre-roll (256 ms). They do not change wake word, sensitivity, devices or output. Save & Apply to activate. Test in dry run first.','muted');hint.setWordWrap(True);p.addWidget(hint)
        l.addStretch()

    def update_mode_help(self,*_):
        mode=self.mode.currentData()
        descriptions={
            'normal':'Waits for a final Vosk result. Uses the default audio-stream latency and endpoint behavior.',
            'lowlatency':'Requests low audio-stream latency and shorter endpoint delays. Still waits for final results.',
            'fast':'Uses low audio-stream latency and, where supported, shorter endpoint delays. Also accepts stable exact partial matches unless they are a prefix of a longer configured command. Faster, but potentially less reliable.'
        }
        self.mode_help.setText(descriptions[mode])
        self.silence.setEnabled(mode!='normal')
        self.stable.setEnabled(mode=='fast')

    def timing_preset(self,mode,stable):
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.timeout.setValue(3)
        self.silence.setValue(300)
        self.stable.setValue(stable)
        self.preroll.setValue(256)

    def device_box(self,kind,current):
        c=W.QComboBox();c.addItem('System default',None)
        for i,d in enumerate(sd.query_devices()):
            if d['max_'+kind+'_channels']>0:c.addItem(f"{i}: {d['name']}",i)
        idx=c.findData(current)
        if idx<0 and current is not None:c.addItem(f'Unavailable device {current}',current);idx=c.count()-1
        c.setCurrentIndex(max(0,idx));return c
    def audio_page(self):
        l=self.settings_scroll('Audio');a=self.config.get('audio',{})
        p=self.panel(l);p.addWidget(label('Audio devices'))
        f=W.QFormLayout();f.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow);p.addLayout(f)
        self.mic=self.device_box('input',self.config['device'])
        self.speaker=self.device_box('output',a.get('output_device'))
        self.setting(f,'Microphone',self.mic,'Select the input device used by both speech engines. System default follows the audio system default at engine startup.')
        self.setting(f,'Feedback device',self.speaker,'Select where wake and success tones play. Headphones help prevent feedback sounds entering the microphone.')
        p=self.panel(l);p.addWidget(label('Feedback sounds'))
        f=W.QFormLayout();f.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow);p.addLayout(f)
        self.sound=W.QCheckBox('Enable audio feedback');self.sound.setChecked(self.config.get('beep',True))
        self.setting(f,'Feedback',self.sound,'Master switch for recognition feedback. Does not affect microphone capture or keyboard output.')
        self.wake_sound_on=W.QCheckBox('Play wake-word tone');self.wake_sound_on.setChecked(self.config.get('wake_beep',False))
        self.setting(f,'Activation tone',self.wake_sound_on,'Off by default. Keep disabled for continuous phrases to avoid the tone overlapping the command.')
        self.volume=spin(0,1,a.get('volume',1.),True)
        self.setting(f,'Feedback volume',self.volume,'0.00 = silent, 1.00 = original sound amplitude. Does not change microphone sensitivity.')
        self.sound_fields={}
        for key,title in [('wake_sound','Wake WAV'),('success_sound','Success WAV')]:
            field=W.QLineEdit(a.get(key) or '');self.sound_fields[key]=field
            holder=W.QWidget();row=W.QHBoxLayout(holder);row.setContentsMargins(0,0,0,0)
            row.addWidget(field);b=W.QPushButton('Browse');b.clicked.connect(lambda _,field=field:self.pick_sound(field));row.addWidget(b)
            self.setting(f,title,holder,'Optional 16-bit PCM WAV file. Leave empty to use the built-in tone.')
        row=W.QHBoxLayout();p.addLayout(row)
        for title,success in [('Test wake tone',False),('Test success tone',True)]:
            b=W.QPushButton(title);b.clicked.connect(lambda _,s=success:beep(True,s,self.audio_settings()));row.addWidget(b)
        p=self.panel(l);p.addWidget(label('Microphone monitoring'))
        self.audio_meter=W.QProgressBar();self.audio_meter.setRange(0,100);self.audio_meter.setTextVisible(False);p.addWidget(self.audio_meter)
        self.audio_peak=label('Peak: 0%','muted');p.addWidget(self.audio_peak)
        hint=label('The meter shows the active microphone while the engine is running. Save & Apply after selecting a different device. Recognition mode controls the input stream latency; tune this on the Recognition page.','muted');hint.setWordWrap(True);p.addWidget(hint)
        l.addStretch()
    def pick_sound(self,field):
        name,_=W.QFileDialog.getOpenFileName(self,'Select feedback sound','','WAV (*.wav)')
        if name:field.setText(name)
    def audio_settings(self):
        return {**self.config.get('audio',{}),'output_device':self.speaker.currentData(),'volume':self.volume.value(),**{k:v.text().strip() or None for k,v in self.sound_fields.items()}}
    def output_page(self):
        l=self.pages['Output'];f=W.QFormLayout();l.addLayout(f);o=self.config['output']
        self.enabled=W.QCheckBox('Enable native keyboard output');self.enabled.setChecked(output_enabled(self.config, self.args.enable_output));f.addRow(self.enabled)
        self.foreground=W.QLineEdit(o.get('foreground_title') or '');f.addRow('Windows foreground title',self.foreground)
        self.hold=spin(0,10000,o.get('default_hold_ms',50));f.addRow('Default hold (ms)',self.hold)
        warning=label('Dry run is recommended for testing. Linux requires an empty foreground title and does not provide focus protection. Use one game keybind per spoken command.','muted');warning.setWordWrap(True);l.addWidget(warning);l.addStretch()
    def log_page(self):
        l=self.pages['Activity Log'];self.log=W.QPlainTextEdit();self.log.setReadOnly(True);self.log.document().setMaximumBlockCount(3000);l.addWidget(self.log,1)
        row=W.QHBoxLayout();l.addLayout(row);b=W.QPushButton('Clear');b.clicked.connect(self.log.clear);row.addWidget(b);b=W.QPushButton('Export log');b.clicked.connect(self.export_log);row.addWidget(b)
    def export_log(self):
        name,_=W.QFileDialog.getSaveFileName(self,'Export log','voice-log.txt','Text (*.txt)')
        if name:
            try:Path(name).write_text(self.log.toPlainText(),encoding='utf-8')
            except OSError as exc:W.QMessageBox.warning(self,'Export failed',str(exc))
    def append_log(self,line):self.log.appendPlainText(line)
    def save(self):
        try:
            c=copy.deepcopy(self.config);c.update(wake_word=self.word.currentText(),sensitivity=self.sensitivity.value(),device=self.mic.currentData(),beep=self.sound.isChecked(),wake_beep=self.wake_sound_on.isChecked(),audio=self.audio_settings())
            c['recognition'].update(mode=self.mode.currentData(),timeout=self.timeout.value(),silence_ms=self.silence.value(),stable_ms=self.stable.value(),pre_roll_ms=self.preroll.value())
            c['output'].update(enabled=self.enabled.isChecked(),foreground_title=self.foreground.text().strip() or None,default_hold_ms=self.hold.value())
            registry(c['commands'],c['output']['default_hold_ms'])
            tmp=self.path.with_suffix(self.path.suffix+'.tmp');tmp.write_text(json.dumps(c,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');tmp.replace(self.path)
            self.config=c;self.wake_title.setText(c['wake_word'].upper());self.status.setText('Configuration saved');return True
        except Exception as exc:W.QMessageBox.warning(self,'Cannot save configuration',str(exc));return False
    def save_and_apply(self):
        if self.stopping or self.closing:
            return
        if not self.save():
            return
        if self.thread and self.config != self.applied_config:
            self.request_stop(restart=True)
        elif self.thread:
            self.status.setText('Configuration unchanged — listening')

    def request_stop(self, restart=False):
        if self.stopping:
            return
        self.restart_pending=restart
        self.stopping=True
        self.start_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.stack.setEnabled(False)
        self.status.setText('Applying configuration…' if restart else 'Stopping…')
        self.stop_event.set()

    def toggle(self):
        if self.closing or self.stopping:
            return
        if self.thread:
            self.request_stop();return
        if self.save():self.start_engine()

    def start_engine(self, preserve_stats=False):
        self.applied_config=copy.deepcopy(self.config)
        args=copy.copy(self.args)
        for k in ['device','sensitivity','mode','timeout','silence_ms','stable_ms','pre_roll_ms']:setattr(args,k,None)
        args.wake_beep=False;args.no_beep=False;args.enable_output=self.enabled.isChecked()
        self.stop_event=threading.Event();self.level=(0,0)
        if not preserve_stats:
            self.count=self.rejects=0;self.total=0.
            self.metrics[0].setText('0');self.metrics[1].setText('—');self.metrics[2].setText('0')
        self.start_btn.setText('Stop engine');self.status.setText('Starting…')
        def work():
            try:run_engine(args,stop=self.stop_event,emit=self.bridge.log.emit,event=self.bridge.event.emit,meter=self.set_level)
            except Exception as exc:self.bridge.log.emit(f'ERROR: {exc}');self.bridge.event.emit('error',{'message':str(exc)})
            finally:self.bridge.finished.emit()
        self.thread=threading.Thread(target=work,daemon=False);self.thread.start()
    def set_level(self,rms,peak):self.level=(rms,peak)
    def update_meter(self):
        rms,peak=self.level;self.bar.setValue(int(min(100,rms*300)));self.peak.setText(f'Peak: {peak:.0%}')
        self.audio_meter.setValue(int(min(100,rms*300)));self.audio_peak.setText(f'Peak: {peak:.0%}')
    def on_event(self,kind,data):
        if self.stopping and kind not in ('match','rejected','timeout','error'):
            return
        if kind=='loading':self.state.setText('Loading speech model…')
        elif kind=='idle':self.state.setText('Waiting for wake word');self.status.setText('Listening')
        elif kind=='wake':self.state.setText('Listening for command…')
        elif kind=='partial':self.state.setText(data['phrase'])
        elif kind=='match':
            self.count+=1;self.total+=data['latency'];self.last.setText(data['phrase']);self.detail.setText(f"{data['source']} · {data['latency']*1000:.0f} ms")
        elif kind in ('rejected','timeout'):self.rejects+=1
        elif kind=='error':self.state.setText(data['message'])
        self.metrics[0].setText(str(self.count));self.metrics[1].setText(f'{self.total/self.count:.2f}s' if self.count else '—');self.metrics[2].setText(str(self.rejects))
    def finished(self):
        if self.thread:self.thread.join()
        restart=self.restart_pending and not self.closing
        self.thread=None;self.level=(0,0);self.stopping=False;self.restart_pending=False
        self.start_btn.setEnabled(True);self.save_btn.setEnabled(True);self.stack.setEnabled(True)
        if self.closing:
            self.close();return
        if restart:
            self.start_engine(preserve_stats=True);return
        self.start_btn.setText('Start engine');self.status.setText('Stopped');self.state.setText('Ready to listen')
    def closeEvent(self,e):
        if self.thread:
            self.closing=True;self.restart_pending=False;self.request_stop();e.ignore();self.status.setText('Stopping…');return
        if self.tray:self.tray.hide()
        e.accept()

def launch(args):
    app=W.QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    try:window=Window(args)
    except Exception as exc:W.QMessageBox.critical(None,'Startup failed',str(exc));return 1
    window.show();return app.exec()
