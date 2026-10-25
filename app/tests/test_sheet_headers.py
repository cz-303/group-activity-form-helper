import sys,tempfile,threading,queue,time,unittest
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sheet_headers import *
from providers.tencent_sheet import read_header_region,is_tencent_sheet
from diagnostics import UserFacingError
from playwright.sync_api import sync_playwright

URL='https://docs.qq.com/sheet/HeaderLocalTest?tab=Test'

class HeaderAnalysis(unittest.TestCase):
    def analyze(self,text,profile=None,start='A1'):
        bounds,matrix=parse_pasted_region(text,start)
        return analyze_headers(bounds,matrix,profile or {})

    def test_column_roundtrip(self):
        for n in (1,26,27,702,703,16384):self.assertEqual(column_number(column_name(n)),n)
        for n in (0,-1,16385,True,'1'):
            with self.assertRaises(ValueError):column_name(n)
        for s in ('','a','A1','../A','XFE'):
            with self.assertRaises(ValueError):column_number(s)

    def test_invalid_ranges(self):
        for s in ('','A:A','1:2','A0:B1','B2:A1','XFE1:XFE2','A1048576:A1048577','A1:Z20','A1:A21','https://x',None):
            with self.subTest(s=s),self.assertRaises(ValueError):parse_range(s)

    def test_range_normalization(self):
        self.assertEqual(parse_range(' $aa$2:$ac$4 ').address,'AA2:AC4')
        self.assertEqual(single_address('$xfd$1048576'),'XFD1048576')
        self.assertIsNone(single_address('A1:B2'))

    def test_reordered_headers(self):
        result=self.analyze('学号\t班级\t姓名\t联系方式')
        self.assertEqual(result['suggested_row'],1)
        self.assertEqual([c['key'] for c in result['rows'][0]['columns']],['学号','班级','姓名',None])

    def test_title_and_shifted_start(self):
        result=self.analyze('报名登记\t\t\n姓名\t学号\t班级',start='C4')
        self.assertEqual(result['suggested_row'],5)
        self.assertEqual(result['rows'][1]['columns'][0]['address'],'C5')

    def test_multiple_candidates_require_selection(self):
        self.assertIsNone(self.analyze('姓名\t学号\n姓名\t班级')['suggested_row'])

    def test_custom_field_and_sensitive(self):
        result=self.analyze('姓名\t宿舍\t密码',{'custom_fields':{'宿舍':'三舍'}})
        self.assertEqual(result['rows'][0]['columns'][1]['key'],'custom:宿舍')
        self.assertIsNone(result['rows'][0]['columns'][2]['key'])

    def test_formula_cannot_match_custom(self):
        result=self.analyze('=姓名\t姓名',{'custom_fields':{'=姓名':'x'}})
        self.assertIsNone(result['rows'][0]['columns'][0]['key'])

    def test_duplicates_flagged(self):
        result=self.analyze('姓名\t姓名\t学号')
        self.assertIn('重复',result['rows'][0]['columns'][0]['state'])

    def test_unicode_quoted_multiline(self):
        bounds,matrix=parse_pasted_region('"备注\n😀"\t姓名\n\t学号\n')
        self.assertEqual(matrix[0][0],'备注\n😀');self.assertEqual(bounds.address,'A1:B2')

    def test_empty_unknown_rows(self):
        result=analyze_headers(parse_range('A1:B2'),[['',''],['','']],{})
        self.assertEqual(result['status'],'empty')
        self.assertEqual(self.analyze('活动\t介绍')['candidates'],[])

    def test_invalid_paste(self):
        for text in ('','\x00','x'*4001,'\t'.join(['x']*27),'\n'.join(['x']*21),'"bad'):
            with self.subTest(text=text[:10]),self.assertRaises(ValueError):parse_pasted_region(text)

    def test_incomplete_matrix(self):
        for matrix in ([],[['姓名']],[[None,'学号']]):
            with self.assertRaises(ValueError):analyze_headers(parse_range('A1:B1'),matrix,{})

HTML='''<title>本地测试</title><input aria-label="地址" value="A2">
<div role="tablist" aria-label="工作表"><div role="tab" aria-selected="true">工作表1</div></div>
<div id="alloy-simple-text-editor" role="combobox" contenteditable="true"></div>
<script>
const data={A1:'学号',B1:'姓名',C1:'班级',A2:'234'};
const box=document.querySelector('input'), editor=document.querySelector('[role=combobox]');
window.navigations=[];
box.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();
window.navigations.push(box.value);editor.innerText=data[box.value]||'';}});
</script>'''

class LocalReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(channel='msedge',headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()
    def setUp(self):
        self.page=self.browser.new_page();self.addCleanup(self.page.close)
        # An isolated, entirely intercepted page; no real document or login is used.
        self.page.route('**/*',lambda r:r.fulfill(body=HTML,content_type='text/html; charset=utf-8'))
        self.page.goto(URL)
    def test_reads_arbitrary_order_and_keeps_existing_data(self):
        data=read_header_region(self.page,URL,'A1:C2')
        self.assertEqual(data['matrix'],[['学号','姓名','班级'],['234','','']])
        self.assertEqual(self.page.locator('input').input_value(),'A2')
        self.assertEqual(self.page.evaluate('data.A2'),'234')
    def test_other_document_or_tab_rejected(self):
        for url in (URL.replace('HeaderLocalTest','OtherTest'),URL.replace('tab=Test','tab=Other')):
            with self.assertRaises(UserFacingError):read_header_region(self.page,url,'A1:B1')
        self.assertEqual(self.page.evaluate('navigations'),[])
    def test_cancel_before_navigation(self):
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1',lambda:True)
        self.assertEqual(self.page.evaluate('navigations'),[])
    def test_editor_focus_rejected(self):
        self.page.locator('[role=combobox]').focus()
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1')
        self.assertEqual(self.page.evaluate('navigations'),[])
    def test_duplicate_address_box_rejected(self):
        self.page.evaluate("document.body.insertAdjacentHTML('beforeend','<input value=A1>')")
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1')
    def test_changed_sheet_discards_result(self):
        self.page.evaluate("box.addEventListener('keydown',e=>{if(e.key==='Enter')document.querySelector('[role=tab]').innerText='另一工作表';})")
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1')
        self.assertEqual(self.page.evaluate('navigations'),['A1'])
    def test_timeout_without_navigation(self):
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1',timeout=0)
        self.assertEqual(self.page.evaluate('navigations'),[])
    def test_missing_formula_structure_rejected(self):
        self.page.locator('[role=combobox]').evaluate('(e)=>e.remove()')
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1')
    def test_editing_during_navigation_stops_without_restoration(self):
        self.page.evaluate("box.addEventListener('keydown',e=>{if(e.key==='Enter')editor.focus();})")
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1')
        self.assertEqual(self.page.evaluate('navigations'),['A1'])
    def test_selection_changes_stops_without_restoration(self):
        self.page.evaluate("box.addEventListener('keydown',e=>{if(e.key==='Enter')box.value='D4';})")
        with self.assertRaises(UserFacingError):read_header_region(self.page,URL,'A1:B1')
        self.assertEqual(self.page.evaluate('navigations'),['A1'])

class WorkerDispatch(unittest.TestCase):
    def setUp(self):
        from app import App
        import logging
        from diagnostics import ErrorRateLimiter
        self.app=App.__new__(App);self.app.sheet_requests=queue.Queue(maxsize=1)
        self.app.quit_event=threading.Event();self.app.processing=threading.Event();self.app.processing.set()
        self.app.logger=logging.getLogger('header-test-null');self.app.logger.addHandler(logging.NullHandler())
        self.app.error_limiter=ErrorRateLimiter()
        self.req={'url':URL,'range':'A1:B1','created':time.monotonic(),'cancel':threading.Event(),'reply':queue.Queue(maxsize=1)}
        self.app.sheet_requests.put(self.req)
    def test_missing_page_gives_actionable_error(self):
        self.app.read_sheet_request({})
        self.assertIn('重新排队',self.req['reply'].get_nowait()['error'])
    def test_cancelled_request_never_calls_reader(self):
        self.req['cancel'].set()
        with patch('providers.tencent_sheet.read_header_region') as reader:
            self.app.read_sheet_request({});reader.assert_not_called()
        self.assertIn('取消',self.req['reply'].get_nowait()['error'])
    def test_reply_sent_once_and_queue_drained(self):
        from unittest.mock import Mock
        self.app.store=Mock();self.app.store.actionable.return_value=True
        page=Mock();page.is_closed.return_value=False
        with patch('providers.tencent_sheet.read_header_region',return_value={'matrix':[['姓名','学号']]}) as reader:
            self.app.read_sheet_request({URL:{'page':page}})
            self.app.read_sheet_request({URL:{'page':page}})
            reader.assert_called_once()
        self.assertEqual(self.req['reply'].qsize(),1)

class HeaderUI(unittest.TestCase):
    def test_manual_preview_cancel_and_context_button(self):
        from app import App
        from sheet_header_ui import show_headers
        from fill_preview import update_preview_button
        from test_attachment_ui import descendants
        from tkinter import ttk
        with tempfile.TemporaryDirectory() as tmp:
            root=tk.Tk();root.withdraw();app=App(root,Path(tmp));app.processing.clear()
            try:
                app.table.insert('','end',iid=URL,values=('测试',));app.table.selection_set(URL)
                update_preview_button(app);self.assertEqual(app.preview_button.cget('text'),'表头读取预览')
                show_headers(app);win=app.sheet_header_window
                root.clipboard_clear();root.clipboard_append('学号\t姓名\t联系方式');root.update()
                buttons={w.cget('text'):w for w in descendants(win) if isinstance(w,ttk.Button)}
                buttons['粘贴范围分析'].invoke();root.update()
                table=next(w for w in descendants(win) if isinstance(w,ttk.Treeview))
                self.assertEqual(len(table.get_children()),3)
                buttons['读取网页表头'].invoke();root.update()
                self.assertEqual(len(table.get_children()),0)
                self.assertTrue(win.winfo_exists());win.destroy();root.update()
            finally:
                app.quit_event.set();root.destroy()
                for handler in app.logger.handlers[:]:handler.close();app.logger.removeHandler(handler)

if __name__=='__main__':unittest.main()
