import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from ui import render_detail
from core import SAMPLE
from detail_header_ui import ellipsize,SummaryLabel,set_header,show_full_detail
from appearance import themed
from design_tokens import dp


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


class SummaryText(unittest.TestCase):
    def test_empty_tiny_and_unicode_widths(self):
        for text in ('','通知 🌙\n中文\t活动','x'*4000):
            for width in (0,1,5,20,5000):
                result=ellipsize(text,len,width)
                self.assertLessEqual(len(result),width)
                self.assertNotIn('\n',result);self.assertNotIn('\t',result)
        self.assertEqual(ellipsize(' 短\n通知 ',len,50),'短 通知')


class HeaderUI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.root.tk.call('tk','scaling',96/72)
        self.app=App(self.root,self.temp.name);self.app.processing.clear()
        self.root.after_cancel(self.app.tick_timer);self.root.after_cancel(self.app.query_timer)
        self.app.table.insert('','end',iid=SAMPLE,values=('测试活动','待处理','未检查','未设置'))
        self.app.table.selection_set(SAMPLE)
        self.detail={'title':'长标题🌙\n'*500,'meta':'来源：测试群\n'*500,
                     'tabs':{'通知原文':'原始通知，不应被修改'}}
    def tearDown(self):
        self.root.update_idletasks();self.root.destroy();self.temp.cleanup()
    def settle(self):
        self.root.after(200,self.root.quit);self.root.mainloop();self.root.update_idletasks()
    def prepare(self):
        self.root.deiconify();self.root.minsize(900,620);self.root.geometry('900x620')
        self.settle()
        render_detail(self.app,self.detail)
        self.app.list_detail.show_detail();self.settle()
    def test_long_title_and_meta_do_not_consume_body_and_full_source_is_preserved(self):
        self.prepare()
        self.assertEqual(self.app.detail_title.get(),self.detail['title'])
        self.assertEqual(self.app.detail_tabs['通知原文'].get('1.0','end-1c'),'原始通知，不应被修改')
        self.assertGreaterEqual(self.app.detail_tabs['通知原文'].winfo_height(),dp(self.root,60))
        for label in (self.app.detail_title_label,self.app.detail_meta_label):
            self.assertEqual(int(label.cget('wraplength')),0)
            if label.winfo_ismapped():
                self.assertTrue(label.display.get().endswith('…'))
                self.assertLessEqual(label.measured_font.measure(label.display.get()),label.winfo_width()-dp(self.root,8))
        self.assertEqual(self.app.detail_meta_label.winfo_manager(),'')
        self.assertTrue(self.app.info_button.winfo_ismapped())
    def test_full_window_keeps_exact_text_and_is_read_only(self):
        self.prepare();self.app.info_button.invoke()
        text=next(w for w in children(self.app.full_detail_window) if isinstance(w,tk.Text))
        self.assertEqual(text.cget('state'),'disabled')
        self.assertIn(self.detail['title'],text.get('1.0','end-1c'))
        self.assertIn(self.detail['meta'],text.get('1.0','end-1c'))
    def test_loading_or_changed_selection_cannot_open_stale_information(self):
        self.prepare();self.app.table.selection_remove(SAMPLE)
        with patch('detail_header_ui.tk.Toplevel') as window:
            show_full_detail(self.app);window.assert_not_called()
        set_header(self.app,'正在读取任务详情…','')
        self.assertIsNone(self.app.full_detail)
        self.assertEqual(self.app.info_button.winfo_manager(),'')
    def test_multiple_selection_hides_single_information(self):
        self.prepare()
        self.app.table.insert('','end',iid='second',values=('另一个活动','待处理','',''))
        self.app.table.selection_set(SAMPLE,'second');render_detail(self.app,self.detail)
        self.assertIsNone(self.app.full_detail)
        self.assertEqual(self.app.info_button.winfo_manager(),'')
    def test_source_trace_and_pending_resize_removed_when_label_destroyed(self):
        source=tk.StringVar(self.root,value='文字')
        label=SummaryLabel(self.root,source)
        self.assertEqual(len(source.trace_info()),1)
        source.set('长文字'*500);label.destroy()
        self.assertEqual(source.trace_info(),[])
        source.set('关闭后的更新');self.root.update_idletasks()
    def test_long_information_in_dark_theme_and_scaled_window(self):
        self.prepare()
        for scale in (1.25,1.5,2):
            self.root.tk.call('tk','scaling',scale*96/72)
            self.app.config['theme']='dark';themed(self.app)
            self.root.geometry(f'{round(900*scale)}x{round(620*scale)}')
            self.settle()
            self.assertGreaterEqual(self.app.detail_tabs['通知原文'].winfo_height(),dp(self.root,60))
            self.assertLessEqual(self.app.info_button.winfo_rootx()+self.app.info_button.winfo_width(),
                                 self.root.winfo_rootx()+self.root.winfo_width())
            self.assertEqual(self.app.detail_title.get(),self.detail['title'])

if __name__=='__main__':unittest.main()
