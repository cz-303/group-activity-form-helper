import sys,unittest,tempfile,json,time
from pathlib import Path
from datetime import datetime,timedelta
from zipfile import ZipFile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_store import TaskStore
from core import SAMPLE
from app import App,SETTINGS,validate_settings
from reminders import countdown,due_alerts,MonitorHealth
from backup_support import make_backup,inspect_backup,stage_restore,apply_pending
from appearance import apply_theme
import tkinter as tk

class Reminders(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.s=TaskStore(self.path/'tasks.sqlite3');self.s.add_many([SAMPLE]);self.config=validate_settings(SETTINGS)
 def tearDown(self):self.tmp.cleanup()
 def test_thresholds_persist_and_clock_backwards(self):
  self.s.save_dates(SAMPLE,'','','2026-09-25 15:00')
  self.assertEqual(len(due_alerts(self.s,self.config,datetime(2026,9,25,14))),1)
  self.s=TaskStore(self.path/'tasks.sqlite3')
  self.assertEqual(due_alerts(self.s,self.config,datetime(2026,9,25,14,1)),[])
  self.assertEqual(due_alerts(self.s,self.config,datetime(2026,9,25,13)),[])
  self.assertEqual(len(due_alerts(self.s,self.config,datetime(2026,9,25,14,30))),1)
  self.assertEqual(due_alerts(self.s,self.config,datetime(2026,9,25,15,1)),[])
  self.assertEqual(len(self.s.alerts()),2)
 def test_offline_catchup_coalesces(self):
  self.s.save_dates(SAMPLE,'','','2026-09-25 15:00')
  self.assertEqual(len(due_alerts(self.s,self.config,datetime(2026,9,25,14,50))),1)
  self.assertEqual(due_alerts(self.s,self.config,datetime(2026,9,25,14,51)),[])
 def test_ignored_done_snoozed_not_reminded(self):
  self.s.save_dates(SAMPLE,'','','2026-09-25 15:00')
  for mode in ['ignored','snoozed']:
   self.s.disposition([SAMPLE],mode,time.time()+1000)
   self.assertEqual(due_alerts(self.s,self.config,datetime(2026,9,25,14,50)),[])
  self.s.done([SAMPLE]);self.assertEqual(due_alerts(self.s,self.config,datetime(2026,9,25,14,50)),[])
 def test_countdown_date_only_and_minutes(self):
  self.assertEqual(countdown('2026-09-25 15:00',datetime(2026,9,25,14,30)),'剩余 30分钟')
  self.assertEqual(countdown('2026-09-25',datetime(2026,9,25,23,59,59)),'不足1分钟')
  self.assertEqual(countdown('2026-09-25',datetime(2026,9,26)),'已截止')
 def test_changed_deadline_new_reminder(self):
  self.s.save_dates(SAMPLE,'','','2026-09-25 15:00');due_alerts(self.s,self.config,datetime(2026,9,25,14,30))
  self.s.save_dates(SAMPLE,'','','2026-09-25 16:00');self.assertEqual(len(due_alerts(self.s,self.config,datetime(2026,9,25,15,30))),1)
 def test_updates_dedup_first_capture_and_read_marker(self):
  self.s.add_many([SAMPLE],notices=[(SAMPLE,'原通知')]);self.assertEqual(due_alerts(self.s,self.config),[])
  self.s.add_many([SAMPLE],notices=[(SAMPLE,'改期通知')]);self.assertTrue(self.s.task(SAMPLE)['notice_unread'])
  self.assertEqual(len(due_alerts(self.s,self.config)),1)
  self.s.add_many([SAMPLE],source='转发',notices=[(SAMPLE,'改期通知')]);self.assertEqual(due_alerts(self.s,self.config),[])
  self.s.read_notice(SAMPLE);self.assertFalse(self.s.task(SAMPLE)['notice_unread'])
 def test_monitor_failure_once_recovery_and_stall(self):
  h=MonitorHealth();h.start(0);h.observe(False,3)
  self.assertFalse(h.check(None,30,20));self.assertTrue(h.check(None,30,31));self.assertFalse(h.check(None,30,80))
  self.assertTrue(h.observe(True,81));self.assertFalse(h.observe(True,82));self.assertTrue(h.check(31,30,113))
  h.reset();self.assertFalse(h.check(None,30,200))
  h.start(0);h.observe(True,0)
  self.assertFalse(h.check(60,30,60,125)) # Slow polling must not cause a false alarm.
  self.assertTrue(h.check(126,30,126,125))

class Backups(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name);self.store=TaskStore(self.data/'tasks.sqlite3');self.store.add_many([SAMPLE]);self.config=validate_settings(dict(SETTINGS,custom_fields={'专业':'水保'}))
  (self.data/'config.json').write_text(json.dumps(self.config),encoding='utf-8')
 def tearDown(self):self.tmp.cleanup()
 def test_roundtrip_and_safety_copy_without_browser(self):
  (self.data/'browser').mkdir();(self.data/'browser'/'cookie').write_text('do not copy')
  path=make_backup(self.store,self.data,self.config,'manual')
  with ZipFile(path) as z:self.assertEqual(set(z.namelist()),{'tasks.sqlite3','config.json','manifest.json'})
  self.store.add_many(['https://docs.qq.com/form/page/Other']);stage_restore(path,self.data,validate_settings)
  self.assertEqual(len(self.store.rows()),2)
  message=apply_pending(self.data,validate_settings);self.assertIn('1 项',message)
  self.assertEqual(len(TaskStore(self.data/'tasks.sqlite3').rows()),1)
  self.assertFalse(json.loads((self.data/'config.json').read_text(encoding='utf-8'))['auto_resume'])
  safety=list((self.data/'backups').glob('before-restore*'));self.assertEqual(len(safety),1)
  self.assertEqual(inspect_backup(safety[0],validate_settings)[3],2)
 def test_corrupt_or_extra_entry_refused_without_change(self):
  path=make_backup(self.store,self.data,self.config)
  with ZipFile(path,'a') as z:z.writestr('../evil.txt','x')
  with self.assertRaises(ValueError):stage_restore(path,self.data,validate_settings)
  self.assertFalse((self.data/'restore_pending.zip').exists());self.assertEqual(len(self.store.rows()),1)
 def test_auto_retention_leaves_manual(self):
  manual=make_backup(self.store,self.data,self.config,'manual')
  for _ in range(4):make_backup(self.store,self.data,self.config,keep=2)
  self.assertEqual(len(list((self.data/'backups').glob('auto-*'))),2);self.assertTrue(manual.exists())
 def test_restore_journal_recovers_pair(self):
  safety=make_backup(self.store,self.data,self.config,'before-restore')
  (self.data/'config.json').write_text('{}')
  (self.data/'restore_journal.json').write_text(json.dumps({'safety':safety.name}))
  apply_pending(self.data,validate_settings)
  self.assertEqual(json.loads((self.data/'config.json').read_text(encoding='utf-8'))['custom_fields'],{'专业':'水保'})

class Theme(unittest.TestCase):
 def test_both_themes_and_options(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=tk.Tk();root.withdraw()
   try:
    a=App(root,tmp);a.processing.clear()
    for theme in ['dark','light']:
     apply_theme(root,theme);root.update_idletasks()
     from design_tokens import PALETTES
     self.assertEqual(root.cget('background'),PALETTES[theme]['bg'])
    self.assertEqual(validate_settings(dict(SETTINGS,deadline_minutes='60,30,10',theme='深色'))['theme'],'dark')
    with self.assertRaises(ValueError):validate_settings(dict(SETTINGS,deadline_minutes='0,abc'))
   finally:root.update_idletasks();root.update_idletasks();root.destroy()

if __name__=='__main__':unittest.main()
