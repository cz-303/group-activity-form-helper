import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from attachment_ui import show_images
from attachment_store import add_image,images
from test_document_images import qr_image,TX


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ImageWindow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='图片界面-');self.addCleanup(self.temp.cleanup)
        self.root=tk.Tk();self.root.withdraw();self.app=App(self.root,self.temp.name)
        self.root.after_cancel(self.app.tick_timer)
        self.addCleanup(lambda:(self.root.update_idletasks(),self.root.destroy()))
        self.app.store.add_many([TX]);self.key,_=add_image(self.app.store,qr_image(),'测试群')

    def test_link_confirmation_cancel_and_unlink_keep_image(self):
        show_images(self.app,TX);win=self.app.image_window;self.root.update()
        controls=list(descendants(win))
        chooser=next(w for w in controls if isinstance(w,ttk.Combobox));chooser.set('待关联');chooser.event_generate('<<ComboboxSelected>>');self.root.update()
        buttons={w.cget('text'):w for w in controls if isinstance(w,ttk.Button)}
        with patch('attachment_ui.messagebox.askyesno',return_value=False):buttons['关联到活动'].invoke()
        self.assertEqual(images(self.app.store,url=TX),[])
        with patch('attachment_ui.messagebox.askyesno',return_value=True):buttons['关联到活动'].invoke()
        self.assertEqual(images(self.app.store,url=TX)[0]['id'],self.key)
        buttons['解除关联'].invoke();self.assertEqual(images(self.app.store,url=TX),[])
        self.assertEqual(images(self.app.store,pending=True)[0]['id'],self.key)

    def test_delete_cancel_empty_state_and_window_reuse(self):
        show_images(self.app);win=self.app.image_window;self.root.update();show_images(self.app)
        self.assertIs(self.app.image_window,win)
        buttons={w.cget('text'):w for w in descendants(win) if isinstance(w,ttk.Button)}
        with patch('attachment_ui.messagebox.askyesno',return_value=False):buttons['删除图片'].invoke()
        self.assertEqual(len(images(self.app.store)),1)
        with patch('attachment_ui.messagebox.askyesno',return_value=True):buttons['删除图片'].invoke()
        self.assertEqual(images(self.app.store),[])
        labels=[str(w.cget('text')) for w in descendants(win) if isinstance(w,ttk.Label)]
        self.assertIn('当前范围没有图片',labels)

    def test_database_failure_is_local_feedback(self):
        show_images(self.app);self.root.update()
        buttons={w.cget('text'):w for w in descendants(self.app.image_window) if isinstance(w,ttk.Button)}
        with patch('attachment_ui.images',side_effect=PermissionError()):buttons['刷新'].invoke()
        labels=[str(w.cget('text')) for w in descendants(self.app.image_window) if isinstance(w,ttk.Label)]
        self.assertTrue(any('没有写入权限' in text for text in labels))


if __name__=='__main__':unittest.main()
