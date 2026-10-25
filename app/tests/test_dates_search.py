from ui_wait import settle_view
import sys, unittest, tempfile, sqlite3, threading
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from activity_time import validate_dates, filter_time, deadline_label, sort_tasks, time_references
from task_store import TaskStore
from core import SAMPLE
from app import App
from ui import refresh_detail, edit_dates
import tkinter as tk

OTHER='https://docs.qq.com/form/page/Other'

class Dates(unittest.TestCase):
 def test_validation(self):
  for values in [('2026-02-29','',''),('','','明天'),('2026-09-25 15:00','2026-09-25 14:00',''),('','2026-09-25','')]:
   with self.assertRaises(ValueError):validate_dates(*values)
  self.assertEqual(validate_dates('2028-02-29','','2028-03-01'),('2028-02-29','','2028-03-01'))
 def test_date_only_deadline_includes_whole_day(self):
  row={'deadline':'2026-09-25'}
  self.assertFalse(filter_time(row,'已截止',datetime(2026,9,25,23,59,59)))
  self.assertTrue(filter_time(row,'已截止',datetime(2026,9,26)))
  self.assertTrue(filter_time(row,'24小时内截止',datetime(2026,9,25)))
 def test_missing_time_never_matches_upcoming(self):
  self.assertFalse(filter_time({},'24小时内截止'))
  self.assertTrue(filter_time({},'未设截止'))
  self.assertFalse(filter_time({},'今天有活动'))
 def test_interval_overlaps_day(self):
  self.assertTrue(filter_time({'event_start':'2026-09-24','event_end':'2026-09-26'},'今天有活动',datetime(2026,9,25,12)))
  self.assertFalse(filter_time({'event_start':'2026-09-26'},'今天有活动',datetime(2026,9,25,12)))
 def test_sort_unscheduled_last_and_keep_original(self):
  rows=[{'created':3,'deadline':''},{'created':2,'deadline':'2026-09-26'},{'created':1,'deadline':'2026-09-25'}]
  self.assertEqual([r['created'] for r in sort_tasks(rows,'截止时间最近')],[1,2,3])
  self.assertEqual(rows[0]['created'],3)
 def test_reference_does_not_infer_relative_date(self):
  text='各位同学\n本周五15:00举行活动\n截止时间：9月25日\n本周五15:00举行活动'
  ref=time_references([{'body':text}])
  self.assertEqual(ref,'本周五15:00举行活动\n\n截止时间：9月25日')

class Persistence(unittest.TestCase):
 def test_dates_survive_restart_and_duplicate_capture(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'tasks';s=TaskStore(path);s.add_many([SAMPLE]);s.save_dates(SAMPLE,'2026-09-25 15:00','','2026-09-24')
   s.add_many([SAMPLE],notices=[(SAMPLE,'报名截止改为9月26日')]);s=TaskStore(path)
   self.assertEqual(s.task(SAMPLE)['deadline'],'2026-09-24')
   self.assertTrue(s.due()) # Dates are informational; do not silently discard expired tasks.
   s.save_dates(SAMPLE,'','','');self.assertEqual(s.task(SAMPLE)['deadline'],'')
 def test_v04_schema_migrates_without_losing_notice_or_state(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'db';s=TaskStore(path);s.add_many([SAMPLE],notices=[(SAMPLE,'原文')]);s.disposition([SAMPLE],'ignored')
   with s.connection() as db:
    for name in ('event_start','event_end','deadline'):db.execute('ALTER TABLE tasks DROP COLUMN '+name)
   s=TaskStore(path);self.assertEqual(s.task(SAMPLE)['deadline'],'');self.assertEqual(s.task(SAMPLE)['disposition'],'ignored');self.assertEqual(s.notices(SAMPLE)[0]['body'],'原文')
 def test_search_notice_and_source_combined_literal_chars(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=TaskStore(Path(tmp)/'db');s.add_many([SAMPLE],'研究生群',[(SAMPLE,'中秋设计 奖励100% AI_作品')]);s.add_many([OTHER],'本科群',[(OTHER,'中秋设计')])
   self.assertEqual(s.matching_urls('中秋 研究生'),{SAMPLE})
   self.assertEqual(s.matching_urls('%'),{SAMPLE})
   self.assertEqual(s.matching_urls('ai_作品'),{SAMPLE})
   self.assertEqual(s.matching_urls("' OR 1=1"),set())
   self.assertEqual(s.matching_urls('中秋'),{SAMPLE,OTHER})
   self.assertIsNone(s.matching_urls('  '))

class GUI(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw();self.a=App(self.root,self.tmp.name);self.a.processing.clear()
 def tearDown(self):self.root.update_idletasks();self.root.destroy();self.tmp.cleanup()
 def test_search_filters_details_and_monitor_are_independent(self):
  a=self.a;a.store.add_many([SAMPLE],'研究生群',[(SAMPLE,'中秋创意设计')]);a.store.add_many([OTHER],'其他群',[(OTHER,'体育活动')])
  a.store.save_dates(SAMPLE,'2026-09-25 15:00','','2026-09-24');a.search_var.set('中秋');settle_view(a)
  self.assertEqual(a.table.get_children(),(SAMPLE,))
  a.table.selection_set(SAMPLE);refresh_detail(a);settle_view(a);self.assertIn('2026-09-25 15:00',a.detail_tabs['活动时间'].get('1.0','end'))
  a.monitor_thread=Mock();a.monitor_thread.is_alive.return_value=True;a.monitor_stop=threading.Event()
  a.emit_monitor('读取异常：窗口不可读');a.emit('活动已暂缓');a.tick()
  self.assertIn('窗口不可读',a.monitor_summary.get());self.assertEqual(a.status.get(),'活动已暂缓')
  a.emit_monitor('正常读取',True);a.tick();self.assertIsNotNone(a.monitor_last_success);self.assertIn('正常读取',a.monitor_summary.get());self.assertEqual(a.status.get(),'活动已暂缓')
  a.source_var.set('其他群');settle_view(a);self.assertEqual(a.table.get_children(),())
  a.source_var.set('全部来源');a.search_var.set('');a.time_filter_var.set('未设截止');settle_view(a);self.assertEqual(a.table.get_children(),(OTHER,))
 def test_time_editor_saves_correct_task(self):
  from tkinter import ttk
  a=self.a;a.store.add_many([SAMPLE,OTHER]);settle_view(a);a.table.selection_set(SAMPLE);edit_dates(a)
  def descendants(w):
   for child in w.winfo_children():
    yield child
    yield from descendants(child)
  entries=[w for w in descendants(a.dates_window) if isinstance(w,ttk.Entry)]
  entries[0].insert(0,'2026-09-25 15:00');entries[2].insert(0,'2026-09-24')
  a.table.selection_set(OTHER)
  button=next(w for w in descendants(a.dates_window) if isinstance(w,ttk.Button) and w['text']=='保存时间');button.invoke()
  self.assertEqual(a.store.task(SAMPLE)['deadline'],'2026-09-24');self.assertEqual(a.store.task(OTHER)['deadline'],'')

if __name__=='__main__':unittest.main()
