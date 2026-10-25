from ui_wait import settle_view
import sys,unittest,tempfile,json,hashlib,threading,time
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from notice_tools import parse_import,import_notice,capture_diagnostic
from fill_preview import build_plan,signature,apply_approved,show_fill_preview
from migration_ui import snapshot_old_folder
from task_store import TaskStore
from reminders import due_alerts
from app import App,SETTINGS,validate_settings
from core import SAMPLE,DEFAULTS

DEFAULTS = dict(DEFAULTS, group='测试学院2026级研究生群', 姓名='测试同学', 学号='TEST001', 班级='测试班', 学院='测试学院')
from activity_editor import edit_activity
from backup_support import inspect_backup
from playwright.sync_api import sync_playwright
from PIL import Image
import tkinter as tk
from tkinter import ttk
OTHER='https://docs.qq.com/form/page/Other'

def children(w):
 for child in w.winfo_children():
  yield child
  yield from children(child)

class ImportAndTasks(unittest.TestCase):
 def test_import_blocks_and_association(self):
  urls,notices,missing=parse_import('通知甲\n'+SAMPLE+'\n\n保留段落\n---\n通知乙\n'+OTHER+'\n---\n没有链接')
  self.assertEqual(set(urls),{SAMPLE,OTHER});self.assertEqual(missing,1)
  self.assertEqual(dict(notices)[SAMPLE],'通知甲\n'+SAMPLE+'\n\n保留段落')
 def test_supplement_link_only_one_body(self):
  self.assertEqual(parse_import('完整正文',SAMPLE)[1],[(SAMPLE,'完整正文')])
  with self.assertRaises(ValueError):parse_import('甲\n---\n乙',SAMPLE)
  with self.assertRaises(ValueError):parse_import('仅通知')
 def test_intent_checklist_and_per_task_reminders(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=TaskStore(Path(tmp)/'db');s.add_many([SAMPLE]);s.save_dates(SAMPLE,'','','2026-09-25 16:00')
   s.save_activity(SAMPLE,'准备手绘作品','想参加',[{'text':'准备图片','done':True},{'text':'写介绍','done':False}],'custom','10')
   s=TaskStore(Path(tmp)/'db');self.assertEqual(s.task(SAMPLE)['intent'],'想参加');self.assertIn(SAMPLE,s.matching_urls('手绘'))
   config=dict(SETTINGS,deadline_reminders=False)
   self.assertEqual(due_alerts(s,config,datetime(2026,9,25,15,30)),[])
   self.assertEqual(len(due_alerts(s,config,datetime(2026,9,25,15,50))),1)
   s.remind_later(SAMPLE,10);self.assertIsNotNone(s.task(SAMPLE)['remind_again_at'])
   s.save_activity(SAMPLE,'','不参加',[],'off','');self.assertIsNone(s.task(SAMPLE)['remind_again_at'])
 def test_remind_again_persists_and_only_once(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=TaskStore(Path(tmp)/'db');s.add_many([SAMPLE]);s.remind_later(SAMPLE,10)
   stamp=s.task(SAMPLE)['remind_again_at'];s=TaskStore(Path(tmp)/'db')
   self.assertEqual(due_alerts(s,SETTINGS,datetime.fromtimestamp(stamp-1)),[])
   self.assertEqual(len(due_alerts(s,SETTINGS,datetime.fromtimestamp(stamp+1))),1)
   self.assertEqual(due_alerts(s,SETTINGS,datetime.fromtimestamp(stamp+2)),[])
 def test_migration_does_not_change_old_database(self):
  with tempfile.TemporaryDirectory() as old,tempfile.TemporaryDirectory() as tmp:
   folder=Path(old);s=TaskStore(folder/'tasks.sqlite3');s.add_many([SAMPLE]);s.save_activity(SAMPLE,'备注','待考虑',[{'text':'附件','done':False}],'custom','30');s.claim()
   before=hashlib.sha256((folder/'tasks.sqlite3').read_bytes()).hexdigest()
   package,count=snapshot_old_folder(folder,tmp,validate_settings)
   self.assertEqual(count,1);self.assertEqual(inspect_backup(package,validate_settings)[3],1)
   self.assertEqual(hashlib.sha256((folder/'tasks.sqlite3').read_bytes()).hexdigest(),before)
   self.assertEqual(s.task(SAMPLE)['state'],'opening')
 def test_icon_multi_resolution(self):
  icon=Image.open(Path(__file__).resolve().parents[1]/'activity-helper.ico')
  self.assertTrue({(16,16),(32,32),(256,256)}.issubset(icon.ico.sizes()));icon.close()

class Preview(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.p=sync_playwright().start();cls.b=cls.p.chromium.launch(channel='msedge',headless=True)
 @classmethod
 def tearDownClass(cls):cls.b.close();cls.p.stop()
 def setUp(self):
  self.page=self.b.new_page();self.page.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<html></html>'));self.page.goto(SAMPLE)
  self.page.set_content(''.join('<div class="question-main-content"><div class="question-title">'+t+'</div>'+v+'</div>' for t,v in [('姓名','<textarea></textarea>'),('学号','<textarea></textarea>'),('联系电话','<textarea></textarea>'),('作品','<input type="file">')]))
 def tearDown(self):self.page.close()
 def test_plan_readonly_and_explicit_edited_approval(self):
  plan=build_plan(self.page,DEFAULTS);self.assertEqual(self.page.locator('textarea').evaluate_all('(xs)=>xs.map(x=>x.value)'),['','',''])
  n=apply_approved(self.page,SAMPLE,DEFAULTS,plan,{0:'本次姓名',1:'',2:'13800138000'})
  self.assertEqual(n,2);self.assertEqual(self.page.locator('textarea').evaluate_all('(xs)=>xs.map(x=>x.value)'),['本次姓名','','13800138000'])
 def test_schema_changed_refuses_all_writes(self):
  plan=build_plan(self.page,DEFAULTS);self.page.locator('.question-title').nth(1).evaluate('(e)=>e.textContent="其他题目"')
  with self.assertRaises(ValueError):apply_approved(self.page,SAMPLE,DEFAULTS,plan,{0:'测试同学'})
  self.assertEqual(self.page.locator('textarea').first.input_value(),'')
 def test_user_value_appeared_refuses_overwrite(self):
  plan=build_plan(self.page,DEFAULTS);self.page.locator('textarea').first.fill('自己填写')
  with self.assertRaises(ValueError):apply_approved(self.page,SAMPLE,DEFAULTS,plan,{0:'其他'})
  self.assertEqual(self.page.locator('textarea').first.input_value(),'自己填写')
 def test_protected_or_unknown_cannot_be_injected(self):
  plan=build_plan(self.page,DEFAULTS)
  with self.assertRaises(ValueError):apply_approved(self.page,SAMPLE,DEFAULTS,plan,{3:'arbitrary file'})
  with self.assertRaises(ValueError):apply_approved(self.page,OTHER,DEFAULTS,plan,{0:'测试同学'})

class UI(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw();self.a=App(self.root,self.tmp.name);self.a.processing.clear();self.a.store.add_many([SAMPLE]);settle_view(self.a);self.a.table.selection_set(SAMPLE)
 def tearDown(self):self.root.update_idletasks();self.root.destroy();self.tmp.cleanup()
 def test_preview_approval_stays_in_memory(self):
  a=self.a;plan=[{'index':0,'title':'姓名','key':'姓名','value':'测试同学','allowed':True,'status':'等待确认'}]
  a.previews[SAMPLE]={'token':signature(plan),'plan':plan,'state':'pending'};show_fill_preview(a)
  button=next(w for w in children(self.root) if isinstance(w,ttk.Button) and w.cget('text')=='确认填写（不提交）');button.invoke()
  self.assertEqual(a.previews[SAMPLE]['state'],'approved');self.assertEqual(a.previews[SAMPLE]['approved'],{0:'测试同学'})
  self.assertNotIn('approved',self.a.store.task(SAMPLE))
 def test_activity_editor_saves(self):
  edit_activity(self.a)
  win=next(w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel));notes=next(w for w in children(win) if isinstance(w,tk.Text));notes.insert('1.0','提前准备作品')
  next(w for w in children(win) if isinstance(w,ttk.Button) and w.cget('text')=='保存').invoke()
  self.assertEqual(self.a.store.task(SAMPLE)['activity_notes'],'提前准备作品')
 def test_diagnostic_does_not_import_automatically(self):
  capture_diagnostic(self.a);w=self.a.diagnostic_window
  run=next(x for x in children(w) if isinstance(x,ttk.Button) and x.cget('text')=='开始 / 重新检查')
  with patch('app.read_group',return_value=['测试 '+OTHER]),patch('adapters.chat_client.client_windows',return_value=[]):
   run.invoke();until=time.time()+5
   while time.time()<until:
    self.root.update();time.sleep(.05)
    texts=[x for x in children(w) if isinstance(x,tk.Text)]
    if '尚未保存' in texts[0].get('1.0','end'):break
  self.assertIn('尚未保存 1',texts[0].get('1.0','end'));self.assertIsNone(self.a.store.task(OTHER))

 def test_browser_worker_requires_one_use_approval(self):
  from test_queue import Context,PW
  a=self.a;context=Context();plan=[{'index':0,'title':'姓名','key':'姓名','value':'测试同学','allowed':True,'status':'等待确认'}]
  a.processing.set()
  def wait_for(predicate):
   until=time.monotonic()+4
   while not predicate():
    if time.monotonic()>until:self.fail('Worker did not reach expected state')
    time.sleep(.02)
  with patch('playwright.sync_api.sync_playwright',lambda:PW(context)),patch('app.build_plan',return_value=plan),patch('app.apply_approved',return_value=1) as fill:
   thread=threading.Thread(target=a.browser);thread.start()
   try:
    wait_for(lambda:SAMPLE in a.previews);time.sleep(.1);self.assertEqual(fill.call_count,0)
    with a.preview_lock:a.previews[SAMPLE].update(state='approved',approved={0:'测试同学'})
    wait_for(lambda:fill.call_count==1);time.sleep(.1);self.assertEqual(fill.call_count,1)
   finally:a.quit_event.set();thread.join(4)
   self.assertFalse(thread.is_alive())

if __name__=='__main__':unittest.main()
