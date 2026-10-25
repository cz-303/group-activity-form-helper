import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_query import TaskViewReader, LatestQuery, ViewRequest, QueryCancelled
from task_store import TaskStore
from core import SAMPLE
from ui_wait import settle_view

def request(**changes):
    return replace(ViewRequest('','进行中','全部来源','全部时间','最近捕获',(),int(time.time()//60)),**changes)

def drain(query):
    until=time.monotonic()+3
    while query.busy and time.monotonic()<until:
        query.poll();time.sleep(.005)
    if query.busy:raise AssertionError('Query did not drain')


class LatestTests(unittest.TestCase):
    def test_many_changes_keep_only_latest_and_one_active_reader(self):
        gate=threading.Event();started=threading.Event();calls=[];results=[]
        def read(value,cancel):
            calls.append(value);started.set();gate.wait(2);return value
        query=LatestQuery(read,lambda k,v:results.append(v),lambda k,e:self.fail(str(e)))
        try:
            query.request('first');self.assertTrue(started.wait(1))
            for n in range(100):query.request(str(n))
            self.assertEqual(calls,['first'])
            gate.set();drain(query)
            self.assertEqual(calls,['first','99']);self.assertEqual(results,['99'])
        finally:gate.set();query.close();query.jobs.thread.join(3)

    def test_old_failure_is_not_shown_and_new_request_succeeds(self):
        gate=threading.Event();results=[];errors=[]
        def read(key,cancel):
            if key=='old':gate.wait(2);raise ValueError('old error')
            return key
        query=LatestQuery(read,lambda k,v:results.append(v),lambda k,e:errors.append(e))
        query.request('old');query.request('new');gate.set();drain(query);query.close()
        self.assertEqual(results,['new']);self.assertEqual(errors,[])

    def test_input_invalidation_and_close_discard_late_result(self):
        for close in (False,True):
            gate=threading.Event();seen=[]
            query=LatestQuery(lambda k,c:(gate.wait(2),k)[1],lambda k,v:seen.append(v),lambda k,e:seen.append(e))
            query.request('old')
            if close:query.close()
            else:query.invalidate()
            gate.set();query.jobs.thread.join(3);query.poll()
            self.assertEqual(seen,[]);query.close()

    def test_failure_does_not_prevent_retry(self):
        errors=[];values=[];fail=[True]
        def read(key,cancel):
            if fail[0]:raise PermissionError()
            return 'ok'
        query=LatestQuery(read,lambda k,v:values.append(v),lambda k,e:errors.append(e))
        query.request('same');drain(query);fail[0]=False;query.request('same');drain(query);query.close()
        self.assertEqual(len(errors),1);self.assertEqual(values,['ok'])


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='中文查询🌙-');self.addCleanup(self.temp.cleanup)
        self.store=TaskStore(Path(self.temp.name)/'tasks.sqlite3')
        self.store.add_many([SAMPLE],source='测试群',notices=[(SAMPLE,'中秋🌙 100% AI_作品')])
        self.reader=TaskViewReader(self.store.path);self.cancel=threading.Event()

    def test_search_is_literal_and_filters_are_preserved(self):
        for text in ('中秋🌙','100%','AI_作品'):
            self.assertEqual(self.reader(request(search=text),self.cancel)['matched'],1)
        self.assertEqual(self.reader(request(search="' OR 1=1"),self.cancel)['matched'],0)
        self.assertEqual(self.reader(request(source='其他群'),self.cancel)['matched'],0)
        self.assertEqual(self.reader(request(time_filter='未设截止'),self.cancel)['matched'],1)

    def test_reader_never_recovers_or_writes_tasks(self):
        self.store.claim();before=Path(self.store.path).read_bytes()
        result=self.reader(request(selection=(SAMPLE,)),self.cancel)
        self.assertEqual(self.store.state(SAMPLE),'opening')
        self.assertEqual(Path(self.store.path).read_bytes(),before)
        self.assertIn('中秋🌙',result['detail']['tabs']['通知原文'])

    def test_clock_change_does_not_recompute_notice_diff(self):
        self.store.add_many([SAMPLE],notices=[(SAMPLE,'第二版通知')])
        first=request(selection=(SAMPLE,))
        self.reader(first,self.cancel)
        with patch('task_details.difflib.ndiff',side_effect=AssertionError('Unchanged diff')):
            self.reader(replace(first,minute=first.minute+1),self.cancel)

    def test_cache_skips_database_then_external_changes_invalidate(self):
        self.reader(request(),self.cancel)
        with patch('task_query.sqlite3.connect',side_effect=AssertionError('Unexpected DB read')):
            self.reader(request(),self.cancel)
        self.store.done([SAMPLE])
        self.assertEqual(self.reader(request(),self.cancel)['matched'],0)

    def test_cancelled_request_and_abusive_search_are_rejected(self):
        self.cancel.set()
        with self.assertRaises(QueryCancelled):self.reader(request(),self.cancel)
        self.cancel.clear()
        for text in ('字'*2001,'词 '*33):
            with self.assertRaises(ValueError):self.reader(request(search=text),self.cancel)

    def test_locked_database_failure_can_be_retried(self):
        db=sqlite3.connect(self.store.path)
        try:
            db.execute('BEGIN EXCLUSIVE')
            with self.assertRaises(sqlite3.OperationalError):self.reader(request(),self.cancel)
        finally:db.rollback();db.close()
        self.assertEqual(self.reader(request(),self.cancel)['matched'],1)

    def test_long_updated_notices_keep_original_without_expensive_diff(self):
        body='超长通知🌙\n'*500
        self.store.add_many([SAMPLE],notices=[(SAMPLE,body)])
        with patch('task_details.difflib.ndiff',side_effect=AssertionError('Diff should be bounded')):
            result=self.reader(request(selection=(SAMPLE,)),self.cancel)
        text=result['detail']['tabs']['通知原文']
        self.assertIn(body,text);self.assertIn('直接对照',text)


class UITests(unittest.TestCase):
    def test_slow_reader_keeps_ui_alive_latest_filter_wins(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder);gate=threading.Event();heartbeats=[]
            try:
                app.store.add_many([SAMPLE],notices=[(SAMPLE,'中秋')]);settle_view(app)
                original=app.query.reader
                def slow(req,cancel):gate.wait(3);return original(req,cancel)
                app.query.reader=slow
                app.refresh_table()
                root.after(10,lambda:heartbeats.append('alive'))
                root.after(300,root.quit);root.mainloop()
                self.assertEqual(heartbeats,['alive']);self.assertIn('正在查询',app.query_status.get())
                app.search_var.set('不会命中');root.update()
                gate.set();settle_view(app)
                self.assertEqual(app.table.get_children(),())
                self.assertEqual(app.detail_tabs['通知原文'].get('1.0','end-1c'),'')
            finally:
                gate.set();root.destroy()

    def test_error_preserves_rows_and_retry_recovers(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder)
            try:
                app.store.add_many([SAMPLE]);settle_view(app);original=app.query.reader
                def fail(req,cancel):raise PermissionError('private local path')
                app.query.reader=fail;app.refresh_table()
                until=time.monotonic()+2
                while app.query.busy and time.monotonic()<until:root.update();time.sleep(.01)
                self.assertEqual(app.table.get_children(),(SAMPLE,))
                self.assertIn('保留上次结果',app.query_status.get());self.assertNotIn('private',app.query_status.get())
                app.query.reader=original;app.retry_query();settle_view(app)
                self.assertEqual(app.query_status.get(),'')
            finally:root.destroy()

    def test_destroy_during_slow_read_discards_callback(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder);seen=[]
            root.update_idletasks()
            def read(req,cancel):cancel.wait(2);return {}
            app.query.reader=read;app.query.success=lambda *args:seen.append(True)
            app.refresh_table();root.destroy()
            self.assertFalse(app.query.jobs.thread.is_alive());self.assertEqual(seen,[])


if __name__=='__main__':unittest.main()
