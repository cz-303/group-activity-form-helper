"""Exercise real Tk dialogs/queues with local synthetic read replies, no web writes."""
import copy
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from sheet_header_ui import show_headers
from test_attachment_ui import descendants
from test_sheet_fill import URL, PROFILE, data
from ui_wait import settle_view


class PreviewLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, self.temp.name)
        self.app.config.update(copy.deepcopy(PROFILE), auto_open_documents=False)
        self.app.store.add_many([URL])
        settle_view(self.app)
        self.app.table.selection_set(URL)
        self.app.processing.set()
        self.app.browser_thread = Mock()
        self.app.browser_thread.is_alive.return_value = True
        show_headers(self.app)
        self.win = self.app.sheet_header_window
        controls = list(descendants(self.win))
        self.entry = next(w for w in controls if isinstance(w, ttk.Entry))
        self.table = next(w for w in controls if isinstance(w, ttk.Treeview))
        self.buttons = {w.cget('text'): w for w in controls if isinstance(w, ttk.Button)}
        self.entry.delete(0, 'end')
        self.entry.insert(0, 'A1:E3')

    def tearDown(self):
        self.app.processing.clear()
        self.app.quit_event.set()
        self.root.destroy()
        self.temp.cleanup()

    def pump(self, condition):
        until = time.monotonic() + 3
        while time.monotonic() < until:
            self.root.update()
            if condition():
                return
            time.sleep(.005)
        self.fail('UI reply was not processed')

    def request_read(self):
        self.buttons['读取网页表头'].invoke()
        return self.app.sheet_requests.get_nowait()

    def complete_read(self, request):
        request['reply'].put(dict(data(), source='本地模拟网页'))
        self.pump(lambda: not self.buttons['读取网页表头'].instate(['disabled']))

    def test_normal_reply_remains_usable(self):
        self.complete_read(self.request_read())
        self.assertEqual(len(self.table.get_children()), 5)
        self.buttons['确认表头并检查空行'].invoke()
        self.assertFalse(self.buttons['填写预览'].instate(['disabled']))

    def test_range_edit_invalidates_an_already_open_write_preview(self):
        self.complete_read(self.request_read())
        self.buttons['确认表头并检查空行'].invoke()
        self.buttons['填写预览'].invoke()
        fill_win = next(w for w in self.root.winfo_children()
                        if isinstance(w, tk.Toplevel) and '确认写入' in w.title())
        controls = list(descendants(fill_win))
        confirm = next(w for w in controls if isinstance(w, ttk.Button)
                       and w.cget('text').startswith('确认写入'))
        self.entry.delete(0, 'end')
        self.entry.insert(0, 'A1:D3')
        next(w for w in controls if isinstance(w, ttk.Checkbutton)).invoke()
        confirm.invoke()
        self.assertTrue(self.app.sheet_requests.empty(), 'Stale preview enqueued a write')
        self.assertEqual(self.table.get_children(), ())
        self.assertTrue(self.buttons['填写预览'].instate(['disabled']))

    def test_cancel_discards_a_success_reply_already_waiting_in_queue(self):
        request = self.request_read()
        request['reply'].put(dict(data(), source='本地模拟网页'))
        self.buttons['取消读取'].invoke()
        self.pump(lambda: not self.buttons['读取网页表头'].instate(['disabled']))
        self.assertEqual(self.table.get_children(), ())
        self.assertTrue(self.buttons['填写预览'].instate(['disabled']))

    def test_range_change_discards_a_late_success_reply(self):
        request = self.request_read()
        self.root.setvar(self.entry.cget('textvariable'), 'A1:D3')
        self.complete_read(request)
        self.assertEqual(self.table.get_children(), ())
        self.assertTrue(self.buttons['填写预览'].instate(['disabled']))

    def assert_control_visible(self, widget, window):
        self.assertTrue(widget.winfo_ismapped())
        self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
        self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
        self.assertGreaterEqual(widget.winfo_rootx(), window.winfo_rootx())
        self.assertGreaterEqual(widget.winfo_rooty(), window.winfo_rooty())
        self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),
                             window.winfo_rootx()+window.winfo_width())
        self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),
                             window.winfo_rooty()+window.winfo_height())

    def settle_layout(self):
        # Width-dependent wrapping is coalesced through a 40ms resize callback.
        until = time.monotonic()+.2
        while time.monotonic()<until:
            self.root.update()
            time.sleep(.005)

    def test_minimum_header_window_keeps_actions_visible_after_row_feedback(self):
        self.root.deiconify()
        self.win.geometry('600x400')
        self.complete_read(self.request_read())
        self.buttons['确认表头并检查空行'].invoke()
        self.settle_layout()
        for name in ('读取网页表头','取消读取','确认表头并检查空行','填写预览','粘贴范围分析','关闭'):
            with self.subTest(button=name):
                self.assert_control_visible(self.buttons[name], self.win)
        self.assertGreaterEqual(self.table.winfo_height(), 40)

    def test_minimum_write_window_keeps_layout_acknowledgment_and_actions_visible(self):
        self.root.deiconify()
        self.complete_read(self.request_read())
        self.buttons['确认表头并检查空行'].invoke()
        self.buttons['填写预览'].invoke()
        fill = next(w for w in self.root.winfo_children()
                    if isinstance(w, tk.Toplevel) and '确认写入' in w.title())
        fill.geometry('600x400')
        self.settle_layout()
        for widget in descendants(fill):
            if isinstance(widget, ttk.Checkbutton) or (isinstance(widget, ttk.Button)
                    and widget.cget('text') in ('确认写入（可能立即保存）','取消／关闭')):
                self.assert_control_visible(widget, fill)
        self.assertTrue(self.app.sheet_requests.empty())


if __name__ == '__main__':
    unittest.main()
