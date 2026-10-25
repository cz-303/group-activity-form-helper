import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from background_jobs import BackgroundJobs
from file_operations import prepare_restore, prepare_old_folder, install_pending, export_tasks
from backup_support import make_backup, inspect_backup
from task_store import TaskStore
from core import SAMPLE
from settings import validate_settings
from about_ui import DOCUMENTS, read_document, show_about


class Jobs(unittest.TestCase):
    def test_callbacks_run_on_owner_and_duplicates_refused(self):
        runner=BackgroundJobs();gate=threading.Event();seen=[];owner=threading.get_ident()
        def work():gate.wait(2);return threading.get_ident()
        self.assertTrue(runner.start(work,lambda value:seen.append((value,threading.get_ident())),self.fail))
        try:self.assertFalse(runner.start(lambda:0,self.fail,self.fail))
        finally:gate.set();runner.thread.join(3)
        self.assertTrue(runner.busy);self.assertEqual(seen,[])
        runner.poll();self.assertNotEqual(seen[0][0],owner);self.assertEqual(seen[0][1],owner)
        self.assertFalse(runner.busy)

    def test_worker_failure_releases_slot_and_delivers_exception(self):
        runner=BackgroundJobs();seen=[]
        def fail():raise PermissionError('private path')
        runner.start(fail,self.fail,seen.append);runner.thread.join(3);runner.poll()
        self.assertIsInstance(seen[0],PermissionError);self.assertFalse(runner.busy)

    def test_close_discards_completion_but_cleans_owned_result(self):
        runner=BackgroundJobs();gate=threading.Event();cleaned=[]
        runner.start(lambda:gate.wait(2) or 'result',self.fail,self.fail,cleaned.append)
        runner.close();gate.set();runner.thread.join(3);runner.poll()
        self.assertEqual(len(cleaned),1);self.assertFalse(runner.start(lambda:1,self.fail,self.fail))

    def test_callback_exception_still_cleans_result(self):
        runner=BackgroundJobs();cleaned=[]
        def broken(value):raise ValueError('UI callback')
        runner.start(lambda:'snapshot',broken,self.fail,cleaned.append);runner.thread.join(3)
        with self.assertRaises(ValueError):runner.poll()
        self.assertEqual(cleaned,['snapshot']);self.assertFalse(runner.busy)

    def test_thread_start_failure_does_not_leave_busy(self):
        runner=BackgroundJobs()
        with patch('background_jobs.threading.Thread.start',side_effect=RuntimeError('no thread')):
            with self.assertRaises(RuntimeError):runner.start(lambda:1,self.fail,self.fail)
        self.assertFalse(runner.busy)


class FileWorkflows(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='中文迁移-');self.root=Path(self.tmp.name)
        self.store=TaskStore(self.root/'tasks.sqlite3');self.store.add_many([SAMPLE])
        self.archive=make_backup(self.store,self.root,validate_settings({}),'manual')
    def tearDown(self):self.tmp.cleanup()

    def test_confirmed_snapshot_is_independent_of_source_changes(self):
        prepared=prepare_restore(self.archive);folder=Path(prepared.directory.name)
        self.archive.write_bytes(b'source changed after preview')
        install_pending(prepared,self.root)
        self.assertEqual(inspect_backup(self.root/'restore_pending.zip',validate_settings)[3],1)
        self.assertFalse(folder.exists())

    def test_rejected_snapshot_cleanup(self):
        prepared=prepare_restore(self.archive);folder=Path(prepared.directory.name)
        prepared.discard_unless_retained();self.assertFalse(folder.exists())
        self.assertFalse((self.root/'restore_pending.zip').exists())

    def test_failed_staging_cleans_snapshot_preserves_existing_pending(self):
        pending=self.root/'restore_pending.zip';pending.write_bytes(b'previous')
        prepared=prepare_restore(self.archive);folder=Path(prepared.directory.name)
        with patch('file_operations.stage_restore',side_effect=PermissionError('in use')):
            with self.assertRaises(PermissionError):install_pending(prepared,self.root)
        self.assertFalse(folder.exists());self.assertEqual(pending.read_bytes(),b'previous')

    def test_old_folder_not_modified(self):
        before=hashlib.sha256((self.root/'tasks.sqlite3').read_bytes()).hexdigest()
        prepared=prepare_old_folder(self.root)
        try:self.assertEqual(prepared.count,1)
        finally:prepared.close()
        self.assertEqual(hashlib.sha256((self.root/'tasks.sqlite3').read_bytes()).hexdigest(),before)

    def test_export_includes_all_rows_and_escapes_formulas(self):
        with self.store.connection() as db:db.execute('UPDATE tasks SET title=?',(' =1+1',))
        target=self.root/'中文名单.csv';self.assertEqual(export_tasks(self.store.path,target),1)
        with target.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.reader(stream))
        self.assertEqual(len(rows),2);self.assertEqual(rows[1][0],"' =1+1")

    def test_export_failure_does_not_replace_existing_file(self):
        target=self.root/'output.csv';target.write_bytes(b'original')
        with patch('storage_io.os.replace',side_effect=PermissionError('occupied')):
            with self.assertRaises(PermissionError):export_tasks(self.store.path,target)
        self.assertEqual(target.read_bytes(),b'original')

    def test_export_refuses_live_database_and_restore_files(self):
        from file_operations import export_migration
        database=self.root/'tasks.sqlite3';before=database.read_bytes()
        with self.assertRaises(ValueError):export_tasks(database,database)
        with self.assertRaises(ValueError):export_migration(self.store,self.root,validate_settings({}),self.root/'restore_pending.zip')
        self.assertEqual(database.read_bytes(),before)

    def test_backup_does_not_take_shared_store_lock(self):
        with patch.object(self.store,'connection',side_effect=AssertionError('shared lock')),patch.object(self.store,'rows',side_effect=AssertionError('live count')):
            archive=make_backup(self.store,self.root,validate_settings({}),'manual')
        manifest,_,_,count=inspect_backup(archive,validate_settings)
        self.assertEqual(manifest['tasks'],count)

    def test_failed_backup_destination_closes_source_connection(self):
        import sqlite3
        real_connect=sqlite3.connect;opened=[]
        def connect(*args,**kwargs):
            if opened:raise sqlite3.OperationalError('disk full')
            connection=real_connect(*args,**kwargs);opened.append(connection);return connection
        with patch('backup_support.sqlite3.connect',side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):make_backup(self.store,self.root,validate_settings({}),'manual')
        with self.assertRaises(sqlite3.ProgrammingError):opened[0].execute('SELECT 1')


class NoticesAndUI(unittest.TestCase):
    def test_restore_confirmation_hands_snapshot_to_worker_once(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder)
            try:
                app.store.add_many([SAMPLE])
                archive=make_backup(app.store,app.data,app.get_config(),'manual')
                app.run_file_job('检查',lambda:prepare_restore(archive),app.confirm_restore,
                                 lambda result:result.discard_unless_retained())
                app.jobs.thread.join(3)
                with patch('app.messagebox.askyesno',return_value=True):app.jobs.poll()
                self.assertTrue(app.jobs.busy)
                app.jobs.thread.join(3);app.jobs.poll()
                self.assertFalse(app.jobs.busy)
                self.assertEqual(inspect_backup(Path(folder)/'restore_pending.zip',validate_settings)[3],1)
            finally:
                if app.jobs.thread:app.jobs.thread.join(3)
                root.update_idletasks();root.destroy()

    def test_cancel_restore_does_not_stage_or_change_task_state(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder)
            try:
                app.store.add_many([SAMPLE]);archive=make_backup(app.store,app.data,app.get_config(),'manual')
                prepared=prepare_restore(archive);snapshot_folder=Path(prepared.directory.name)
                app.run_file_job('检查',lambda:prepared,app.confirm_restore,lambda result:result.discard_unless_retained())
                app.jobs.thread.join(3)
                with patch('app.messagebox.askyesno',return_value=False):app.jobs.poll()
                self.assertFalse(snapshot_folder.exists());self.assertFalse((Path(folder)/'restore_pending.zip').exists())
                self.assertEqual(app.store.state(SAMPLE),'pending')
            finally:root.update_idletasks();root.destroy()

    def test_legal_documents_include_contact_and_no_blanket_waiver(self):
        text=read_document('软件声明')
        for phrase in ('github.com/cz-303/group-activity-form-helper/issues','非','LGPL','不免除依法应承担的责任'):self.assertIn(phrase,text)
        self.assertIn('提交前',read_document('隐私说明'))
        with self.assertRaises(ValueError):read_document('../config.json')

    def test_missing_bundled_notice_has_readable_error(self):
        with tempfile.TemporaryDirectory() as folder,patch('about_ui.resource_root',return_value=Path(folder)):
            self.assertIn('无法读取',read_document('软件声明'))

    def test_about_reuses_window_and_background_keeps_ui_responsive(self):
        import tkinter as tk
        from tkinter import ttk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder);gate=threading.Event();seen=[]
            try:
                show_about(app);window=app.about_window;show_about(app);self.assertIs(window,app.about_window)
                nb=next(w for w in window.winfo_children() if isinstance(w,ttk.Notebook));self.assertEqual(len(nb.tabs()),len(DOCUMENTS)+1)
                self.assertEqual(nb.tab(nb.tabs()[0],'text'),'功能范围')
                app.run_file_job('测试后台操作',lambda:gate.wait(3),lambda _:seen.append('complete'))
                root.after(10,lambda:seen.append('heartbeat'));root.after(80,root.quit);root.mainloop()
                self.assertIn('heartbeat',seen);self.assertNotIn('complete',seen)
                gate.set();app.jobs.thread.join(3);app.jobs.poll();self.assertIn('complete',seen)
            finally:
                gate.set()
                if app.jobs.thread:app.jobs.thread.join(3)
                root.update_idletasks();root.destroy()


if __name__=='__main__':unittest.main()
