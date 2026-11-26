import copy
import json
import threading
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets as W
import sounddevice as sd
import pvporcupine
from core.config import registry
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
        self.thread=None;self.stop_event=None;self.level=(0,0);self.count=0;self.rejects=0;self.total=0.;self.closing=False
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
        self.save_btn=W.QPushButton('Save configuration');self.save_btn.clicked.connect(self.save);bottom.addWidget(self.save_btn)
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
    def recognition_page(self):
        l=self.pages['Recognition'];f=W.QFormLayout();l.addLayout(f);r=self.config['recognition']
        self.word=W.QComboBox();self.word.addItems(sorted(pvporcupine.KEYWORDS));self.word.setCurrentText(self.config['wake_word']);f.addRow('Wake word',self.word)
        self.sensitivity=spin(0,1,self.config['sensitivity'],True);f.addRow('Sensitivity',self.sensitivity)
        self.mode=W.QComboBox();self.mode.addItems(['normal','lowlatency','fast']);self.mode.setCurrentText(r['mode']);f.addRow('Recognition mode',self.mode)
        self.timeout=spin(.1,60,r['timeout'],True);f.addRow('Command timeout (s)',self.timeout)
        self.silence=spin(1,10000,r['silence_ms']);f.addRow('Endpoint silence (ms)',self.silence)
        self.stable=spin(0,10000,r['stable_ms']);f.addRow('Partial stability (ms)',self.stable)
        self.preroll=spin(0,1000,r.get('pre_roll_ms',256));f.addRow('Audio pre-roll (ms)',self.preroll)
        l.addWidget(label('Fast mode accepts stable partial matches. Changes apply when the engine restarts.','muted'));l.addStretch()
    def device_box(self,kind,current):
        c=W.QComboBox();c.addItem('System default',None)
        for i,d in enumerate(sd.query_devices()):
            if d['max_'+kind+'_channels']>0:c.addItem(f"{i}: {d['name']}",i)
        idx=c.findData(current)
        if idx<0 and current is not None:c.addItem(f'Unavailable device {current}',current);idx=c.count()-1
        c.setCurrentIndex(max(0,idx));return c
    def audio_page(self):
        l=self.pages['Audio'];f=W.QFormLayout();l.addLayout(f);a=self.config.get('audio',{})
        self.mic=self.device_box('input',self.config['device']);self.speaker=self.device_box('output',a.get('output_device'));f.addRow('Microphone',self.mic);f.addRow('Feedback device',self.speaker)
        self.sound=W.QCheckBox('Enable audio feedback');self.sound.setChecked(self.config.get('beep',True));f.addRow(self.sound)
        self.wake_sound_on=W.QCheckBox('Play wake-word tone');self.wake_sound_on.setChecked(self.config.get('wake_beep',False));f.addRow(self.wake_sound_on)
        self.volume=spin(0,1,a.get('volume',1.),True);f.addRow('Feedback volume',self.volume)
        self.sound_fields={}
        for key,title in [('wake_sound','Wake WAV'),('success_sound','Success WAV')]:
            field=W.QLineEdit(a.get(key) or '');self.sound_fields[key]=field;row=W.QHBoxLayout();row.addWidget(field);b=W.QPushButton('Browse');b.clicked.connect(lambda _,field=field:self.pick_sound(field));row.addWidget(b);f.addRow(title,row)
        row=W.QHBoxLayout();l.addLayout(row)
        for title,success in [('Test wake tone',False),('Test success tone',True)]:
            b=W.QPushButton(title);b.clicked.connect(lambda _,s=success:beep(True,s,self.audio_settings()));row.addWidget(b)
        l.addWidget(label('Use the Dashboard meter while the engine is running to test the microphone. WAV files must be 16-bit PCM.','muted'));l.addStretch()
    def pick_sound(self,field):
        name,_=W.QFileDialog.getOpenFileName(self,'Select feedback sound','','WAV (*.wav)')
        if name:field.setText(name)
    def audio_settings(self):
        return {**self.config.get('audio',{}),'output_device':self.speaker.currentData(),'volume':self.volume.value(),**{k:v.text().strip() or None for k,v in self.sound_fields.items()}}
    def output_page(self):
        l=self.pages['Output'];f=W.QFormLayout();l.addLayout(f);o=self.config['output']
        self.enabled=W.QCheckBox('Enable native keyboard output for this session');self.enabled.setChecked(self.args.enable_output);f.addRow(self.enabled)
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
            c['recognition'].update(mode=self.mode.currentText(),timeout=self.timeout.value(),silence_ms=self.silence.value(),stable_ms=self.stable.value(),pre_roll_ms=self.preroll.value())
            c['output'].update(foreground_title=self.foreground.text().strip() or None,default_hold_ms=self.hold.value())
            registry(c['commands'],c['output']['default_hold_ms'])
            tmp=self.path.with_suffix(self.path.suffix+'.tmp');tmp.write_text(json.dumps(c,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');tmp.replace(self.path)
            self.config=c;self.wake_title.setText(c['wake_word'].upper());self.status.setText('Configuration saved' if not self.thread else 'Saved — restart to apply');return True
        except Exception as exc:W.QMessageBox.warning(self,'Cannot save configuration',str(exc));return False
    def toggle(self):
        if self.thread:
            self.stop_event.set();self.start_btn.setEnabled(False);self.status.setText('Stopping…');return
        if not self.save():return
        args=copy.copy(self.args)
        for k in ['device','sensitivity','mode','timeout','silence_ms','stable_ms','pre_roll_ms']:setattr(args,k,None)
        args.wake_beep=False;args.no_beep=False;args.enable_output=self.enabled.isChecked()
        self.stop_event=threading.Event();self.count=self.rejects=0;self.total=0.;self.level=(0,0)
        self.start_btn.setText('Stop engine');self.status.setText('Starting…');self.enabled.setEnabled(False)
        def work():
            try:run_engine(args,stop=self.stop_event,emit=self.bridge.log.emit,event=self.bridge.event.emit,meter=self.set_level)
            except Exception as exc:self.bridge.log.emit(f'ERROR: {exc}');self.bridge.event.emit('error',{'message':str(exc)})
            finally:self.bridge.finished.emit()
        self.thread=threading.Thread(target=work,daemon=False);self.thread.start()
    def set_level(self,rms,peak):self.level=(rms,peak)
    def update_meter(self):
        rms,peak=self.level;self.bar.setValue(int(min(100,rms*300)));self.peak.setText(f'Peak: {peak:.0%}')
    def on_event(self,kind,data):
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
        self.thread=None;self.level=(0,0);self.start_btn.setText('Start engine');self.start_btn.setEnabled(True);self.enabled.setEnabled(True);self.status.setText('Stopped');self.state.setText('Ready to listen')
        if self.closing:self.close()
    def closeEvent(self,e):
        if self.thread:
            self.closing=True;self.stop_event.set();e.ignore();self.status.setText('Stopping…');return
        if self.tray:self.tray.hide()
        e.accept()

def launch(args):
    app=W.QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    try:window=Window(args)
    except Exception as exc:W.QMessageBox.critical(None,'Startup failed',str(exc));return 1
    window.show();return app.exec()
