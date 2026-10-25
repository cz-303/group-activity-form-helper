from ui_wait import settle_view
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_store import TaskStore
from core import SAMPLE
from view_cache import ViewCache
from backup_validation import validate_database


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='中文缓存-')
        self.addCleanup(self.temp.cleanup)
        self.store=TaskStore(Path(self.temp.name)/'tasks.sqlite3')
        self.store.add_many([SAMPLE],notices=[(SAMPLE,'原始通知🌙')])
        self.cache=ViewCache(self.store)

    def test_idle_reads_are_cached_and_search_is_bounded(self):
        with patch.object(self.store,'rows',wraps=self.store.rows) as rows:
            for _ in range(20):self.cache.rows()
            self.assertEqual(rows.call_count,1)
        for n in range(100):self.cache.matching_urls(str(n))
        self.assertEqual(len(self.cache.values),2)

    def test_commits_refresh_but_duplicate_capture_does_not(self):
        self.cache.rows();token=self.store.revision_token()
        self.store.add_many([SAMPLE],notices=[(SAMPLE,'原始通知🌙')])
        self.assertEqual(token,self.store.revision_token())
        self.store.done([SAMPLE])
        self.assertEqual(self.cache.rows()[0]['state'],'done')

    def test_external_connection_changes_invalidate(self):
        self.cache.rows()
        with closing(sqlite3.connect(self.store.path)) as db:
            with db:db.execute("UPDATE tasks SET title='外部修改'")
        self.assertEqual(self.cache.rows()[0]['title'],'外部修改')

    def test_external_wal_write_invalidates_before_checkpoint(self):
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            self.cache.rows()
            with db:db.execute("UPDATE tasks SET title='WAL 修改'")
            self.assertEqual(self.cache.rows()[0]['title'],'WAL 修改')

    def test_rollback_never_caches_uncommitted_change(self):
        self.cache.rows()
        with self.assertRaises(RuntimeError):
            with self.store.connection() as db:
                db.execute("UPDATE tasks SET title='未提交'")
                raise RuntimeError('interrupted')
        self.assertEqual(self.cache.rows()[0]['title'],'')

    def test_notice_search_and_merge_after_update(self):
        self.assertEqual(self.cache.matching_urls('地点更新'),set())
        self.store.add_many([SAMPLE],notices=[(SAMPLE,'地点更新🌙')])
        self.assertEqual(self.cache.matching_urls('地点更新'),{SAMPLE})
        self.assertEqual(len(self.cache.notices(SAMPLE)),2)
        self.assertEqual(self.store.task(SAMPLE)['notice_revision'],1)

    def test_unread_count_is_not_limited_to_300(self):
        with self.store.connection() as db:
            db.executemany('INSERT INTO alerts VALUES(?,?,?,?,?)',[(str(i),'提醒','测试',i,0) for i in range(350)])
        self.assertEqual(self.store.unread_alert_count(),350)
        self.store.read_alerts();self.assertEqual(self.store.unread_alert_count(),0)

    def test_index_preserves_old_and_new_backup_validation(self):
        self.assertEqual(validate_database(self.store.path),1)
        with self.store.connection() as db:db.execute('DROP INDEX notices_by_url')
        self.assertEqual(validate_database(self.store.path),1)

    def test_untrusted_expression_index_still_rejected(self):
        with self.store.connection() as db:
            db.execute('DROP INDEX notices_by_url')
            db.execute('CREATE INDEX notices_by_url ON notices(lower(body))')
        with self.assertRaises(ValueError):validate_database(self.store.path)

    def test_portable_backup_omits_only_runtime_index(self):
        from backup_support import make_backup,inspect_backup
        from settings import validate_settings
        from zipfile import ZipFile
        archive=make_backup(self.store,Path(self.temp.name),validate_settings({}),'manual')
        target=Path(self.temp.name)/'portable.sqlite3'
        with ZipFile(archive) as z:target.write_bytes(z.read('tasks.sqlite3'))
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='notices_by_url'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notices').fetchone()[0],1)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='notices_by_url'").fetchone()[0],1)
        self.assertEqual(inspect_backup(archive,validate_settings)[3],1)

    def test_process_interruption_rolls_back_then_recovers_opening(self):
        self.store.claim()
        script="import sqlite3,sys,os; db=sqlite3.connect(sys.argv[1]); db.execute('BEGIN IMMEDIATE'); db.execute(\"UPDATE tasks SET state='done'\"); os._exit(23)"
        result=subprocess.run([sys.executable,'-B','-c',script,self.store.path],timeout=15)
        self.assertEqual(result.returncode,23)
        recovered=TaskStore(self.store.path)
        self.assertEqual(recovered.state(SAMPLE),'opening')
        recovered.recover_inflight()
        self.assertEqual(recovered.state(SAMPLE),'pending')
        self.assertEqual(recovered.notices(SAMPLE)[0]['body'],'原始通知🌙')


class ShutdownTests(unittest.TestCase):
    def test_backup_and_file_writers_survive_shutdown_deadline(self):
        from app import App
        for kind in ('backup_thread','file_worker','maintenance_worker'):
            with self.subTest(kind=kind):
                callbacks=[];root=Mock();root.winfo_children.return_value=[]
                root.after.side_effect=lambda delay,fn:callbacks.append(fn)
                worker=Mock();worker.is_alive.return_value=True
                app=SimpleNamespace(closing=False,root=root,status=Mock(),tray=None,
                    monitor_stop=None,quit_event=threading.Event(),processing=threading.Event(),
                    monitor_thread=None,browser_thread=None,backup_thread=None,logger=Mock(),jobs=Mock(),query=Mock(),maintenance=Mock())
                app.jobs.thread=None;app.query.jobs.thread=None;app.maintenance.thread=None
                if kind=='backup_thread':app.backup_thread=worker
                elif kind=='maintenance_worker':app.maintenance.thread=worker
                else:app.jobs.thread=worker
                with patch('app.ttk.Label'),patch('app.time.monotonic',return_value=0):App.shutdown(app)
                with patch('app.time.monotonic',return_value=100):callbacks.pop(0)()
                root.destroy.assert_not_called()
                worker.is_alive.return_value=False
                with patch('app.time.monotonic',return_value=101):callbacks.pop(0)()
                root.destroy.assert_called_once()
                App.shutdown(app)
                app.jobs.close.assert_called_once()


class RefreshTests(unittest.TestCase):
    def test_idle_ui_skips_queries_and_diff_but_reacts_to_changes(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder)
            try:
                app.store.add_many([SAMPLE],notices=[(SAMPLE,'第一版')])
                app.store.add_many([SAMPLE],notices=[(SAMPLE,'第二版')])
                # Finish the real notice acknowledgement before measuring an unchanged view.
                app.config['notify_popup']=False
                app.schedule_maintenance();app.maintenance.thread.join(3);app.maintenance.poll()
                settle_view(app);app.table.selection_set(SAMPLE);settle_view(app)
                import task_query
                with patch('task_query.query_matching_urls',wraps=task_query.query_matching_urls) as rows,patch('task_details.difflib.ndiff') as diff:
                    for _ in range(25):settle_view(app)
                    self.assertEqual(rows.call_count,0);diff.assert_not_called()
                    app.table_snapshot=None;settle_view(app)
                    self.assertEqual(rows.call_count,0);diff.assert_not_called()
                app.search_var.set('不存在');settle_view(app)
                self.assertEqual(len(app.table.get_children()),0)
                app.search_var.set('第二版');settle_view(app)
                self.assertEqual(len(app.table.get_children()),1)
                app.store.done([SAMPLE]);app.filter_var.set('已处理');settle_view(app)
                self.assertEqual(len(app.table.get_children()),1)
            finally:root.update_idletasks();root.destroy()

    def test_clock_change_refreshes_deadline_without_database_write(self):
        import tkinter as tk
        from datetime import datetime, timedelta
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder)
            try:
                app.store.add_many([SAMPLE])
                app.store.save_dates(SAMPLE,'','',(datetime.now()+timedelta(days=1)).strftime('%Y-%m-%d %H:%M'))
                with patch('app.time.time',return_value=600),patch('task_query.countdown',return_value='剩余 1分钟'):settle_view(app)
                with patch('app.time.time',return_value=660),patch('task_query.countdown',return_value='已截止'):settle_view(app)
                self.assertEqual(app.table.item(SAMPLE,'values')[-1],'已截止')
            finally:root.update_idletasks();root.destroy()


if __name__=='__main__':unittest.main()
