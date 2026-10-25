import sys,tempfile,unittest,time
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from appearance import themed


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


class TaskSettingsUI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,self.temp.name)
        self.app.processing.clear()
        self.app.config.update(max_tabs=5,max_attempts=4,retry_seconds=45,custom_fields={'专业':'测试专业'})
        self.original=self.app.get_config()
        self.app.open_settings()
        notebook=self.app.settings_notebook
        notebook.select(2);notebook.ensure_selected()
        controls=list(children(self.app.settings_window))
        self.advanced=next(w for w in controls if w.winfo_name()=='task_advanced')
        self.rules=next(w for w in controls if w.winfo_name()=='task_rules')
        self.entries=[w for w in children(self.advanced) if isinstance(w,ttk.Entry)]
    def tearDown(self):
        self.root.update_idletasks();self.root.destroy();self.temp.cleanup()
    def button(self,text):
        return next(w for w in children(self.app.settings_window) if isinstance(w,ttk.Button) and w.cget('text')==text)
    def settle(self):
        self.root.after(100,self.root.quit);self.root.mainloop()
    def settle_region(self,canvas):
        self.root.update_idletasks()
        deadline=time.monotonic()+1.5
        def poll():
            region=tuple(map(float,canvas.cget('scrollregion').split()))
            bbox=tuple(map(float,canvas.bbox('all')))
            if region==bbox or time.monotonic()>=deadline:self.root.quit()
            else:self.root.after(10,poll)
        self.root.after(10,poll);self.root.mainloop()
    def test_basic_controls_visible_and_advanced_initially_collapsed(self):
        self.assertEqual(self.advanced.winfo_manager(),'')
        self.assertEqual(self.rules.winfo_manager(),'')
        self.assertEqual([w.get() for w in self.entries],['5','4','45'])
        checks=[w for w in children(self.app.settings_window) if isinstance(w,ttk.Checkbutton)]
        self.assertEqual(len(checks),3)
        self.assertTrue(all(int(w.grid_info()['row'])<5 for w in checks))
    def test_edit_survives_fold_switch_and_save(self):
        self.button('展开高级选项').invoke()
        self.entries[0].delete(0,'end');self.entries[0].insert(0,'6')
        self.button('收起高级选项').invoke()
        notebook=self.app.settings_notebook
        notebook.select(0);notebook.ensure_selected()
        notebook.select(2);notebook.ensure_selected()
        self.button('展开高级选项').invoke()
        self.assertEqual(self.entries[0].get(),'6')
        self.button('收起高级选项').invoke()
        self.button('保存设置').invoke()
        expected=dict(self.original,max_tabs=6)
        self.assertEqual(self.app.get_config(),expected)
    def test_unchanged_collapsed_values_preserved(self):
        self.button('保存设置').invoke()
        self.assertEqual(self.app.get_config(),self.original)
    def test_cancel_does_not_write(self):
        self.button('展开高级选项').invoke()
        self.entries[0].delete(0,'end');self.entries[0].insert(0,'6')
        self.button('取消').invoke()
        self.assertEqual(self.app.get_config(),self.original)
    def test_invalid_collapsed_edit_rejected_without_losing_input(self):
        self.button('展开高级选项').invoke()
        self.entries[0].delete(0,'end');self.entries[0].insert(0,'100')
        self.button('收起高级选项').invoke()
        with patch('app.messagebox.showerror') as error:
            self.button('保存设置').invoke();error.assert_called_once()
        self.assertTrue(self.app.settings_window.winfo_exists())
        self.assertEqual(self.app.get_config(),self.original)
        self.assertEqual(self.entries[0].get(),'100')
    def test_expansion_updates_scroll_region_and_dark_theme(self):
        self.settle()
        canvas=self.advanced.master.master
        before=tuple(map(float,canvas.cget('scrollregion').split()))
        self.button('展开高级选项').invoke()
        self.button('展开打开与重试规则').invoke()
        self.settle_region(canvas)
        expanded=tuple(map(float,canvas.cget('scrollregion').split()))
        self.assertGreater(expanded[3],before[3])
        self.assertEqual(expanded,tuple(map(float,canvas.bbox('all'))))
        self.app.config['theme']='dark';themed(self.app,self.app.settings_window)
        from design_tokens import PALETTES;self.assertEqual(canvas.cget('background'),PALETTES['dark']['surface'])
        self.assertEqual(self.entries[0].get(),'5')
        self.button('收起高级选项').invoke()
        self.button('收起打开与重试规则').invoke()
        self.settle_region(canvas)
        self.assertLess(float(canvas.cget('scrollregion').split()[3]),expanded[3])

if __name__=='__main__':unittest.main()
