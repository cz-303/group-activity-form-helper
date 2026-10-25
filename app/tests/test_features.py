from ui_wait import settle_view
import sys,unittest,tempfile,sqlite3,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_store import TaskStore,state_label
from core import SAMPLE,DEFAULTS,fill_known_fields

DEFAULTS = dict(DEFAULTS, group='测试学院2026级研究生群', 姓名='测试同学', 学号='TEST001', 班级='测试班', 学院='测试学院')
from field_report import inspect_fields
from app import App
from ui import refresh_detail,notify_new
import tkinter as tk
from playwright.sync_api import sync_playwright

class Persistence(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db';self.s=TaskStore(self.path)
 def tearDown(self):self.tmp.cleanup()
 def test_original_text_exact_and_dedup(self):
  body='活动通知\n请报名\n请报名\n'+SAMPLE
  self.s.add_many([SAMPLE],source='目标群',notices=[(SAMPLE,body)])
  self.s.add_many([SAMPLE],source='目标群',notices=[(SAMPLE,body)])
  restored=TaskStore(self.path);restored.recover_inflight()
  self.assertEqual(len(restored.notices(SAMPLE)),1)
  self.assertEqual(restored.notices(SAMPLE)[0]['body'],body)
 def test_multiple_links_share_notice(self):
  other='https://docs.qq.com/form/page/Other'
  body='活动报名\n'+SAMPLE+'\n'+other
  self.s.add_many([SAMPLE,other],'群',[(SAMPLE,body),(other,body)])
  self.assertEqual(self.s.notices(SAMPLE)[0]['body'],self.s.notices(other)[0]['body'])
 def test_legacy_schema_migration(self):
  p=Path(self.tmp.name)/'old'
  with sqlite3.connect(p) as d:
   d.execute("CREATE TABLE tasks(url TEXT PRIMARY KEY,source TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','opening','opened','failed','done')),attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL,note TEXT NOT NULL DEFAULT '')")
   d.execute("INSERT INTO tasks(url,source,created,updated,state) VALUES(?,?,0,0,'opened')",(SAMPLE,'旧群'))
  d.close()
  s=TaskStore(p);self.assertEqual(s.task(SAMPLE)['state'],'opened');self.assertEqual(s.task(SAMPLE)['disposition'],'active');self.assertEqual(s.notices(SAMPLE),[])
 def test_ignore_survives_repeated_capture_restart(self):
  self.s.add_many([SAMPLE]);self.s.disposition([SAMPLE],'ignored');self.s.add_many([SAMPLE])
  s=TaskStore(self.path);self.assertFalse(s.due());self.assertIsNone(s.claim());self.assertEqual(state_label(s.task(SAMPLE)),'已忽略')
  s.disposition([SAMPLE],'active');self.assertTrue(s.due())
 def test_snooze_resumes_at_time(self):
  self.s.add_many([SAMPLE]);until=time.time()+600;self.s.disposition([SAMPLE],'snoozed',until)
  self.assertFalse(self.s.due());self.s.wake_snoozed(until-1);self.assertFalse(self.s.due())
  s=TaskStore(self.path);self.assertEqual(state_label(s.task(SAMPLE)),'已暂缓');s.wake_snoozed(until+1);self.assertTrue(s.due())
 def test_snoozed_opened_not_reopened(self):
  self.s.add_many([SAMPLE]);self.s.claim();self.s.opened(SAMPLE);until=time.time()+50
  self.s.disposition([SAMPLE],'snoozed',until);self.assertFalse(self.s.actionable(SAMPLE))
  self.s.wake_snoozed(until+1);self.assertTrue(self.s.actionable(SAMPLE));self.assertFalse(self.s.due())
 def test_inflight_cannot_ignore(self):
  self.s.add_many([SAMPLE]);self.s.claim();self.assertEqual(self.s.disposition([SAMPLE],'ignored'),0)
 def test_field_report_stores_no_answers(self):
  self.s.add_many([SAMPLE]);self.s.claim();self.s.opened(SAMPLE)
  self.s.report(SAMPLE,{'missing':['联系电话'],'review':[],'blocked':[],'filled':4,'questions':5},'活动标题')
  self.assertEqual(self.s.task(SAMPLE)['title'],'活动标题');self.assertNotIn(DEFAULTS['姓名'],self.s.task(SAMPLE)['field_report'])

class Fields(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.p=sync_playwright().start();cls.b=cls.p.chromium.launch(channel='msedge',headless=True)
 @classmethod
 def tearDownClass(cls):cls.b.close();cls.p.stop()
 def test_missing_and_manual(self):
  page=self.b.new_page()
  try:
   page.set_content(''.join('<div class="question-main-content"><div class="question-title">'+t+'</div>'+c+'</div>' for t,c in [('姓名','<textarea></textarea>'),('联系电话','<textarea></textarea>'),('作品介绍','<textarea></textarea>'),('作品图片','<input type="file">'),('活动场次','<select><option>一</option></select>')]))
   fill_known_fields(page,DEFAULTS);r=inspect_fields(page)
   self.assertEqual(r['filled'],1);self.assertEqual(r['missing'],['联系电话','作品介绍']);self.assertEqual(len(r['review']),2)
   self.assertNotIn(DEFAULTS['姓名'],json.dumps(r,ensure_ascii=False))
   page.locator('textarea').nth(1).fill('test');self.assertNotIn('联系电话',inspect_fields(page)['missing'])
  finally:page.close()
 def test_login_not_false_missing(self):
  page=self.b.new_page()
  try:
   page.set_content('<div class="question-main-content"><div class="question-title">姓名</div><textarea disabled></textarea></div>')
   r=inspect_fields(page);self.assertEqual(r['missing'],[]);self.assertEqual(len(r['blocked']),1)
  finally:page.close()

class Layout(unittest.TestCase):
 def test_details_filter_and_notification_preferences(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=tk.Tk();root.withdraw()
   try:
    a=App(root,tmp);a.processing.clear();a.store.add_many([SAMPLE],'测试群',[(SAMPLE,'活动通知\n保留换行')]);settle_view(a)
    a.table.selection_set(SAMPLE);refresh_detail(a);settle_view(a)
    self.assertIn('保留换行',a.detail_tabs['通知原文'].get('1.0','end'))
    a.ignore_selected();settle_view(a);self.assertEqual(len(a.table.get_children()),0)
    a.filter_var.set('已忽略');settle_view(a);self.assertEqual(len(a.table.get_children()),1)
    a.table.selection_set(SAMPLE);a.restore_selected();a.filter_var.set('进行中');settle_view(a);self.assertEqual(len(a.table.get_children()),1)
    a.config.update(notify_new=False,notify_popup=False);notify_new(a,2);self.assertEqual(a.unread_new,0)
    a.config['notify_new']=True;notify_new(a,2);notify_new(a,3);self.assertEqual(a.unread_new,5);self.assertIsNone(a.reminder)
    root.update_idletasks();self.assertEqual(len(a.table['columns']),4);self.assertEqual(set(a.detail_tabs),{'通知原文','待补填','活动时间','处理信息'})
   finally:root.update_idletasks();root.update_idletasks();root.destroy()

if __name__=='__main__':unittest.main()

