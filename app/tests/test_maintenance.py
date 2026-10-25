import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import SAMPLE
from maintenance import run_maintenance, MaintenanceResult
from settings import validate_settings
from task_store import TaskStore
from ui_wait import settle_view


class MaintenanceTransactions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='维护测试🌙-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=TaskStore(self.root/'tasks.sqlite3')
        self.config=validate_settings({'backup_enabled':False})
        self.now=datetime(2026,9,28,12,0)
        self.store.add_many([SAMPLE])
        self.store.save_dates(SAMPLE,'','','2026-09-28 12:20')

    def run_batch(self,**kwargs):
        return run_maintenance(self.store,self.root,self.config,now=self.now,**kwargs)

    def snooze_in_past(self):
        with self.store.connection() as db:
            db.execute("UPDATE tasks SET disposition='snoozed',snooze_until=?",(self.now.timestamp()-5,))

    def test_wake_and_deadline_commit_together_and_restart_does_not_repeat(self):
        self.snooze_in_past();first=self.run_batch()
        self.assertEqual(first.woke,1);self.assertTrue(first.due)
        self.assertEqual(len(first.alerts),1);self.assertEqual(self.store.task(SAMPLE)['disposition'],'active')
        second=run_maintenance(TaskStore(self.store.path),self.root,self.config,now=self.now)
        self.assertEqual(second.alerts,[]);self.assertEqual(self.store.unread_alert_count(),1)

    def test_failure_after_alert_generation_rolls_back_entire_batch(self):
        self.snooze_in_past()
        with patch('maintenance.has_due_tasks',side_effect=sqlite3.OperationalError('synthetic')):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_batch(system_alerts=[('system','提醒','消息')])
        self.assertEqual(self.store.task(SAMPLE)['disposition'],'snoozed')
        self.assertEqual(self.store.alerts(),[])
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reminder_keys').fetchone()[0],0)
        result=self.run_batch(system_alerts=[('system','提醒','消息')])
        self.assertEqual(len(result.alerts),2)

    def test_system_alerts_deduplicate_and_reminder_scan_can_be_skipped(self):
        items=[('test','后台提醒','不含资料')]*2
        first=self.run_batch(system_alerts=items,check_reminders=False)
        self.assertEqual(first.alerts,[('后台提醒','不含资料')])
        self.assertEqual(self.run_batch(system_alerts=items,check_reminders=False).alerts,[])
        self.assertEqual(len(self.run_batch().alerts),1)

    def test_ignored_done_and_future_snoozed_are_not_reminded(self):
        for mode in ('ignored','done','snoozed'):
            with self.store.connection() as db:
                db.execute('UPDATE tasks SET state=?,disposition=?,snooze_until=?',
                           ('done' if mode=='done' else 'pending',mode if mode!='done' else 'active',self.now.timestamp()+3600))
            self.assertEqual(self.run_batch().alerts,[])

    def test_clock_forward_then_backward_does_not_duplicate(self):
        self.assertEqual(len(self.run_batch().alerts),1)
        result=run_maintenance(self.store,self.root,self.config,now=self.now-timedelta(minutes=5))
        self.assertEqual(result.alerts,[])
        result=run_maintenance(self.store,self.root,self.config,now=self.now+timedelta(hours=1))
        self.assertEqual(result.alerts,[])

    def test_backup_directory_failure_preserves_committed_alerts(self):
        self.config['backup_enabled']=True
        with patch('maintenance.Path.glob',side_effect=PermissionError('private path')):
            result=self.run_batch(check_backup=True)
        self.assertIsInstance(result.backup_error,PermissionError)
        self.assertEqual(len(result.alerts),1);self.assertEqual(self.store.unread_alert_count(),1)
        self.assertEqual(self.run_batch().alerts,[])

    def test_recent_auto_backup_and_disabled_backup_do_not_request_another(self):
        self.assertFalse(self.run_batch(check_backup=True).backup_due)
        self.config['backup_enabled']=True
        self.assertTrue(self.run_batch(check_backup=True).backup_due)
        folder=self.root/'backups';folder.mkdir();(folder/'auto-test.zip').write_bytes(b'test')
        self.assertFalse(self.run_batch(check_backup=True).backup_due)

    def test_exclusive_lock_failure_changes_nothing_and_retry_succeeds(self):
        db=sqlite3.connect(self.store.path)
        try:
            db.execute('BEGIN EXCLUSIVE')
            with self.assertRaises(sqlite3.OperationalError):self.run_batch()
        finally:db.rollback();db.close()
        self.assertEqual(len(self.run_batch().alerts),1)


class MaintenanceUI(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from app import App
        self.temp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,self.temp.name)
        self.app.config.update(backup_enabled=False,notify_popup=False,notify_sound=False)
        self.root.after_cancel(self.app.tick_timer)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.update_idletasks();self.root.destroy();self.temp.cleanup()

    def poll_maintenance(self):
        self.app.maintenance.thread.join(3)
        self.assertFalse(self.app.maintenance.thread.is_alive())
        self.app.maintenance.poll()

    def test_slow_search_does_not_suppress_deadline_reminder(self):
        app=self.app;gate=threading.Event()
        app.store.add_many([SAMPLE]);app.store.save_dates(SAMPLE,'','',(datetime.now()+timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M'))
        reader=app.query.reader
        app.query.reader=lambda req,cancel:(gate.wait(3),reader(req,cancel))[1]
        try:
            app.refresh_table();self.assertTrue(app.query.busy)
            app.schedule_maintenance();self.poll_maintenance()
            self.assertTrue(app.query.busy);self.assertEqual(app.store.unread_alert_count(),1)
        finally:gate.set();app.query.jobs.thread.join(3)

    def test_maintenance_runs_off_ui_and_does_not_block_heartbeat(self):
        app=self.app;gate=threading.Event();started=threading.Event();threads=[];beats=[]
        def slow(*args,**kwargs):
            threads.append(threading.get_ident());started.set();gate.wait(3);return MaintenanceResult([],False,0)
        try:
            with patch('app.run_maintenance',side_effect=slow):
                app.schedule_maintenance();self.assertTrue(started.wait(1))
                for _ in range(20):app.schedule_maintenance()
                self.root.after(20,lambda:beats.append(True));self.root.after(100,self.root.quit);self.root.mainloop()
                self.assertEqual(beats,[True]);self.assertEqual(len(threads),1)
                self.assertNotEqual(threads[0],threading.get_ident())
                gate.set();self.poll_maintenance()
        finally:gate.set();app.maintenance.thread.join(3)

    def test_file_job_is_queued_behind_maintenance_and_prevents_new_batch(self):
        app=self.app;gate=threading.Event();order=[];file_started=threading.Event()
        def slow(*args,**kwargs):
            gate.wait(3);order.append('maintenance');return MaintenanceResult([],False,0)
        def file_work():order.append('file');file_started.set();return 1
        try:
            with patch('app.run_maintenance',side_effect=slow) as work:
                app.schedule_maintenance()
                self.assertTrue(app.run_file_job('测试文件',file_work,lambda n:None))
                self.assertFalse(file_started.wait(.05))
                app.schedule_maintenance();self.assertEqual(work.call_count,1)
                gate.set();app.jobs.thread.join(3);self.poll_maintenance();app.jobs.poll()
            self.assertEqual(order,['maintenance','file'])
        finally:
            gate.set();app.maintenance.thread.join(3)
            if app.jobs.thread:app.jobs.thread.join(3)

    def test_manual_backup_waits_for_maintenance(self):
        app=self.app;gate=threading.Event();order=[]
        def slow(*args,**kwargs):gate.wait(3);order.append('maintenance');return MaintenanceResult([],False,0)
        def backup(*args,**kwargs):order.append('backup');return Path('synthetic.zip')
        try:
            with patch('app.run_maintenance',side_effect=slow),patch('app.make_backup',side_effect=backup):
                app.schedule_maintenance();app.start_backup()
                time.sleep(.03);self.assertEqual(order,[])
                gate.set();app.backup_thread.join(3);self.poll_maintenance()
            self.assertEqual(order,['maintenance','backup'])
        finally:gate.set();app.maintenance.thread.join(3);app.backup_thread.join(3)

    def test_failure_retains_queued_alert_and_retry_stays_paused(self):
        app=self.app;app.deliver_alert('queued','测试提醒','测试正文');app.processing.set()
        with patch('app.run_maintenance',side_effect=PermissionError('private path')):
            app.schedule_maintenance();self.poll_maintenance()
        self.assertIn('queued',app.pending_alerts);self.assertFalse(app.processing.is_set())
        self.assertNotIn('private',app.maintenance_activity.get())
        self.assertIn('后台检查失败',app.maintenance_activity.get())
        with patch('app.run_maintenance') as work:
            app.schedule_maintenance();work.assert_not_called()
        app.retry_maintenance();self.poll_maintenance()
        self.assertEqual(app.pending_alerts,{});self.assertFalse(app.processing.is_set())
        self.assertEqual(app.store.unread_alert_count(),1);self.assertIn('已恢复',app.status.get())

    def test_paused_state_is_rechecked_before_browser_launch(self):
        app=self.app;app.store.add_many([SAMPLE]);app.processing.set()
        with patch.object(app,'browser') as browser:
            app.schedule_maintenance();app.maintenance.thread.join(3)
            app.processing.clear();app.maintenance.poll();browser.assert_not_called()

    def test_recovery_message_respects_explicit_user_resume(self):
        app=self.app;app.maintenance_failed=True;app.processing.set()
        app.schedule_maintenance();self.poll_maintenance()
        self.assertTrue(app.processing.is_set());self.assertIn('已继续',app.status.get())

    def test_system_alert_enqueue_does_not_touch_database_on_ui_thread(self):
        app=self.app
        with patch.object(app.store,'connection',side_effect=AssertionError('UI DB call')):
            app.deliver_alert('id','标题','正文')
        self.assertIn('id',app.pending_alerts)

    def test_timer_acknowledges_alert_queued_during_previous_batch(self):
        app=self.app;gate=threading.Event();started=threading.Event()
        def slow(*args,**kwargs):
            started.set();gate.wait(3);return run_maintenance(*args,**kwargs)
        try:
            with patch('app.run_maintenance',side_effect=slow):
                app.schedule_maintenance();self.assertTrue(started.wait(1))
                app.deliver_alert('late','延后入队的提醒','只保存一次')
                app.tick();gate.set()
                until=time.monotonic()+5
                while 'late' in app.pending_alerts and time.monotonic()<until:
                    self.root.update();time.sleep(.01)
                self.assertNotIn('late',app.pending_alerts)
                self.assertEqual(app.store.unread_alert_count(),1)
        finally:gate.set();app.maintenance.thread.join(3)

    def test_no_maintenance_starts_while_file_job_busy(self):
        app=self.app;gate=threading.Event()
        try:
            app.run_file_job('测试等待',lambda:gate.wait(3),lambda _:None)
            with patch('app.run_maintenance') as work:
                app.schedule_maintenance();work.assert_not_called()
        finally:gate.set();app.jobs.thread.join(3);app.jobs.poll()

    def test_shutdown_keeps_committed_alert_but_discards_popup_callback(self):
        app=self.app;gate=threading.Event()
        app.deliver_alert('shutdown','退出期间提醒','测试')
        def slow(*args,**kwargs):gate.wait(3);return run_maintenance(*args,**kwargs)
        try:
            with patch('app.run_maintenance',side_effect=slow),patch('app.alert_popup') as popup:
                app.schedule_maintenance();app.maintenance.close();gate.set();self.poll_maintenance()
                popup.assert_not_called()
            self.assertEqual(app.store.unread_alert_count(),1)
        finally:gate.set();app.maintenance.thread.join(3)


if __name__=='__main__':unittest.main()
