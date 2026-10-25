import sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ui_layout import ResizeUpdate,filter_inline,set_wrap
from appearance import apply_theme

class ResizeUpdates(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
    def tearDown(self):
        self.root.update_idletasks();self.root.destroy()
    def pump(self,seconds):
        until=time.monotonic()+seconds
        while time.monotonic()<until:self.root.update();time.sleep(.002)
    def test_continuous_events_are_limited_and_final_size_is_retained(self):
        frame=ttk.Frame(self.root);calls=[]
        job=ResizeUpdate(frame,lambda event:calls.append(event.width))
        for width in range(60):
            job.request(SimpleNamespace(width=width));self.root.update();time.sleep(.002)
        self.pump(.1)
        self.assertEqual(calls[-1],59)
        self.assertLess(len(calls),15)
    def test_destroy_cancels_pending_resize(self):
        frame=ttk.Frame(self.root);calls=[]
        job=ResizeUpdate(frame,lambda event:calls.append(event.width))
        job.request(SimpleNamespace(width=10));frame.destroy();self.pump(.08)
        self.assertEqual(calls,[])
    def test_same_wrap_width_does_not_reconfigure(self):
        label=ttk.Label(self.root,wraplength=200)
        with patch.object(label,'configure',wraps=label.configure) as configure:
            set_wrap(label,200);configure.assert_not_called()
            set_wrap(label,250);set_wrap(label,250);self.assertEqual(configure.call_count,1)
    def test_filter_breakpoint_has_hysteresis(self):
        self.assertTrue(filter_inline(840,840,None,16))
        self.assertTrue(filter_inline(830,840,True,16))
        self.assertFalse(filter_inline(823,840,True,16))
        self.assertFalse(filter_inline(850,840,False,16))
        self.assertTrue(filter_inline(856,840,False,16))
    def test_larger_fill_images_do_not_inflate_controls(self):
        apply_theme(self.root)
        button=ttk.Button(self.root,text='测试')
        panel=ttk.LabelFrame(self.root,text='测试');ttk.Label(panel,text='内容').pack()
        self.root.update_idletasks()
        images=next(iter(self.root._rounded_theme_assets.values()))[1]
        self.assertLess(button.winfo_reqheight(),images[0].height())
        self.assertLess(panel.winfo_reqheight(),images[20].height())
