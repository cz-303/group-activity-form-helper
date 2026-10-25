import sys,tempfile,unittest,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from ui import update_task_selection,render_detail,show_empty

class UIHierarchy(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,self.tmp.name);self.app.processing.clear()
    def tearDown(self):
        self.root.update_idletasks();self.root.destroy();self.tmp.cleanup()
    def select(self,count,state='待处理'):
        for i in range(count):self.app.table.insert('','end',iid=f'task-{i}',values=('活动',state,'未检查','未设置'))
        self.app.table.selection_set(self.app.table.get_children())
        update_task_selection(self.app)
    def test_multi_selection_never_displays_single_task_detail(self):
        self.select(2)
        render_detail(self.app,{'title':'不应显示的单项','meta':'个人信息','tabs':{'通知原文':'单项原文'}})
        self.assertEqual(self.app.detail_title.get(),'已选择 2 项任务')
        self.assertEqual(self.app.detail_tabs['通知原文'].get('1.0','end-1c'),'')
        self.assertEqual(self.app.open_button.cget('text'),'打开选中链接（2）')
        for i in (0,1,2,3):self.assertEqual(self.app.task_menu.entrycget(i,'state'),'disabled')
        self.assertEqual(self.app.task_menu.entrycget(4,'state'),'normal')
    def test_no_selection_and_single_failed_task(self):
        update_task_selection(self.app)
        self.assertTrue(self.app.open_button.instate(['disabled']))
        self.select(1,'打开失败')
        self.assertEqual(self.app.open_button.cget('text'),'重试打开')
        self.assertIn('先点击',self.app.selection_hint.get())
        self.assertEqual(self.app.task_menu.entrycget(0,'state'),'normal')
    def test_empty_actions_hidden_during_loading_and_filter_reset_works(self):
        show_empty(self.app,'没有任务','new')
        self.assertEqual(self.app.empty_import.winfo_manager(),'pack')
        self.assertEqual(self.app.empty_setup.winfo_manager(),'pack')
        self.app.empty_setup.invoke()
        notebook=self.app.settings_notebook
        self.assertEqual(notebook.tab(notebook.select(),'text'),'群聊监测')
        self.app.settings_window.destroy()
        self.app.search_var.set('没有结果')
        show_empty(self.app,'无匹配','filtered')
        self.assertEqual(self.app.empty_import.winfo_manager(),'')
        self.assertEqual(self.app.empty_reset.winfo_manager(),'pack')
        self.app.empty_reset.invoke();self.assertEqual(self.app.search_var.get(),'')
        show_empty(self.app,'正在加载')
        self.assertEqual(self.app.empty_actions.winfo_manager(),'')
    def test_monitor_stopping_disables_control_then_recovers(self):
        self.app.monitor_thread=SimpleNamespace(is_alive=lambda:True)
        self.app.monitor_stop=threading.Event();self.app.monitor_stop.set()
        self.app.refresh_monitor_status()
        self.assertEqual(self.app.monitor_button.cget('text'),'正在停止…')
        self.assertTrue(self.app.monitor_button.instate(['disabled']))
        self.app.monitor_thread=None;self.app.refresh_monitor_status()
        self.assertEqual(self.app.monitor_button.cget('text'),'开始监测')
        self.assertFalse(self.app.monitor_button.instate(['disabled']))
    def test_processing_control_reuses_existing_commands_without_rebinding(self):
        with patch.object(self.app,'resume') as resume:
            self.app.process_control_state=None;self.app.refresh_monitor_status()
            self.app.process_button.invoke();resume.assert_called_once()
        self.app.processing.set();self.app.refresh_monitor_status()
        self.assertEqual(self.app.process_button.cget('text'),'暂停处理')
        with patch.object(self.app.process_button,'configure') as configure:
            self.app.refresh_monitor_status();configure.assert_not_called()

if __name__=='__main__':unittest.main()
