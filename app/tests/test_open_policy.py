import sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from settings import validate_settings,save_settings,load_settings
from open_policy import queue_filter,auto_open_allowed
from task_store import TaskStore
from maintenance import run_maintenance
from core import SAMPLE
from app import App
from test_queue import Context,PW
from test_attachment_ui import descendants
from ui_wait import settle_view

TX='https://docs.qq.com/sheet/OpenPolicyTest'
KD='https://www.kdocs.cn/l/OpenPolicyTest'
OFF={'auto_open_forms':False,'auto_open_documents':False}


class PolicyQueue(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=TaskStore(self.root/'tasks.sqlite3')
        self.store.add_many([SAMPLE,TX,KD],notices=[(SAMPLE,'原通知'),(TX,'表格原通知')])

    def test_old_configuration_preserves_behavior_and_boolean_validation(self):
        config=validate_settings({})
        self.assertTrue(config['auto_open_forms']);self.assertTrue(config['auto_open_documents'])
        for key in OFF:
            for value in ['false',0,None]:
                with self.assertRaises(ValueError):validate_settings({key:value})

    def test_preferences_persist_without_affecting_startup_setting(self):
        save_settings(self.root/'config.json',dict(OFF,auto_resume=True))
        config,warning=load_settings(self.root/'config.json')
        self.assertFalse(warning);self.assertTrue(config['auto_resume'])
        self.assertFalse(config['auto_open_forms']);self.assertFalse(config['auto_open_documents'])

    def test_both_off_preserves_tasks_notices_and_attempt_counts(self):
        eligibility=queue_filter(OFF)
        self.assertFalse(self.store.due(eligibility=eligibility));self.assertIsNone(self.store.claim(eligibility=eligibility))
        self.assertEqual(len(self.store.rows()),3);self.assertEqual(self.store.notices(SAMPLE)[0]['body'],'原通知')
        self.assertTrue(all(r['attempts']==0 for r in self.store.rows()))

    def test_disabled_first_item_does_not_block_allowed_type(self):
        eligibility=queue_filter(dict(OFF,auto_open_documents=True))
        found=[]
        while row:=self.store.claim(eligibility=eligibility):
            found.append(row['url']);self.store.opened(row['url'])
        self.assertEqual(set(found),{TX,KD});self.assertEqual(self.store.state(SAMPLE),'pending')
        self.assertEqual(self.store.claim(eligibility=queue_filter(dict(OFF,auto_open_forms=True)))['url'],SAMPLE)

    def test_manual_exception_is_scoped_to_exact_url_and_not_sql_input(self):
        row=self.store.claim(eligibility=queue_filter(OFF,[KD,"x') OR 1=1 --"]))
        self.assertEqual(row['url'],KD);self.assertIsNone(self.store.claim(eligibility=queue_filter(OFF,[KD])))

    def test_disabled_retry_snooze_and_restart_stay_held(self):
        row=self.store.claim(now=1);self.store.failed(row['url'],'test',now=1,delay=1)
        with self.store.connection() as db:db.execute("UPDATE tasks SET disposition='snoozed',snooze_until=1 WHERE url=?",(TX,))
        restored=TaskStore(self.store.path);config=validate_settings(OFF)
        result=run_maintenance(restored,self.root,config)
        self.assertFalse(result.due);self.assertEqual(result.woke,1)
        self.assertTrue(run_maintenance(restored,self.root,config,manual_urls=[KD]).due)
        self.assertEqual(restored.state(row['url']),'failed')

    def test_ignored_and_done_never_bypassed_by_manual_filter(self):
        self.store.disposition([TX],'ignored');self.store.done([KD])
        self.assertIsNone(self.store.claim(eligibility=queue_filter(OFF,[TX,KD])))

    def test_unknown_url_not_authorized(self):
        self.assertFalse(auto_open_allowed('https://example.com',validate_settings({})))


class BrowserPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.tmp.name)
        self.root.after_cancel(self.app.tick_timer)
        self.app.config.update(OFF,backup_enabled=False,notify_popup=False)
        self.app.store.add_many([SAMPLE,TX,KD]);self.app.processing.set()
        self.addCleanup(lambda:(self.root.update_idletasks(),self.root.destroy()))

    def run_browser(self,context,predicate):
        with patch('playwright.sync_api.sync_playwright',lambda:PW(context)):
            thread=threading.Thread(target=self.app.browser);thread.start()
            try:
                until=time.monotonic()+3
                while not predicate() and time.monotonic()<until:time.sleep(.01)
                self.assertTrue(predicate())
                time.sleep(.08)
            finally:self.app.quit_event.set();thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_both_off_does_not_even_launch_a_browser(self):
        with patch('playwright.sync_api.sync_playwright') as browser:
            self.app.browser();browser.assert_not_called()

    def test_explicit_manual_open_once_without_enabling_other_tasks(self):
        settle_view(self.app);self.app.table.selection_set(TX);self.app.requeue_selected()
        context=Context();self.run_browser(context,lambda:TX in context.visits)
        self.assertEqual(context.visits,[TX]);self.assertEqual(self.app.manual_open_urls,set())
        self.assertFalse(self.app.config['auto_open_documents']);self.assertEqual(self.app.store.state(SAMPLE),'pending')

    def test_manual_failure_does_not_retry_when_auto_open_off(self):
        fail='https://docs.qq.com/sheet/Fail';self.app.store.add_many([fail]);self.app.manual_open_urls.add(fail)
        context=Context();self.run_browser(context,lambda:self.app.store.state(fail)=='failed')
        self.assertEqual(context.visits,[fail]);self.assertEqual(self.app.manual_open_urls,set())
        self.assertIsNone(self.app.store.claim(now=time.time()+10000,eligibility=queue_filter(OFF)))

    def test_turn_off_during_page_creation_stops_navigation(self):
        self.app.config['auto_open_documents']=True
        app=self.app
        class SwitchingContext(Context):
            def new_page(self):
                page=super().new_page()
                with app.config_lock:app.config['auto_open_documents']=False
                return page
        context=SwitchingContext();self.run_browser(context,lambda:len(context.all)>1 and context.all[-1].closed)
        self.assertEqual(context.visits,[]);self.assertEqual(self.app.store.state(TX),'pending')

    def test_settings_ui_save_and_waiting_state(self):
        self.app.open_settings();win=self.app.settings_window
        self.app.settings_notebook.select(2);self.app.settings_notebook.ensure_selected()
        controls=list(descendants(win));checks=[w for w in controls if isinstance(w,ttk.Checkbutton)]
        toggle=next(w for w in checks if w.cget('text')=='收集表：捕获链接后自动跳转')
        toggle.invoke()
        save=next(w for w in controls if isinstance(w,ttk.Button) and w.cget('text')=='保存设置')
        save.invoke();self.assertTrue(self.app.config['auto_open_forms'])
        self.assertFalse(self.app.config['auto_open_documents'])
        settle_view(self.app)
        self.assertEqual(self.app.table.item(TX,'values')[1],'等待手动打开')
        self.assertEqual(self.app.table.item(SAMPLE,'values')[1],'待处理')
        self.app.table.selection_set(TX);settle_view(self.app)
        self.assertIn('等待手动打开',self.app.detail_meta.get())
        self.assertIn('重试时间不触发自动打开',self.app.detail_tabs['处理信息'].get('1.0','end'))

    def test_manual_permission_is_not_restored_after_restart(self):
        self.app.manual_open_urls.add(TX)
        config=validate_settings(OFF);save_settings(Path(self.tmp.name)/'config.json',config)
        self.assertFalse(run_maintenance(TaskStore(self.app.store.path),self.tmp.name,config).due)

    def test_ignore_defer_done_revoke_old_manual_request(self):
        settle_view(self.app);self.app.table.selection_set(TX)
        for action in [self.app.ignore_selected,lambda:self.app.defer_selected(600),self.app.done_selected]:
            self.app.store.requeue([TX]);self.app.manual_open_urls.add(TX)
            action();self.assertNotIn(TX,self.app.manual_open_urls)


if __name__=='__main__':unittest.main()
