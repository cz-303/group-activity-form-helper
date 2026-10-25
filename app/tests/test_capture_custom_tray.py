import sys,unittest,tempfile,threading,queue,time
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App,validate_settings,SETTINGS
from core import SAMPLE,DEFAULTS,capture_notifications,validate_custom_fields,fill_known_fields

DEFAULTS = dict(DEFAULTS, group='测试学院2026级研究生群', 姓名='测试同学', 学号='TEST001', 班级='测试班', 学院='测试学院')
from task_store import TaskStore
from tray_support import Tray
from playwright.sync_api import sync_playwright
import tkinter as tk
from tkinter import ttk

class Capture(unittest.TestCase):
 def test_same_link_merges_all_variants_without_requeuing(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=TaskStore(Path(tmp)/'db')
   for text in [SAMPLE,SAMPLE,'活动改期 '+SAMPLE+'?from=chat']:
    urls,notices=capture_notifications([text]);s.add_many(urls,'群',notices)
   self.assertEqual(len(s.rows()),1);self.assertEqual(len(s.notices(SAMPLE)),2)
   s.done([SAMPLE]);urls,notices=capture_notifications([SAMPLE]);self.assertEqual(s.add_many(urls,'群',notices),0);self.assertEqual(s.state(SAMPLE),'done')
 def test_monitor_first_poll_and_failed_save_retry(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=tk.Tk();root.withdraw()
   try:
    a=App(root,tmp);a.processing.clear();stop=threading.Event();reads=[0];original=a.store.add_many;calls=[0]
    def read(*args):
     reads[0]+=1
     if reads[0]>3:stop.set()
     return [SAMPLE]
    def save(*args,**kwargs):
     calls[0]+=1
     if calls[0]==1:raise OSError('temporary save failure')
     return original(*args,**kwargs)
    with patch('app.read_group',side_effect=read),patch.object(a.store,'add_many',side_effect=save):
     a.monitor(dict(a.config,poll_seconds=0),stop)
    self.assertEqual(len(a.store.rows()),1);self.assertEqual(len(a.store.notices(SAMPLE)),1)
    events=[]
    while not a.events.empty():events.append(a.events.get_nowait())
    self.assertEqual(sum(isinstance(e,tuple) and e[0]=='new' for e in events),1)
   finally:root.update_idletasks();root.update_idletasks();root.destroy()

class Custom(unittest.TestCase):
 def test_validation_duplicates_and_protected(self):
  for values in [{' 学生姓名':'a'},{'专业':'A','专业：':'B'},{'验证码':'123'},{'':'a'}]:
   with self.assertRaises(ValueError):validate_custom_fields(values)
  self.assertEqual(validate_settings(dict(SETTINGS,custom_fields={'专业':'水土保持'}))['custom_fields'],{'专业':'水土保持'})
 def test_exact_only_and_no_overwrite_or_password(self):
  with sync_playwright() as p:
   b=p.chromium.launch(channel='msedge',headless=True);page=b.new_page()
   cards=[('专业','<textarea></textarea>'),('研究方向','<textarea>人工已填写</textarea>'),('导师','<input type="password">'),('备用专业','<textarea></textarea>')]
   page.set_content(''.join('<div class="question-main-content"><div class="question-title">'+t+'</div>'+v+'</div>' for t,v in cards))
   fill_known_fields(page,dict(DEFAULTS,custom_fields={'专业':'水土保持','研究方向':'生态','导师':'老师'}))
   self.assertEqual(page.locator('textarea').evaluate_all('(xs)=>xs.map(x=>x.value)'),['水土保持','人工已填写','']);self.assertEqual(page.locator('input').input_value(),'');b.close()
 def test_custom_editor_saved_draft(self):
  from profile_editor import build_custom_editor
  root=tk.Tk();root.withdraw()
  try:
   nb=ttk.Notebook(root);read=build_custom_editor(nb,{'专业':'旧值'})
   frame=nb.nametowidget(nb.tabs()[0]);entry=next(w for w in frame.winfo_children() if isinstance(w,ttk.Entry));text=next(w for w in frame.winfo_children() if isinstance(w,tk.Text))
   entry.insert(0,'研究方向');text.insert('1.0','生态修复')
   self.assertEqual(read(),{'专业':'旧值','研究方向':'生态修复'})
  finally:root.update_idletasks();root.update_idletasks();root.destroy()

class TrayTests(unittest.TestCase):
 def test_real_tray_starts_and_stops(self):
  events=queue.Queue();tray=Tray(events);tray.start()
  try:
   self.assertTrue(tray.ready.wait(10));self.assertEqual(events.get(timeout=2)[0],'tray_ready')
   self.assertTrue(tray.icon.visible)
  finally:tray.stop();tray.thread.join(5)
  self.assertFalse(tray.thread.is_alive())
 def test_hide_restore_and_failed_tray_keeps_window(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=tk.Tk();root.withdraw()
   try:
    a=App(root,tmp);a.processing.clear();a.tray=Mock();a.tray.ready.is_set.return_value=True
    a.hide_to_tray();self.assertEqual(root.state(),'withdrawn');self.assertFalse(a.quit_event.is_set())
    a.handle_tray(('tray_show',));self.assertEqual(root.state(),'normal')
    root.withdraw();a.handle_tray(('tray_failed','测试错误'));self.assertEqual(root.state(),'normal')
    self.assertIn('测试错误',a.status.get())
    a.close();self.assertTrue(a.close_window.winfo_exists());self.assertFalse(a.closing)
   finally:root.update_idletasks();root.update_idletasks();root.destroy()

if __name__=='__main__':unittest.main()
