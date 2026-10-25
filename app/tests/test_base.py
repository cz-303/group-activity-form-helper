from ui_wait import settle_view
import sys,unittest,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_store import TaskStore,state_label
from core import SAMPLE
from app import App,validate_settings,SETTINGS
import tkinter as tk

def url(i):return 'https://docs.qq.com/form/page/Test'+str(i)

class Tasks(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'tasks.sqlite3';self.s=TaskStore(self.path)
 def tearDown(self):self.tmp.cleanup()
 def test_more_than_old_limit_persists(self):
  self.assertEqual(self.s.add_many([url(i) for i in range(50)]),50)
  for i in range(8):
   item=self.s.claim();self.s.opened(item['url'])
  # No claim is made while full; all 42 tasks remain durable.
  self.assertEqual(self.s.counts(),{'opened':8,'pending':42})
  restored=TaskStore(self.path);restored.recover_inflight()
  self.assertEqual(restored.counts(),{'opened':8,'pending':42})
 def test_restart_recovers_only_inflight(self):
  self.s.add_many([url(i) for i in range(4)])
  a=self.s.claim();self.s.opened(a['url']);b=self.s.claim();self.s.opened(b['url']);self.s.done([b['url']])
  self.s.claim()
  restored=TaskStore(self.path);restored.recover_inflight()
  self.assertEqual(restored.counts(),{'done':1,'opened':1,'pending':2})
 def test_duplicate_does_not_reset_done(self):
  self.s.add_many([SAMPLE]);self.s.done([SAMPLE])
  self.assertEqual(self.s.add_many([SAMPLE+'?x=1']),0)
  self.assertEqual(self.s.state(SAMPLE),'done')
 def test_retry_backoff_and_exhaustion(self):
  self.s.add_many([SAMPLE]);self.s.claim(now=100)
  self.s.failed(SAMPLE,'network',3,10,now=100)
  self.assertFalse(self.s.due(now=109));self.assertTrue(self.s.due(now=110))
  self.s.claim(now=110);self.s.failed(SAMPLE,'network',3,10,now=110)
  self.assertFalse(self.s.due(now=129));self.assertTrue(self.s.due(now=130))
  self.s.claim(now=130);self.s.failed(SAMPLE,'network',3,10,now=130)
  self.assertFalse(self.s.due(now=9999))
  self.assertEqual(state_label(self.s.rows()[0]),'需要手动重试')
  self.s.requeue([SAMPLE]);self.assertTrue(self.s.due());self.assertEqual(self.s.rows()[0]['attempts'],0)
 def test_failed_state_survives_restart(self):
  self.s.add_many([SAMPLE]);self.s.claim();self.s.failed(SAMPLE,'offline',max_attempts=1)
  restored=TaskStore(self.path);restored.recover_inflight()
  self.assertEqual(restored.state(SAMPLE),'failed');self.assertFalse(restored.due())
 def test_inflight_not_manually_overwritten(self):
  self.s.add_many([SAMPLE]);self.s.claim();self.s.done([SAMPLE]);self.s.requeue([SAMPLE])
  self.assertEqual(self.s.state(SAMPLE),'opening')
  self.s.release(SAMPLE);self.assertEqual(self.s.state(SAMPLE),'pending')
 def test_atomic_batch_rollback(self):
  with self.assertRaises(ValueError):self.s.add_many([SAMPLE,'https://example.com'])
  self.assertEqual(self.s.rows(),[])
 def test_concurrent_capture_unique(self):
  threads=[threading.Thread(target=lambda:self.s.add_many([url(i) for i in range(30)])) for _ in range(4)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(len(self.s.rows()),30)
 def test_concurrent_claim_unique(self):
  self.s.add_many([url(i) for i in range(20)]);claimed=[]
  def worker():
   while True:
    item=self.s.claim()
    if not item:break
    claimed.append(item['url'])
  threads=[threading.Thread(target=worker) for _ in range(3)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(len(claimed),20);self.assertEqual(len(set(claimed)),20)

class Settings(unittest.TestCase):
 def test_validation(self):
  self.assertEqual(validate_settings(SETTINGS)['max_tabs'],8)
  for values in [dict(SETTINGS,max_tabs=0),dict(SETTINGS,retry_seconds='bad'),dict(SETTINGS,keywords=' , '),dict(SETTINGS,group=None)]:
   with self.assertRaises(ValueError):validate_settings(values)
 def test_gui_settings_and_queue(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=tk.Tk();root.withdraw()
   try:
    app=App(root,tmp);app.processing.clear();app.store.add_many([SAMPLE]);settle_view(app)
    self.assertEqual(len(app.table.get_children()),1)
    app.table.selection_set(SAMPLE);app.done_selected();self.assertEqual(app.store.state(SAMPLE),'done')
    app.requeue_selected();self.assertEqual(app.store.state(SAMPLE),'pending')
    app.open_settings();root.update_idletasks()
    from tkinter import ttk
    nb=next(w for w in app.settings_window.winfo_children() if isinstance(w,ttk.Notebook))
    self.assertEqual(len(nb.tabs()),6)
    app.settings_window.destroy()
   finally:root.update_idletasks();root.update_idletasks();root.destroy()

if __name__=='__main__':unittest.main()
