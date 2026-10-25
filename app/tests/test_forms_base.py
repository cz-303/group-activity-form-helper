import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from core import *

DEFAULTS = dict(DEFAULTS, group='测试学院2026级研究生群', 姓名='测试同学', 学号='TEST001', 班级='测试班', 学院='测试学院')
from playwright.sync_api import sync_playwright

class Rules(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(canonical_url(SAMPLE + '?x=1#x'), SAMPLE)
        for url in ['http://docs.qq.com/form/page/abc', 'https://docs.qq.com.evil.test/form/page/abc',
                    'https://user@docs.qq.com/form/page/abc', 'https://docs.qq.com:123/form/page/abc',
                    'https://docs.qq.com/sheet/a', 'https://evil.test/?' + SAMPLE]:
            self.assertIsNone(canonical_url(url))
        self.assertEqual(links_from_text('活动收集表 ' + SAMPLE + '，欢迎报名！'), [SAMPLE])
    def test_capture_existing_and_standalone_links(self):
        urls, notices = capture_notifications([SAMPLE, '活动 '+SAMPLE])
        self.assertEqual(urls, [SAMPLE])
        self.assertEqual(len(notices), 2)
    def test_window_scope(self):
        group = DEFAULTS['group']
        self.assertTrue(matching_window(group, group))
        self.assertTrue(matching_window(group+' (126) - ChatApp', group, 'ChatApp'))
        self.assertFalse(matching_window(group+' - 其他', group, 'ChatApp'))
        for name in ['ChatApp', '另一个群 '+group, group+'通知群', group[:10]]:
            self.assertFalse(matching_window(name, group))

class Forms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.b = cls.p.chromium.launch(channel='msedge',headless=True)
    @classmethod
    def tearDownClass(cls):
        cls.b.close(); cls.p.stop()
    def setUp(self):
        self.page = self.b.new_page()
    def tearDown(self):
        self.page.close()
    def fixture(self, titles, disabled=False):
        html = '<form onsubmit="window.submitted=true;return false">'
        for title in titles:
            html += '<div class="question-main-content"><div class="question-title">'+title+'</div><textarea '+('disabled' if disabled else '')+'></textarea></div>'
        self.page.set_content(html+'<button type="submit">提交</button></form>')
    def test_known_order_and_phone(self):
        self.fixture(['姓名','学院','班级','学号','联系电话'])
        status = fill_known_fields(self.page, DEFAULTS)
        self.assertEqual(self.page.locator('textarea').evaluate_all('(a)=>a.map(e=>e.value)'),
                         [DEFAULTS[k] for k in ['姓名','学院','班级','学号','联系电话']])
        self.assertIn('联系电话',status)
        self.assertFalse(self.page.evaluate('Boolean(window.submitted)'))
    def test_locked_form(self):
        self.fixture(['姓名'], True)
        self.assertIn('登录', fill_known_fields(self.page,DEFAULTS))
        self.assertEqual(self.page.locator('textarea').input_value(),'')
    def test_no_overwrite_or_refill(self):
        self.fixture(['姓名','学号'])
        self.page.locator('textarea').nth(0).fill('自己改过的姓名')
        touched = set()
        fill_known_fields(self.page,DEFAULTS,touched)
        self.assertEqual(self.page.locator('textarea').nth(0).input_value(),'自己改过的姓名')
        self.page.locator('textarea').nth(1).fill('')
        fill_known_fields(self.page,DEFAULTS,touched)
        self.assertEqual(self.page.locator('textarea').nth(1).input_value(),'')
    def test_unknown_and_duplicate(self):
        self.fixture(['姓名','姓名','紧急联系人姓名','手机验证码','所在班级'])
        fill_known_fields(self.page,DEFAULTS)
        self.assertEqual(self.page.locator('textarea').evaluate_all('(a)=>a.map(e=>e.value)'),['','','','',DEFAULTS['班级']])

if __name__=='__main__':unittest.main()
