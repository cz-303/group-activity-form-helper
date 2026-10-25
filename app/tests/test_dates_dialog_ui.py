import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import SAMPLE
from task_store import TaskStore
from dates_dialog_ui import show_dates_dialog
from detail_header_ui import SummaryLabel
from design_tokens import dp
from ui_layout import centered_position, work_area


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class DatesDialog(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.tk.call('tk', 'scaling', 96 / 72)
        self.root.geometry('900x620+180+100')
        self.root.update()
        self.store = TaskStore(Path(self.temp.name) / 'tasks.sqlite3')
        self.store.add_many([SAMPLE])
        self.theme = 'light'
        self.app = SimpleNamespace(root=self.root, store=self.store,
                                   get_config=lambda: {'theme': self.theme},
                                   emit=Mock(), refresh_table=Mock())

    def open(self, title=None):
        row = dict(self.store.task(SAMPLE))
        if title is not None:
            row['title'] = title
        win = show_dates_dialog(self.app, SAMPLE, row)
        self.root.after(100, self.root.quit)
        self.root.mainloop()
        self.root.update_idletasks()
        return win

    def entries(self, win):
        return [widget for widget in descendants(win) if isinstance(widget, ttk.Entry)]

    def button(self, win, name):
        return next(widget for widget in descendants(win)
                    if isinstance(widget, ttk.Button) and widget.cget('text') == name)

    def test_centered_and_empty_reference_is_read_only(self):
        win = self.open()
        rect = (self.root.winfo_rootx(), self.root.winfo_rooty(), self.root.winfo_width(), self.root.winfo_height())
        expected = centered_position(rect, (win.winfo_width(), win.winfo_height()), work_area(self.root), dp(win, 16))
        self.assertEqual((win.winfo_x(), win.winfo_y()), expected)
        text = next(widget for widget in descendants(win) if isinstance(widget, tk.Text))
        self.assertEqual(text.cget('state'), 'disabled')
        self.assertIn('未找到时间相关文字', text.get('1.0', 'end'))

    def test_long_unicode_title_and_small_window_keep_actions_visible(self):
        title = '中秋活动🌙\n' * 1000
        for theme in ('light', 'dark'):
            self.theme = theme
            for scale in (1, 1.25, 1.5, 2):
                self.root.tk.call('tk', 'scaling', scale * 96 / 72)
                win = self.open(title)
                win.geometry(f'{dp(win, 480)}x{dp(win, 400)}')
                self.root.after(100, self.root.quit)
                self.root.mainloop()
                self.root.update_idletasks()
                heading = next(widget for widget in descendants(win) if isinstance(widget, SummaryLabel))
                self.assertEqual(heading.source.get(), title)
                self.assertNotIn('\n', heading.cget('text'))
                for widget in self.entries(win) + [self.button(win, '保存时间'), self.button(win, '取消')]:
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), win.winfo_rootx() + win.winfo_width())
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), win.winfo_rooty() + win.winfo_height())
                win.destroy()

    def test_invalid_date_keeps_window_and_input(self):
        win = self.open()
        entry = self.entries(win)[0]
        entry.insert(0, '不是日期🌙')
        with patch('dates_dialog_ui.messagebox.showerror') as error:
            self.button(win, '保存时间').invoke()
        error.assert_called_once()
        self.assertIs(error.call_args.kwargs['parent'], win)
        self.assertTrue(win.winfo_exists())
        self.assertEqual(entry.get(), '不是日期🌙')
        self.assertEqual(self.store.task(SAMPLE)['event_start'], '')
        self.app.emit.assert_not_called()

    def test_blank_fields_clear_dates_and_emit_success(self):
        self.store.save_dates(SAMPLE, '2026-10-10 15:00', '', '2026-10-09')
        win = self.open()
        for entry in self.entries(win):
            entry.delete(0, 'end')
        self.button(win, '保存时间').invoke()
        self.assertFalse(win.winfo_exists())
        row = self.store.task(SAMPLE)
        self.assertEqual((row['event_start'], row['event_end'], row['deadline']), ('', '', ''))
        self.app.emit.assert_called_once()
        self.assertEqual(self.app.emit.call_args.kwargs['state'], 'success')
        self.app.refresh_table.assert_called_once()

    def test_cancel_does_not_save(self):
        win = self.open()
        self.entries(win)[0].insert(0, '2026-10-10')
        self.button(win, '取消').invoke()
        self.assertEqual(self.store.task(SAMPLE)['event_start'], '')
        self.app.emit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
