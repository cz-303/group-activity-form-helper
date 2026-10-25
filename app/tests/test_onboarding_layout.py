"""Allowed minimum dialog sizes must preserve usable confirmation actions."""
import sys
import tempfile
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from notice_tools import import_notice
from fill_preview import show_fill_preview,signature

URL='https://docs.qq.com/form/page/OnboardingLayoutLocalOnly'

def descendants(parent):
    for child in parent.winfo_children():
        yield child
        yield from descendants(child)

class OnboardingLayout(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=tk.Tk()
        self.app=App(self.root,self.temp.name)
        self.app.config.update(auto_open_forms=False,auto_open_documents=False,notify_popup=False,backup_enabled=False)
        self.app.processing.clear()
        self.callback_errors=[]
        original=self.root.report_callback_exception
        def report(kind,error,trace):
            self.callback_errors.append(type(error).__name__)
            original(kind,error,trace)
        self.root.report_callback_exception=report

    def tearDown(self):
        self.root.update_idletasks()
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.callback_errors,[],'A dialog callback failed even though controls remained visible')

    def pump(self):
        until=time.monotonic()+.15
        while time.monotonic()<until:
            self.root.update()
            time.sleep(.004)

    def button(self,window,text):
        return next(widget for widget in descendants(window) if isinstance(widget,ttk.Button) and widget.cget('text')==text)

    def assert_usable(self,window,widget):
        self.assertTrue(widget.winfo_ismapped())
        self.assertGreaterEqual(widget.winfo_height(),widget.winfo_reqheight())
        self.assertGreaterEqual(widget.winfo_width(),widget.winfo_reqwidth())
        x=widget.winfo_rootx()-window.winfo_rootx()
        y=widget.winfo_rooty()-window.winfo_rooty()
        self.assertGreaterEqual(x,0)
        self.assertGreaterEqual(y,0)
        self.assertLessEqual(x+widget.winfo_width(),window.winfo_width()+1)
        self.assertLessEqual(y+widget.winfo_height(),window.winfo_height()+1)

    def shrink(self,window):
        width,height=window.minsize()
        window.geometry(f'{width}x{height}')
        self.pump()

    def test_import_preview_can_still_be_confirmed_after_shrinking(self):
        import_notice(self.app)
        window=next(widget for widget in self.root.winfo_children() if isinstance(widget,tk.Toplevel))
        text=next(widget for widget in descendants(window) if isinstance(widget,tk.Text) and str(widget.cget('state'))=='normal')
        text.insert('1.0','本地合成通知\n'+URL)
        self.pump()
        self.button(window,'分析预览').invoke()
        self.shrink(window)
        for label in ('分析预览','确认导入','粘贴剪贴板文字'):
            self.assert_usable(window,self.button(window,label))
        self.assertFalse(self.button(window,'确认导入').instate(['disabled']))
        self.button(window,'确认导入').invoke()
        self.assertEqual(self.app.store.state(URL),'pending')
        self.assertEqual(self.app.store.notices(URL)[0]['body'],'本地合成通知\n'+URL)
        self.assertIsNone(self.app.browser_thread)

    def test_fill_preview_keeps_edited_approval_and_actions_after_shrinking(self):
        self.app.table.insert('','end',iid=URL,values=('合成表单','待处理','未检查','未设置'))
        self.app.table.selection_set(URL)
        plan=[dict(index=0,title='姓名',key='姓名',value='初始合成资料',allowed=True,status='等待确认')]
        self.app.previews[URL]=dict(token=signature(plan),plan=plan,state='pending')
        show_fill_preview(self.app)
        window=next(widget for widget in self.root.winfo_children() if isinstance(widget,tk.Toplevel))
        table=next(widget for widget in descendants(window) if isinstance(widget,ttk.Treeview))
        table.selection_set('0')
        self.pump()
        edit=next(widget for widget in descendants(window) if isinstance(widget,tk.Text))
        edit.delete('1.0','end');edit.insert('1.0','本次合成资料')
        self.shrink(window)
        for label in ('确认填写（不提交）','取消'):
            self.assert_usable(window,self.button(window,label))
        self.button(window,'确认填写（不提交）').invoke()
        self.assertEqual(self.app.previews[URL]['state'],'approved')
        self.assertEqual(self.app.previews[URL]['approved'],{0:'本次合成资料'})
        self.assertIsNone(self.app.browser_thread)

if __name__=='__main__':
    unittest.main()
