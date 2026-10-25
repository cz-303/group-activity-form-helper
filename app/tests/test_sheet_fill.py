import sys,unittest,json,tempfile,threading
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sheet_headers import parse_range
from sheet_fill import make_sheet_plan,validate_cell_value,validate_approval
from providers.tencent_sheet_fill import apply_sheet_plan
from providers.tencent_sheet import editing_active
from playwright.sync_api import sync_playwright
URL='https://docs.qq.com/sheet/FillLocalTest?tab=A'
PROFILE={'姓名':'测试同学','学号':'20260001','班级':'测试班2601','custom_fields':{'宿舍':'三舍'}}

def data(matrix=None):
    return {'bounds':parse_range('A1:E3'),'matrix':matrix or [['班级','姓名','学号','宿舍','联系方式'],['234','','','',''],['','','','','']],
            'worksheet':'工作表1','document_title':'测试表','page_bound':True}

class Plan(unittest.TestCase):
    def test_order_and_custom_mapping(self):
        p=make_sheet_plan(data(),PROFILE,1)
        self.assertEqual(p['target_row'],3)
        self.assertEqual([c['value'] for c in p['cells']],['测试班2601','测试同学','20260001','三舍',''])
        self.assertFalse(p['cells'][-1]['allowed'])
    def test_global_profile_not_modified(self):
        before=json.dumps(PROFILE,sort_keys=True);make_sheet_plan(data(),PROFILE,1)
        self.assertEqual(json.dumps(PROFILE,sort_keys=True),before)
    def test_empty_values_skip_and_unknown_block(self):
        p=make_sheet_plan(data(),PROFILE,1)
        self.assertEqual(validate_approval(p,{'A3':'','B3':'x'}),{'B3':'x'})
        with self.assertRaises(ValueError):validate_approval(p,{'E3':'x'})
        with self.assertRaises(ValueError):validate_approval(p,{'B3':''})
    def test_formula_format_and_invalid_input(self):
        for v in ('=1',' +1','-1','@x','0001','1234567890123456','x\ny','x\ty','x\x00y','x'*4001,None):
            with self.subTest(v=str(v)[:20]),self.assertRaises(ValueError):validate_cell_value(v)
        for v in ('😀','示例同学','2026123456','13812345678','测试班2601'):
            self.assertEqual(validate_cell_value(v),v)
    def test_token_changes_with_profile_matrix_and_header(self):
        p=make_sheet_plan(data(),PROFILE,1)
        profile=dict(PROFILE,姓名='其他同学');self.assertNotEqual(p['token'],make_sheet_plan(data(),profile,1)['token'])
        d=data();d['matrix'][1][0]='changed';self.assertNotEqual(p['token'],make_sheet_plan(d,PROFILE,1)['token'])
    def test_invalid_candidate_cannot_plan(self):
        d=data();d['matrix'][2][0]='occupied'
        with self.assertRaises(ValueError):make_sheet_plan(d,PROFILE,1)

HTML='''<meta charset=utf-8><title>测试表</title><input value=A2>
<div role=tablist aria-label=工作表><div role=tab aria-selected=true>工作表1</div></div>
<div id=alloy-simple-text-editor role=combobox contenteditable=true></div>
<div class=table-input-stage style="max-width:0px;max-height:0px;overflow:hidden"><div id=alloy-rich-text-editor role=combobox contenteditable=true></div></div>
<script>
const data={A1:'班级',B1:'姓名',C1:'学号',D1:'宿舍',E1:'联系方式',A2:'234'};
const box=document.querySelector('input'), editor=document.querySelector('#alloy-simple-text-editor');
window.writes=[];window.fail=false;window.corrupt=false;
box.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();editor.innerText=data[box.value]||'';}});
editor.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();
 if(!window.fail){data[box.value]=window.corrupt?'错误内容':editor.innerText;window.writes.push(box.value);}
 box.focus();}});
</script>'''

class Writes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(channel='msedge',headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()
    def setUp(self):
        self.page=self.browser.new_page();self.addCleanup(self.page.close)
        self.page.route('**/*',lambda r:r.fulfill(body=HTML,content_type='text/html; charset=utf-8'))
        self.page.goto(URL);self.plan=make_sheet_plan(data(),PROFILE,1)
    def apply(self,values=None,**kwargs):return apply_sheet_plan(self.page,URL,PROFILE,self.plan,values or {'B3':'测试同学'},confirmed_layout=True,**kwargs)
    def test_writes_and_verifies_only_approved_cells(self):
        r=self.apply({'A3':'测试班2601','B3':'测试同学','C3':'20260001'})
        self.assertEqual(r['written'],['A3','B3','C3'])
        self.assertEqual(self.page.evaluate('data.A2'),'234')
        self.assertEqual(self.page.evaluate('data.D3'),None)
    def test_manual_structure_confirmation_required(self):
        with self.assertRaises(ValueError):apply_sheet_plan(self.page,URL,PROFILE,self.plan,{'B3':'x'})
        self.assertEqual(self.page.evaluate('writes'),[])
    def test_clipboard_unbound_plan_cannot_write(self):
        d=data();d['page_bound']=False;plan=make_sheet_plan(d,PROFILE,1)
        with self.assertRaises(ValueError):apply_sheet_plan(self.page,URL,PROFILE,plan,{'B3':'x'},True)
        self.assertEqual(self.page.evaluate('writes'),[])
    def test_changed_row_does_not_overwrite(self):
        self.page.evaluate("data.B3='其他人'")
        with self.assertRaises(Exception):self.apply()
        self.assertEqual(self.page.evaluate('data.B3'),'其他人');self.assertEqual(self.page.evaluate('writes'),[])
    def test_changed_header_stops(self):
        self.page.evaluate("data.B1='其他表头'")
        with self.assertRaises(Exception):self.apply()
        self.assertEqual(self.page.evaluate('writes'),[])
    def test_changed_profile_stops(self):
        with self.assertRaises(Exception):apply_sheet_plan(self.page,URL,dict(PROFILE,姓名='已变更'),self.plan,{'B3':'x'},True)
        self.assertEqual(self.page.evaluate('writes'),[])
    def test_cancelled_does_not_write(self):
        with self.assertRaises(Exception):self.apply(cancelled=lambda:True)
        self.assertEqual(self.page.evaluate('writes'),[])
    def test_failed_or_corrupt_save_is_not_reported_success(self):
        self.page.evaluate('window.corrupt=true')
        with self.assertRaises(Exception):self.apply()
        self.assertEqual(self.page.evaluate('writes'),['B3'])
    def test_partial_failure_does_not_continue_or_rollback(self):
        self.page.evaluate("editor.addEventListener('keydown',e=>{if(e.key==='Enter'&&box.value==='A3')data.B3='并发内容';})")
        with self.assertRaises(Exception):self.apply({'A3':'测试班2601','B3':'测试同学','C3':'20260001'})
        self.assertEqual(self.page.evaluate('writes'),['A3'])
        self.assertEqual(self.page.evaluate('data.B3'),'并发内容')
    def test_normal_clipped_grid_focus_allowed_actual_edit_blocked(self):
        self.page.locator('#alloy-rich-text-editor').focus();self.assertFalse(editing_active(self.page))
        self.page.locator('.table-input-stage').evaluate("e=>e.style.maxWidth='100px'")
        self.assertTrue(editing_active(self.page))

class ApprovalUI(unittest.TestCase):
    def test_layout_required_one_use_and_clear_old_snapshot(self):
        import tkinter as tk
        from tkinter import ttk
        from app import App
        from sheet_fill_ui import show_sheet_fill
        from test_attachment_ui import descendants
        with tempfile.TemporaryDirectory() as tmp:
            root=tk.Tk();root.withdraw();app=App(root,Path(tmp));app.processing.set()
            app.browser_thread=Mock();app.browser_thread.is_alive.return_value=True
            dispatched=Mock()
            try:
                show_sheet_fill(app,URL,data(),PROFILE,1,on_dispatch=dispatched)
                win=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
                controls=list(descendants(win));button=next(w for w in controls if isinstance(w,ttk.Button) and w.cget('text').startswith('确认写入'))
                button.invoke();self.assertTrue(app.sheet_requests.empty())
                next(w for w in controls if isinstance(w,ttk.Checkbutton)).invoke()
                button.invoke();request=app.sheet_requests.get_nowait()
                self.assertEqual(request['action'],'write');self.assertTrue(request['confirmed_layout']);dispatched.assert_called_once()
                button.invoke();self.assertTrue(app.sheet_requests.empty())
                request['reply'].put({'written':['B3']});root.update()
                # Destroy cancels the outstanding callback and pending request.
                win.destroy();root.update()
            finally:
                app.quit_event.set();root.destroy()
                for handler in app.logger.handlers[:]:handler.close();app.logger.removeHandler(handler)

class WriteWorker(unittest.TestCase):
    def test_duplicate_request_cannot_write_again(self):
        import queue,time,logging
        from app import App
        from diagnostics import ErrorRateLimiter
        app=App.__new__(App);app.sheet_requests=queue.Queue(maxsize=1)
        app.processing=threading.Event();app.processing.set();app.quit_event=threading.Event()
        app.logger=logging.getLogger('sheet-worker-test');app.error_limiter=ErrorRateLimiter()
        app.get_config=lambda:PROFILE;app.store=Mock();app.store.actionable.return_value=True
        page=Mock();page.is_closed.return_value=False
        request={'url':URL,'action':'write','created':time.monotonic(),'cancel':threading.Event(),
                 'reply':queue.Queue(maxsize=1),'plan':make_sheet_plan(data(),PROFILE,1),
                 'approved':{'B3':'测试同学'},'confirmed_layout':True}
        with patch('providers.tencent_sheet_fill.apply_sheet_plan',return_value={'written':['B3']}) as writer:
            app.sheet_requests.put(request);app.read_sheet_request({URL:{'page':page}});request['reply'].get_nowait()
            app.sheet_requests.put(request);app.read_sheet_request({URL:{'page':page}})
            writer.assert_called_once()
        self.assertIn('已经使用',request['reply'].get_nowait()['error'])

if __name__=='__main__':unittest.main()
