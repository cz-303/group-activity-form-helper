import sys
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from close_dialog_ui import centered_position, work_area
from design_tokens import dp


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class DialogPosition(unittest.TestCase):
    def test_center_and_edge_clamping(self):
        self.assertEqual(centered_position((200, 100, 1000, 700), (520, 320), (0, 0, 1920, 1040)), (440, 290))
        self.assertEqual(centered_position((1800, 1000, 1000, 700), (520, 320), (0, 0, 1920, 1040)), (1384, 704))
        self.assertEqual(centered_position((-100, -100, 100, 100), (520, 320), (0, 0, 1920, 1040)), (16, 16))

    def test_negative_monitor_origin(self):
        x, y = centered_position((-1800, 100, 1000, 700), (520, 320), (-1920, 0, 0, 1040))
        self.assertEqual((x, y), (-1560, 290))


class CloseDialog(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.tk.call('tk', 'scaling', 96 / 72)
        self.root.geometry('900x620+180+100')
        self.root.update()
        self.theme = 'light'
        self.app = SimpleNamespace(root=self.root, closing=False, close_window=None,
                                   get_config=lambda: {'theme': self.theme},
                                   hide_to_tray=Mock(), shutdown=Mock())

    def buttons(self):
        return {widget.cget('text'): widget for widget in descendants(self.app.close_window)
                if isinstance(widget, ttk.Button)}

    def test_centered_and_repeated_close_reuses_window(self):
        App.close(self.app)
        win = self.app.close_window
        self.root.update()
        bounds = work_area(self.root)
        expected = centered_position((self.root.winfo_rootx(), self.root.winfo_rooty(),
                                      self.root.winfo_width(), self.root.winfo_height()),
                                     (win.winfo_width(), win.winfo_height()), bounds, dp(win, 16))
        self.assertEqual((win.winfo_x(), win.winfo_y()), expected)
        App.close(self.app)
        self.assertIs(self.app.close_window, win)
        self.assertEqual(str(win.transient()), str(self.root))
        self.buttons()['取消'].invoke()
        self.app.hide_to_tray.assert_not_called()
        self.app.shutdown.assert_not_called()

    def test_actions_and_reopen_after_cancel(self):
        for action, callback in (('最小化到托盘', self.app.hide_to_tray), ('彻底退出', self.app.shutdown)):
            App.close(self.app)
            win = self.app.close_window
            self.buttons()[action].invoke()
            self.assertFalse(win.winfo_exists())
            callback.assert_called_once()

    def test_escape_only_dismisses_dialog(self):
        App.close(self.app)
        win = self.app.close_window
        win.focus_force()
        self.root.update()
        win.event_generate('<Escape>')
        self.root.update()
        self.assertFalse(win.winfo_exists())
        self.assertTrue(self.root.winfo_exists())
        self.app.hide_to_tray.assert_not_called()
        self.app.shutdown.assert_not_called()

    def test_themes_and_dpi_keep_controls_inside_dialog(self):
        for theme in ('light', 'dark'):
            self.theme = theme
            for scale in (1, 1.25, 1.5, 2):
                self.root.tk.call('tk', 'scaling', scale * 96 / 72)
                App.close(self.app)
                win = self.app.close_window
                self.root.update()
                for widget in descendants(win):
                    if widget.winfo_ismapped():
                        self.assertGreaterEqual(widget.winfo_rootx(), win.winfo_rootx())
                        self.assertGreaterEqual(widget.winfo_rooty(), win.winfo_rooty())
                        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), win.winfo_rootx() + win.winfo_width())
                        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), win.winfo_rooty() + win.winfo_height())
                self.assertEqual(self.buttons()['最小化到托盘'].cget('style'), 'Primary.TButton')
                win.destroy()

    def test_monitor_lookup_failure_uses_tk_bounds(self):
        with patch('ui_layout.ctypes.WinDLL', side_effect=OSError('test')):
            with self.assertLogs('ui_layout', level='WARNING'):
                bounds = work_area(self.root)
        self.assertGreater(bounds[2], bounds[0])
        self.assertGreater(bounds[3], bounds[1])


if __name__ == '__main__':
    unittest.main()
