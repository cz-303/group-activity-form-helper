"""Local Edge + real worker/storage/provider faults. No remote form is contacted."""
import copy
import logging
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from diagnostics import ErrorRateLimiter
from sheet_fill import make_sheet_plan
from task_store import TaskStore
from test_sheet_fill import HTML, PROFILE, URL, data
from playwright.sync_api import sync_playwright


class WorkerFaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.route('**/*', lambda route: route.fulfill(
            body=HTML, content_type='text/html; charset=utf-8'))
        self.page.goto(URL)
        self.app = App.__new__(App)
        self.app.sheet_requests = queue.Queue(maxsize=1)
        self.app.processing = threading.Event()
        self.app.processing.set()
        self.app.quit_event = threading.Event()
        self.app.store = TaskStore(Path(self.temp.name) / 'tasks.sqlite3')
        self.app.store.add_many([URL], source='isolated fault test')
        self.assertIsNotNone(self.app.store.claim())
        self.app.store.opened(URL)
        self.assertEqual(self.app.store.state(URL), 'opened')
        self.app.get_config = lambda: copy.deepcopy(PROFILE)
        self.app.logger = logging.getLogger('isolated-worker-faults')
        self.app.logger.handlers = [logging.NullHandler()]
        self.app.logger.propagate = False
        self.app.error_limiter = ErrorRateLimiter()
        self.request = {
            'url': URL, 'action': 'write', 'created': time.monotonic(),
            'cancel': threading.Event(), 'reply': queue.Queue(maxsize=1),
            'plan': make_sheet_plan(data(), PROFILE, 1),
            'approved': {'A3': PROFILE['班级'], 'B3': PROFILE['姓名'],
                         'C3': PROFILE['学号']}, 'confirmed_layout': True,
        }

    def dispatch(self, entries=None):
        self.app.sheet_requests.put_nowait(self.request)
        self.app.read_sheet_request(
            {URL: {'page': self.page}} if entries is None else entries)
        return self.request['reply'].get_nowait()

    def assert_rejected_without_writes(self):
        reply = self.dispatch()
        self.assertIn('error', reply)
        self.assertEqual(self.page.evaluate('writes'), [])
        self.assertEqual(self.page.evaluate('data.A2'), '234')
        self.assertEqual(self.app.store.state(URL), 'opened')
        self.assertTrue(self.app.sheet_requests.empty())

    def test_cancelled_before_dispatch(self):
        self.request['cancel'].set()
        self.assert_rejected_without_writes()
        self.assertNotIn('consumed', self.request)

    def test_expired_before_dispatch(self):
        self.request['created'] = time.monotonic() - 121
        self.assert_rejected_without_writes()
        self.assertNotIn('consumed', self.request)

    def test_paused_before_dispatch(self):
        self.app.processing.clear()
        self.assert_rejected_without_writes()

    def test_exiting_before_dispatch(self):
        self.app.quit_event.set()
        self.assert_rejected_without_writes()

    def test_ignored_task_before_dispatch(self):
        self.app.store.disposition([URL], 'ignored')
        self.assert_rejected_without_writes()

    def test_missing_page(self):
        self.assertIn('error', self.dispatch(entries={}))
        self.assertEqual(self.page.evaluate('writes'), [])
        self.assertNotIn('consumed', self.request)

    def test_closed_page(self):
        self.page.close()
        self.assertIn('error', self.dispatch())
        self.assertEqual(self.app.store.state(URL), 'opened')
        self.assertNotIn('consumed', self.request)

    def test_changed_target_preserves_concurrent_content(self):
        self.page.evaluate("data.B3='并发内容'")
        self.assert_rejected_without_writes()
        self.assertEqual(self.page.evaluate('data.B3'), '并发内容')

    def test_dropped_save_is_not_reported_success(self):
        self.page.evaluate('window.fail=true')
        reply = self.dispatch()
        self.assertIn('error', reply)
        self.assertEqual(self.page.evaluate('writes'), [])
        self.assertEqual(self.app.store.state(URL), 'opened')
        self.assertNotIn('已写入并回读核验', self.app.store.task(URL)['note'])

    def test_cancel_after_first_commit_keeps_partial_data_and_never_retries(self):
        page = self.page

        class CancelAfterCommit:
            def is_set(self):
                return page.evaluate('writes.length') >= 1

        self.request['cancel'] = CancelAfterCommit()
        first = self.dispatch()
        self.assertIn('error', first)
        self.assertIn('可能', first['error'])
        self.assertEqual(self.page.evaluate('writes'), ['A3'])
        self.assertEqual(self.page.evaluate('data.A3'), PROFILE['班级'])
        self.assertIsNone(self.page.evaluate('data.B3'))
        self.assertEqual(self.app.store.state(URL), 'opened')
        # Re-enqueueing an already consumed approval cannot write another cell.
        self.request['cancel'] = threading.Event()
        second = self.dispatch()
        self.assertIn('已经使用', second['error'])
        self.assertEqual(self.page.evaluate('writes'), ['A3'])

    def test_success_consumes_approval_and_keeps_task_opened(self):
        reply = self.dispatch()
        self.assertEqual(reply['written'], ['A3', 'B3', 'C3'])
        self.assertEqual(self.app.store.state(URL), 'opened')
        self.assertIn('已写入并回读核验 3 格', self.app.store.task(URL)['note'])
        self.assertIn('已经使用', self.dispatch()['error'])
        self.assertEqual(self.page.evaluate('writes'), ['A3', 'B3', 'C3'])


if __name__ == '__main__':
    unittest.main()
