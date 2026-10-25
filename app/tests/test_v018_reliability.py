import hashlib,io,sqlite3,sys,tempfile,threading,time,unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock,patch
from zipfile import ZipFile
import tkinter as tk
from tkinter import ttk
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from task_store import TaskStore
from task_links import canonical_task_url,task_identity
from diagnostics import ErrorRateLimiter,record_error
from open_policy import open_failure_notice
from backup_support import make_backup,inspect_backup,stage_restore,apply_pending
from backup_validation import validate_database
from settings import validate_settings
from attachment_store import add_image,associate,images
from notice_tools import import_notice
from test_queue import Context,PW
from test_attachment_ui import descendants

BASE='https://docs.qq.com/sheet/ReliabilityTest'
A=BASE+'?tab=A&u=Keep&_t=1'
B=BASE+'?tab=A&u=Keep&_t=2'


class Identity(unittest.TestCase):
    def test_navigation_unchanged_and_numeric_stamp_identity(self):
        self.assertEqual(canonical_task_url(A),A)
        self.assertEqual(task_identity(A),task_identity(B))
        self.assertEqual(task_identity(A),task_identity(BASE+'?tab=A&u=Keep'))

    def test_tabs_access_unknown_query_and_fragment_remain_distinct(self):
        for url in [A.replace('tab=A','tab=B'),A.replace('Keep','Other'),A+'&extra=1',A+'#cell=B2',A.replace('tab=A&u=Keep','u=Keep&tab=A')]:
            self.assertNotEqual(task_identity(A),task_identity(url))

    def test_malformed_encoded_or_repeated_stamps_not_removed(self):
        for query in ['_t=bad','_t=','_t=1&_t=2','%5Ft=1','_t='+('1'*21),'_t=+1']:
            self.assertNotEqual(task_identity(BASE),task_identity(BASE+'?'+query))
        self.assertIsNone(task_identity('javascript:bad'))

    def test_kdocs_has_independent_conservative_rules(self):
        base='https://www.kdocs.cn/l/LocalTest'
        self.assertNotEqual(task_identity(base+'?_t=1'),task_identity(base+'?_t=2'))


class StoreAndMigration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='中文迁移-');self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name);self.store=TaskStore(self.data/'tasks.sqlite3')

    def test_capture_keeps_original_url_notice_and_done_state(self):
        s=self.store;s.add_many([A],notices=[(A,'活动原通知🌙')]);s.done([A])
        self.assertEqual(s.add_many([B],notices=[(B,'更新通知🌿')]),0)
        self.assertEqual(len(s.rows()),1);self.assertEqual(s.task(B)['url'],A)
        self.assertEqual(s.state(A),'done');self.assertEqual(len(s.notices(A)),2)
        self.assertEqual(s.task(A)['notice_revision'],1)

    def test_preview_empty_batch_variants_and_actual_count(self):
        before=Path(self.store.path).read_bytes()
        self.assertEqual(self.store.preview_additions([]),{'links':0,'new':0,'merged':0})
        self.assertEqual(self.store.preview_additions([A,B]),{'links':2,'new':1,'merged':1})
        self.assertEqual(Path(self.store.path).read_bytes(),before)
        self.assertEqual(self.store.add_many([A,B]),1)
        self.assertEqual(self.store.preview_additions([A,B]),{'links':2,'new':0,'merged':2})

    def test_preview_separate_tabs_and_raw_duplicate(self):
        urls=[A,B,A.replace('tab=A','tab=B'),A]
        self.assertEqual(self.store.preview_additions(urls),{'links':4,'new':2,'merged':2})
        self.assertEqual(self.store.add_many(urls),2)

    def test_invalid_batch_rolls_back_and_invalid_preview_read_only(self):
        with self.assertRaises(ValueError):self.store.add_many([A,'javascript:bad'])
        with self.assertRaises(ValueError):self.store.preview_additions([A,'javascript:bad'])
        self.assertEqual(self.store.rows(),[])

    def legacy_fixture(self):
        s=self.store;s.add_many([A],notices=[(A,'完整原通知')])
        s.save_dates(A,'2026-10-10 15:00','','2026-10-09 23:00')
        s.save_activity(A,'旧版备注','待考虑',[{'text':'准备作品','done':True}],'custom',[30])
        s.remind_later(A);s.disposition([A],'ignored')
        key,_=add_image(s,Image.new('RGB',(20,20),'white'));associate(s,key,A)
        with s.connection() as db:
            db.execute('DROP INDEX tasks_by_link_identity')
            db.execute('ALTER TABLE tasks DROP COLUMN link_identity')
            db.execute('INSERT INTO tasks(url,source,created,updated,state) VALUES(?,?,?,?,?)',(B,'旧版',1,1,'done'))
        return key

    def test_additive_migration_preserves_historical_duplicates_and_metadata(self):
        key=self.legacy_fixture();s=TaskStore(self.store.path)
        self.assertEqual(len(s.rows()),2);row=s.task(A)
        self.assertEqual(row['activity_notes'],'旧版备注');self.assertEqual(row['reminder_mode'],'custom')
        self.assertEqual(row['disposition'],'ignored');self.assertEqual(row['deadline'],'2026-10-09 23:00')
        self.assertIsNotNone(row['remind_again_at']);self.assertEqual(s.state(B),'done')
        self.assertEqual(s.notices(A)[0]['body'],'完整原通知');self.assertEqual(images(s,url=A)[0]['id'],key)
        self.assertEqual(s.add_many([A,B]),0)
        self.assertEqual(s.add_many([BASE+'?tab=A&u=Keep&_t=3']),0)
        self.assertEqual(len(s.rows()),2)

    def test_schema_failure_rolls_back_and_retry_migration_succeeds(self):
        self.legacy_fixture()
        with patch('task_store.task_identity',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):TaskStore(self.store.path)
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertNotIn('link_identity',{r[1] for r in db.execute('PRAGMA table_info(tasks)')})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],2)
        self.assertEqual(len(TaskStore(self.store.path).rows()),2)

    def test_backup_legacy_inspection_does_not_modify_original(self):
        self.legacy_fixture();before=Path(self.store.path).read_bytes()
        archive=make_backup(self.store,self.data,validate_settings({}),'manual')
        self.assertEqual(inspect_backup(archive,validate_settings)[3],2)
        self.assertEqual(Path(self.store.path).read_bytes(),before)

    def test_backup_roundtrip_rebuilds_index_and_preserves_association(self):
        self.store.add_many([A],notices=[(A,'备份通知')])
        key,_=add_image(self.store,Image.new('RGB',(10,10)));associate(self.store,key,A)
        archive=make_backup(self.store,self.data,validate_settings({}),'manual')
        with ZipFile(archive) as z:
            temporary=self.data/'snapshot.sqlite3';temporary.write_bytes(z.read('tasks.sqlite3'))
        with closing(sqlite3.connect(temporary)) as db:
            indices={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertNotIn('tasks_by_link_identity',indices)
        target=self.data/'恢复';target.mkdir();stage_restore(archive,target,validate_settings)
        apply_pending(target,validate_settings);s=TaskStore(target/'tasks.sqlite3')
        self.assertEqual(s.add_many([B]),0);self.assertEqual(images(s,url=A)[0]['id'],key)
        self.assertEqual(validate_database(s.path),1)

    def test_tampered_identity_and_expression_index_rejected(self):
        self.store.add_many([A])
        with self.store.connection() as db:db.execute('UPDATE tasks SET link_identity=?',('forged',))
        with self.assertRaises(ValueError):validate_database(self.store.path)
        with self.store.connection() as db:
            db.execute('UPDATE tasks SET link_identity=?',(task_identity(A),))
            db.execute('DROP INDEX tasks_by_link_identity')
            db.execute('CREATE INDEX tasks_by_link_identity ON tasks(lower(url))')
        with self.assertRaises(ValueError):validate_database(self.store.path)

    def test_two_store_concurrent_capture_creates_one_task(self):
        second=TaskStore(self.store.path);barrier=threading.Barrier(2)
        def capture(args):
            store,url=args;barrier.wait();return store.add_many([url],notices=[(url,'通知')])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(capture,[(self.store,A),(second,B)]))
        self.assertEqual(sum(results),1);self.assertEqual(len(self.store.rows()),1)


class DiagnosticsAndFeedback(unittest.TestCase):
    def test_retry_off_paused_limit_and_changed_state_messages(self):
        row={'url':A,'state':'failed','disposition':'active','retry_at':1}
        self.assertIn('手动',open_failure_notice(row,{'auto_open_documents':False}))
        self.assertIn('重试时间',open_failure_notice(row,{}))
        self.assertIn('暂停',open_failure_notice(row,{},False))
        row['retry_at']=None;self.assertIn('上限',open_failure_notice(row,{}))
        row['disposition']='ignored';self.assertIn('不会重试',open_failure_notice(row,{}))
        row['state']='done';self.assertIn('最新状态',open_failure_notice(row,{}))

    def test_redaction_rate_interval_independent_locations_and_bound(self):
        ticks=[0];limit=ErrorRateLimiter(clock=lambda:ticks[0],max_keys=3);logger=Mock()
        error=ValueError('private name https://example.invalid/token')
        for _ in range(20):record_error(logger,'monitor',error,limiter=limit)
        self.assertEqual(logger.warning.call_count,1)
        self.assertNotIn('private',str(logger.warning.call_args));self.assertNotIn('https',str(logger.warning.call_args))
        ticks[0]=60;record_error(logger,'monitor',error,limiter=limit)
        record_error(logger,'preview',error,limiter=limit);self.assertEqual(logger.warning.call_count,3)
        for i in range(20):limit.allow(('location',i))
        self.assertEqual(len(limit.recent),3)

    def test_rate_limiter_concurrent_repetition(self):
        limit=ErrorRateLimiter(clock=lambda:0)
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:limit.allow(('same',)),range(100)))
        self.assertEqual(sum(results),1)


class UIAndWorker(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,self.tmp.name);self.root.after_cancel(self.app.tick_timer)
        self.app.config.update(auto_open_forms=False,auto_open_documents=False,notify_popup=False,backup_enabled=False)

    def tearDown(self):
        self.app.quit_event.set();self.root.update_idletasks();self.root.destroy();self.tmp.cleanup()

    def test_import_preview_matches_variant_save_count(self):
        import_notice(self.app)
        win=next(w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel))
        widgets=list(descendants(win));texts=[w for w in widgets if isinstance(w,tk.Text)]
        texts[0].insert('1.0','完整通知\n'+A+'\n'+B);self.root.update()
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='分析预览').invoke()
        self.assertIn('预计新增 1 项',texts[1].get('1.0','end'))
        self.assertIn('其余 1 个链接',texts[1].get('1.0','end'))
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='确认导入').invoke()
        self.assertEqual(len(self.app.store.rows()),1)
        self.assertEqual(len(self.app.store.notices(A)),1)

    def test_worker_manual_failure_feedback_and_no_auto_retry(self):
        fail='https://docs.qq.com/sheet/Fail';a=self.app
        a.store.add_many([fail]);a.manual_open_urls.add(fail);a.processing.set();context=Context()
        with patch('playwright.sync_api.sync_playwright',lambda:PW(context)),patch.object(a,'emit') as emit:
            thread=threading.Thread(target=a.browser);thread.start()
            try:
                until=time.monotonic()+4
                while time.monotonic()<until and not any('请手动重新排队' in str(c) for c in emit.call_args_list):time.sleep(.01)
                self.assertTrue(any('请手动重新排队' in str(c) for c in emit.call_args_list))
                self.assertEqual(context.visits,[fail])
            finally:a.quit_event.set();thread.join(4)
        self.assertFalse(thread.is_alive())

    def test_worker_preview_failure_logged_once_without_private_values(self):
        from core import SAMPLE
        a=self.app;a.config['auto_open_forms']=True;a.store.add_many([SAMPLE]);a.processing.set();context=Context()
        with patch('playwright.sync_api.sync_playwright',lambda:PW(context)),patch('app.build_plan',side_effect=RuntimeError('private profile')):
            thread=threading.Thread(target=a.browser);thread.start()
            try:
                until=time.monotonic()+4;log=Path(self.tmp.name)/'diagnostics.log'
                while time.monotonic()<until and 'form-preview' not in log.read_text(encoding='utf-8'):time.sleep(.01)
                time.sleep(.06);body=log.read_text(encoding='utf-8')
                self.assertEqual(body.count('form-preview'),1);self.assertNotIn('private profile',body)
            finally:a.quit_event.set();thread.join(4)
        self.assertFalse(thread.is_alive())


if __name__=='__main__':unittest.main()
