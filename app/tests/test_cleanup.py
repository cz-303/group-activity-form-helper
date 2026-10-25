import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from dataclasses import replace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_cleanup import CleanupOptions,preview_cleanup,execute_cleanup,file_signature
from task_store import TaskStore
from core import SAMPLE
from settings import validate_settings
from backup_support import inspect_backup


class Cleanup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='中文清理-');self.data=Path(self.temp.name)
        self.store=TaskStore(self.data/'tasks.sqlite3');self.config=validate_settings({})
        self.now=time.time();self.old=self.now-100*86400
        self.store.add_many([SAMPLE],notices=[(SAMPLE,'需要保留在安全备份的原通知')]);self.store.done([SAMPLE])
        with self.store.connection() as db:db.execute('UPDATE tasks SET updated=?',(self.old,))
    def tearDown(self):self.temp.cleanup()
    def preview(self,**options):return preview_cleanup(self.store,self.data,CleanupOptions(**options),self.now)
    def run_plan(self,plan):return execute_cleanup(self.store,self.data,self.config,plan)

    def test_default_selects_nothing_and_no_backup(self):
        plan=self.preview();self.assertEqual(plan.task_urls,());result=self.run_plan(plan)
        self.assertEqual(result['tasks'],0);self.assertTrue(self.store.task(SAMPLE));self.assertFalse(result['safety'])

    def test_selected_tasks_and_notices_deleted_with_restorable_backup(self):
        plan=self.preview(tasks=True);result=self.run_plan(plan)
        self.assertEqual(result['tasks'],1);self.assertEqual(result['notices'],1)
        self.assertIsNone(self.store.task(SAMPLE));self.assertEqual(self.store.notices(SAMPLE),[])
        self.assertEqual(inspect_backup(result['safety'],validate_settings)[3],1)
        # Documented: a future capture may create the deleted task again.
        self.assertEqual(self.store.add_many([SAMPLE]),1)

    def test_active_snoozed_opening_and_recent_tasks_preserved(self):
        for index in range(4):self.store.add_many([SAMPLE+str(index)])
        with self.store.connection() as db:
            db.execute('UPDATE tasks SET updated=?',(self.old,))
            db.execute("UPDATE tasks SET disposition='snoozed',state='done' WHERE url=?",(SAMPLE+'1',))
            db.execute("UPDATE tasks SET disposition='ignored',state='opening' WHERE url=?",(SAMPLE+'2',))
            db.execute("UPDATE tasks SET state='done',updated=? WHERE url=?",(self.now,SAMPLE+'3'))
        plan=self.preview(tasks=True,safety_backup=False)
        self.assertEqual(plan.task_urls,(SAMPLE,));self.run_plan(plan)
        self.assertEqual(len(self.store.rows()),4)

    def test_stale_preview_refuses_before_any_delete(self):
        plan=self.preview(tasks=True)
        self.store.requeue([SAMPLE])
        with self.assertRaises(ValueError):self.run_plan(plan)
        self.assertEqual(self.store.state(SAMPLE),'pending');self.assertTrue(self.store.notices(SAMPLE))

    def test_changed_notice_without_task_timestamp_refuses(self):
        plan=self.preview(tasks=True)
        with self.store.connection() as db:db.execute('UPDATE notices SET body=?',('新正文',))
        with self.assertRaises(ValueError):self.run_plan(plan)
        self.assertTrue(self.store.task(SAMPLE))

    def test_backup_failure_aborts_task_deletion(self):
        plan=self.preview(tasks=True)
        with patch('data_cleanup.make_backup',side_effect=PermissionError('disk full')):
            with self.assertRaises(PermissionError):self.run_plan(plan)
        self.assertTrue(self.store.task(SAMPLE));self.assertTrue(self.store.notices(SAMPLE))

    def test_transaction_failure_rolls_back_notices(self):
        with self.store.connection() as db:db.execute("CREATE TRIGGER blocked BEFORE DELETE ON tasks BEGIN SELECT RAISE(ABORT,'blocked'); END")
        plan=self.preview(tasks=True,safety_backup=False)
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):self.run_plan(plan)
        self.assertTrue(self.store.task(SAMPLE));self.assertTrue(self.store.notices(SAMPLE))

    def test_only_old_read_alerts_deleted(self):
        with self.store.connection() as db:
            db.executemany('INSERT INTO alerts VALUES(?,?,?,?,?)',[('old-read','a','b',self.old,1),('unread','a','b',self.old,0),('recent','a','b',self.now,1)])
        plan=self.preview(alerts=True,safety_backup=False);self.assertEqual(plan.alert_ids,('old-read',));self.run_plan(plan)
        self.assertEqual({r['id'] for r in self.store.alerts()},{'unread','recent'})

    def files(self):
        folder=self.data/'backups';folder.mkdir()
        for i in range(5):
            p=folder/f'auto-20260101-00000{i}-000001.zip';p.write_bytes(b'old-auto');os.utime(p,(self.old+i,self.old+i))
        for name in ('manual-20260101.zip','before-restore-test.zip','before-cleanup-test.zip','auto-foreign.zip'):
            p=folder/name;p.write_bytes(b'keep');os.utime(p,(self.old,self.old))
        for name in ('diagnostics.log','diagnostics.log.1','diagnostics.log.2','config.json'):
            p=self.data/name;p.write_bytes(b'keep-or-clean');os.utime(p,(self.old,self.old))

    def test_auto_backup_retention_and_current_log_protected(self):
        self.files();plan=self.preview(backups=True,logs=True);self.assertEqual(len(plan.files),5)
        result=self.run_plan(plan);self.assertEqual(len(result['files']),5)
        self.assertEqual(len(list((self.data/'backups').glob('auto-2026*.zip'))),2)
        self.assertTrue((self.data/'diagnostics.log').exists());self.assertTrue((self.data/'config.json').exists())
        self.assertTrue((self.data/'backups'/'before-cleanup-test.zip').exists())

    def test_changed_file_refuses_whole_preview(self):
        self.files();plan=self.preview(backups=True)
        (self.data/plan.files[0][0]).write_bytes(b'changed')
        with self.assertRaises(ValueError):self.run_plan(plan)
        self.assertEqual(len(list((self.data/'backups').glob('auto-2026*.zip'))),5)

    def test_locked_file_is_reported_as_partial_failure(self):
        self.files();plan=self.preview(logs=True);original=Path.unlink
        def unlink(path,*args,**kwargs):
            if path.name=='diagnostics.log.1':raise PermissionError('in use')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):result=self.run_plan(plan)
        self.assertEqual(result['failed'],['diagnostics.log.1']);self.assertEqual(result['files'],['diagnostics.log.2'])

    def test_forged_plan_cannot_delete_config(self):
        self.files();plan=self.preview()
        # tempfile may return an 8.3 path on Windows; production cleanup uses
        # the resolved root. Reach the forged-plan check with the same root.
        forged=replace(plan,files=(file_signature(self.data.resolve(),'config.json'),))
        with self.assertRaises(ValueError):self.run_plan(forged)
        self.assertTrue((self.data/'config.json').exists())

    def test_pending_restore_prevents_cleanup(self):
        plan=self.preview(tasks=True);(self.data/'restore_pending.zip').write_bytes(b'pending')
        with self.assertRaises(ValueError):self.run_plan(plan)
        self.assertTrue(self.store.task(SAMPLE))

    def test_junction_or_symlink_data_directory_refused(self):
        with patch.object(Path,'is_junction',return_value=True):
            with self.assertRaises(ValueError):self.preview()

    def test_other_directory_plan_refused(self):
        plan=replace(self.preview(tasks=True),root=str(self.data.parent))
        with self.assertRaises(ValueError):self.run_plan(plan)

    def test_options_validated(self):
        for options in (CleanupOptions(days=True),CleanupOptions(days=0),CleanupOptions(tasks='yes')):
            with self.assertRaises(ValueError):preview_cleanup(self.store,self.data,options)


class CleanupUI(unittest.TestCase):
    def test_defaults_no_delete_preview_cancel_and_window_reuse(self):
        import tkinter as tk
        from tkinter import ttk
        from app import App
        from cleanup_ui import show_cleanup
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder)
            try:
                show_cleanup(app);window=app.cleanup_window;show_cleanup(app);self.assertIs(window,app.cleanup_window)
                controls=list(walk(window))
                checks=[w for w in controls if isinstance(w,ttk.Checkbutton)]
                self.assertEqual([bool(w.instate(['selected'])) for w in checks],[False,False,False,False,True])
                confirm=next(w for w in controls if isinstance(w,ttk.Button) and w.cget('text')=='确认清理…')
                self.assertTrue(confirm.instate(['disabled']))
                app.store.add_many([SAMPLE]);app.store.done([SAMPLE])
                with app.store.connection() as db:db.execute('UPDATE tasks SET updated=?',(time.time()-100*86400,))
                checks[0].invoke()
                next(w for w in controls if isinstance(w,ttk.Button) and w.cget('text')=='生成预览').invoke()
                app.jobs.thread.join(3);app.jobs.poll();self.assertFalse(confirm.instate(['disabled']))
                with patch('cleanup_ui.messagebox.askyesno',return_value=False):confirm.invoke()
                self.assertTrue(app.store.task(SAMPLE))
            finally:root.update_idletasks();root.destroy()


if __name__=='__main__':unittest.main()
