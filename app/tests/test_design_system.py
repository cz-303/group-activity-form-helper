import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from appearance import apply_theme
from design_tokens import PALETTES, STATES, FONTS
from ui_status import StatusVar, StatusLabel
from app import App
from core import SAMPLE
from notice_tools import import_notice


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def contrast(a,b):
    def luminance(color):
        channels=[int(color[n:n+2],16)/255 for n in (1,3,5)]
        channels=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return sum(v*k for v,k in zip(channels,(.2126,.7152,.0722)))
    a,b=sorted((luminance(a),luminance(b)))
    return (b+.05)/(a+.05)


class Tokens(unittest.TestCase):
    def test_semantic_text_and_primary_actions_have_readable_contrast(self):
        for name,p in PALETTES.items():
            for _,fg,bg in STATES.values():
                self.assertGreaterEqual(contrast(p[fg],p[bg]),4.5,(name,fg))
            for bg in ['accent','primary_hover','error','danger_hover']:
                self.assertGreaterEqual(contrast(p['primary_fg'],p[bg]),4.5,(name,bg))


class DesignWidgets(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.addCleanup(lambda:(self.root.update_idletasks(),self.root.destroy()))

    def test_state_color_and_text_survive_theme_switch(self):
        variable=StatusVar(self.root,'重试读取',state='error')
        label=StatusLabel(self.root,variable)
        for theme in ['light','dark','light']:
            apply_theme(self.root,theme)
            self.assertEqual(variable.get(),'重试读取')
            self.assertEqual(label.cget('text'),'未完成 · 重试读取')
            self.assertEqual(ttk.Style(self.root).lookup(label.cget('style'),'foreground'),PALETTES[theme]['error'])
        variable.set('恢复',state='success');self.assertEqual(label.cget('text'),'已完成 · 恢复')
        variable.set('下一条消息');self.assertEqual(variable.state,'info')
        variable.set('未知状态',state='unknown');self.assertEqual(variable.state,'info')
        variable.set('');self.assertEqual(label.cget('text'),'')

    def test_destroyed_feedback_does_not_keep_a_tcl_callback(self):
        variable=StatusVar(self.root,'状态')
        label=StatusLabel(self.root,variable);self.assertEqual(len(variable.trace_info()),1)
        label.destroy();self.assertEqual(variable.trace_info(),[])
        variable.set('异步操作完成',state='success')

    def test_rounded_controls_preserve_native_disabled_and_keyboard_behavior(self):
        apply_theme(self.root)
        calls=[];button=ttk.Button(self.root,text='确认',command=lambda:calls.append(1),style='Primary.TButton')
        button.pack();button.invoke();button.state(['disabled']);button.invoke();self.assertEqual(calls,[1])
        button.state(['!disabled']);self.root.deiconify();self.root.update();button.focus_force()
        button.event_generate('<KeyPress-space>');button.event_generate('<KeyRelease-space>');self.root.update()
        self.assertEqual(calls,[1,1])
        entry=ttk.Entry(self.root);entry.insert(0,'中文 🌙');self.assertEqual(entry.get(),'中文 🌙')
        combo=ttk.Combobox(self.root,values=['浅色','深色'],state='readonly');combo.current(1)
        self.assertEqual(combo.get(),'深色');self.assertEqual(button.winfo_class(),'TButton')

    def test_theme_cache_is_reused_and_scale_changes_control_height(self):
        button=ttk.Button(self.root,text='测试尺寸');button.pack();sizes=[]
        for scale in (1,1.25,1.5,2):
            self.root.tk.call('tk','scaling',scale*96/72)
            apply_theme(self.root);self.root.update_idletasks();sizes.append(button.winfo_reqheight())
            count=len(self.root._rounded_theme_assets)
            for _ in range(3):apply_theme(self.root)
            self.assertEqual(len(self.root._rounded_theme_assets),count)
        self.assertEqual(sizes,sorted(sizes));self.assertGreater(sizes[-1],sizes[0])

    def test_all_text_editors_inherit_body_font(self):
        widgets=[tk.Text(self.root),ttk.Entry(self.root),ttk.Combobox(self.root)]
        apply_theme(self.root)
        for widget in widgets:
            self.assertEqual(self.root.tk.splitlist(widget.cget('font')),tuple(map(str,FONTS['body'])))

    def test_checkbox_preserves_toggle_and_disabled_state(self):
        value=tk.BooleanVar(self.root,False);button=ttk.Checkbutton(self.root,variable=value)
        apply_theme(self.root,'dark');button.invoke();self.assertTrue(value.get())
        button.state(['disabled']);button.invoke();self.assertTrue(value.get())

    def test_compact_window_keeps_detail_and_primary_action_visible(self):
        for scale,width,height in [(1,900,620),(2,1840,980)]:
            with self.subTest(scale=scale),tempfile.TemporaryDirectory() as folder:
                root=tk.Tk();root.tk.call('tk','scaling',scale*96/72)
                app=App(root,folder);root.after_cancel(app.tick_timer)
                try:
                    root.minsize(900,620);root.geometry(f'{width}x{height}');root.update()
                    # Compact mode deliberately shows the list first. Open details
                    # before checking the detail surface and shared primary action.
                    app.list_detail.show_detail()
                    root.after(160,root.quit);root.mainloop()
                    detail=app.detail_tabs['通知原文']
                    self.assertTrue(detail.winfo_ismapped())
                    self.assertGreaterEqual(detail.winfo_height(),60*scale)
                    self.assertLessEqual(app.preview_button.winfo_rooty()+app.preview_button.winfo_height(),root.winfo_rooty()+height)
                finally:root.update_idletasks();root.destroy()


class AppFeedback(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='界面状态🌙-');self.addCleanup(self.temp.cleanup)
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.temp.name)
        self.root.after_cancel(self.app.tick_timer)
        self.app.config.update(backup_enabled=False,notify_popup=False)
        self.addCleanup(lambda:(self.root.update_idletasks(),self.root.destroy()))

    def test_worker_event_is_applied_only_on_ui_thread_and_preserves_log_text(self):
        before=self.app.status.get()
        worker=threading.Thread(target=lambda:self.app.emit('页面已打开，尚未提交',state='warning'))
        worker.start();worker.join()
        self.assertEqual(self.app.status.get(),before)
        self.app._tick_once();self.assertEqual(self.app.status.state,'warning')
        self.assertEqual(self.app.status.get(),'页面已打开，尚未提交')
        self.assertTrue(self.app.log_lines[-1].endswith('页面已打开，尚未提交'))

    def test_query_error_and_file_error_have_explicit_severity(self):
        self.app.query_failed(self.app.view_request(),PermissionError())
        self.assertEqual(self.app.query_status.state,'error')
        feedback=StatusVar(self.root)
        with patch('app.messagebox.showerror'):
            self.app.run_file_job('测试文件',lambda:(_ for _ in ()).throw(PermissionError()),lambda _:None,feedback=feedback)
            self.assertEqual(feedback.state,'running')
            self.app.jobs.thread.join(2);self.app.jobs.poll()
            self.assertEqual(feedback.state,'error');self.assertEqual(self.app.status.state,'error')

    def test_import_requires_current_preview_and_failed_reanalysis_revokes_it(self):
        import_notice(self.app);win=[w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel)][0]
        widgets=list(descendants(win));texts=[w for w in widgets if isinstance(w,tk.Text)]
        buttons={str(w.cget('text')):w for w in widgets if isinstance(w,ttk.Button)}
        body=next(w for w in texts if str(w.cget('state'))=='normal')
        confirm=buttons['确认导入'];analyse=buttons['分析预览']
        self.assertTrue(confirm.instate(['disabled']))
        body.insert('1.0','活动通知 '+SAMPLE);self.root.update();analyse.invoke();self.root.update()
        self.assertFalse(confirm.instate(['disabled']))
        body.insert('end',' 新通知');self.root.update();self.assertTrue(confirm.instate(['disabled']))
        analyse.invoke();self.root.update();self.assertFalse(confirm.instate(['disabled']))
        body.delete('1.0','end');self.root.update()
        with patch('notice_tools.messagebox.showerror'):analyse.invoke()
        self.assertTrue(confirm.instate(['disabled']))
        self.assertEqual(len(self.app.store.rows()),0)


if __name__=='__main__':unittest.main()
