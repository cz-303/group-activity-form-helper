import sys,tempfile,unittest
from pathlib import Path
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from ui import update_task_selection,set_text
from design_tokens import dp


class ResponsiveDetails(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,self.temp.name);self.app.processing.clear()
        self.layout=self.app.list_detail
        self.app.table.insert('','end',iid='test-task',values=('测试活动','待处理','待补充','未设置'))
    def tearDown(self):
        self.root.update_idletasks();self.root.destroy();self.temp.cleanup()
    def compact(self):self.layout.update_width(dp(self.root,700))
    def selected(self):
        self.app.table.selection_set('test-task');update_task_selection(self.app)
    def test_compact_starts_with_list_and_keeps_actions_outside_hidden_pane(self):
        self.compact()
        self.assertFalse(self.layout.wide)
        self.assertEqual(tuple(map(str,self.layout.panes.panes())),(str(self.layout.list_panel),))
        self.assertEqual(self.app.details_button.cget('text'),'查看详情')
        self.assertTrue(self.app.details_button.instate(['disabled']))
        self.selected();self.app.details_button.invoke()
        self.assertEqual(tuple(map(str,self.layout.panes.panes())),(str(self.layout.detail_panel),))
        self.assertEqual(self.app.details_button.cget('text'),'返回列表')
        self.assertNotEqual(self.app.preview_button.master,self.layout.list_panel)
        self.assertNotEqual(self.app.open_button.master,self.layout.list_panel)
    def test_back_keeps_selection_detail_tab_and_text(self):
        self.compact();self.selected()
        text=self.app.detail_tabs['通知原文'];set_text(text,'保留的通知')
        notebook=text.master.master.master
        notebook.select(2)
        tab=notebook.select()
        self.app.details_button.invoke();self.app.details_button.invoke()
        self.assertEqual(self.app.table.selection(),('test-task',))
        self.assertEqual(text.get('1.0','end-1c'),'保留的通知')
        self.assertEqual(notebook.select(),tab)
        self.assertEqual(self.app.details_button.cget('text'),'查看详情')
    def test_resize_hysteresis_avoids_repeated_switching(self):
        self.compact()
        self.layout.update_width(dp(self.root,850));self.assertFalse(self.layout.wide)
        self.layout.update_width(dp(self.root,870));self.assertTrue(self.layout.wide)
        self.layout.update_width(dp(self.root,830));self.assertTrue(self.layout.wide)
        self.layout.update_width(dp(self.root,810));self.assertFalse(self.layout.wide)
    def test_wide_restore_keeps_same_widgets_and_selection(self):
        self.compact();self.selected();self.layout.show_detail()
        original=self.app.detail_tabs['通知原文']
        self.layout.update_width(dp(self.root,1000))
        self.assertEqual(len(self.layout.panes.panes()),2)
        self.assertIs(self.app.detail_tabs['通知原文'],original)
        self.assertEqual(self.app.table.selection(),('test-task',))
        self.assertEqual(self.app.details_button.winfo_manager(),'')
        self.layout.update_width(dp(self.root,700))
        self.assertEqual(self.app.details_button.cget('text'),'返回列表')
    def test_no_extra_layout_on_same_mode_and_threshold_scales(self):
        for scale in (1,1.25,1.5,2):
            self.root.tk.call('tk','scaling',scale*96/72)
            self.compact();self.assertFalse(self.layout.wide)
            self.layout.update_width(dp(self.root,1000));self.assertTrue(self.layout.wide)
        from unittest.mock import patch
        with patch.object(self.layout.panes,'forget') as forget:
            for width in range(1000,1100):self.layout.update_width(dp(self.root,width))
            forget.assert_not_called()

if __name__=='__main__':unittest.main()
