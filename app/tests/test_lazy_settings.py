import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tkinter as tk
import json,subprocess
from tkinter import ttk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from appearance import themed
from ui_layout import IdleUpdate

def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)

class SettingsLoading(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,self.tmp.name);self.app.processing.clear()
    def tearDown(self):
        self.root.update_idletasks();self.root.destroy();self.tmp.cleanup()
    def test_only_first_panel_created_and_unopened_values_survive_save(self):
        self.app.config.update(custom_fields={'专业':'测试专业'},auto_open_forms=False,deadline_minutes=[90,20])
        expected=self.app.get_config()
        self.app.open_settings();nb=self.app.settings_notebook
        self.assertEqual(len(nb.tabs()),6);self.assertEqual(len(nb.built),1)
        controls=list(children(self.app.settings_window))
        self.assertFalse(any(isinstance(w,ttk.Checkbutton) for w in controls))
        next(w for w in controls if isinstance(w,ttk.Button) and w.cget('text')=='保存设置').invoke()
        self.assertEqual(self.app.get_config(),expected)
    def test_panel_is_built_once_and_edits_survive_switch(self):
        self.app.open_settings();nb=self.app.settings_notebook
        nb.select(2);nb.ensure_selected()
        panel=self.root.nametowidget(nb.select());controls=list(children(panel))
        toggle=next(w for w in controls if isinstance(w,ttk.Checkbutton) and w.cget('text')=='收集表：捕获链接后自动跳转')
        toggle.invoke();value=toggle.getvar(toggle.cget('variable'))
        nb.select(0);nb.ensure_selected();nb.select(2);nb.ensure_selected()
        self.assertEqual(toggle.getvar(toggle.cget('variable')),value)
        self.assertEqual(len(nb.built),2)
        for index in range(6):nb.select(index);nb.ensure_selected()
        self.assertEqual(len(nb.built),6)
    def test_target_theme_does_not_walk_root(self):
        win=tk.Toplevel(self.root);text=tk.Text(win);text.pack()
        with patch.object(self.root,'winfo_children',side_effect=AssertionError('root traversal')):
            themed(self.app,win)
        self.assertEqual(text.cget('background'),'#FFFFFF')
    def test_layout_burst_is_merged_and_destroy_cancels(self):
        frame=ttk.Frame(self.root);calls=[]
        job=IdleUpdate(frame,lambda event:calls.append(event.width))
        for number in range(100):job.request(SimpleNamespace(width=number))
        self.root.update_idletasks();self.assertEqual(calls,[99])
        job.request(SimpleNamespace(width=200));frame.destroy();self.root.update_idletasks()
        self.assertEqual(calls,[99])

class StartupProbe(unittest.TestCase):
    def test_probe_produces_timings_using_temporary_data(self):
        with tempfile.TemporaryDirectory(prefix='启动测试-') as directory:
            report=Path(directory)/'启动报告.json'
            result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'app.py'),
                                   '--startup-probe',str(report)],capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            payload=json.loads(report.read_text(encoding='utf-8'))
            self.assertTrue(payload['mapped']);self.assertFalse(payload['frozen'])
            self.assertGreater(payload['runtime_to_ready_seconds'],0)
