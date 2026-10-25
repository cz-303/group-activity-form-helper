"""Clear-all-data (1.0): safety checks, retry on locked files, helper entry point and menu wiring."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import data_purge
from data_purge import is_app_data_dir, purge_data_dir, run_purge_helper, helper_command, request_purge


def make_install(folder, marker='app.py'):
    root = Path(folder) / '群活动填表助手'
    data = root / 'data'
    (data / 'browser' / 'Default').mkdir(parents=True)
    (data / 'backups').mkdir()
    (root / marker).write_text('', encoding='utf-8')
    (data / 'config.json').write_text('{}', encoding='utf-8')
    (data / 'tasks.sqlite3').write_bytes(b'db')
    (data / 'browser' / 'Default' / 'Cookies').write_bytes(b'c')
    (data / 'backups' / 'b.zip').write_bytes(b'z')
    return root, data


class DataDirSafety(unittest.TestCase):
    def test_only_app_data_folder_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data = make_install(folder)
            self.assertTrue(is_app_data_dir(data, root))
            self.assertFalse(is_app_data_dir(root, root))
            self.assertFalse(is_app_data_dir(data / 'backups', root))
            other = Path(folder) / 'other' / 'data'
            other.mkdir(parents=True)
            (other / 'config.json').write_text('{}', encoding='utf-8')
            self.assertFalse(is_app_data_dir(other, root))
            self.assertFalse(is_app_data_dir(other, other.parent))  # no app marker beside it

    def test_unknown_contents_are_refused_and_left_intact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'app'
            data = root / 'data'
            data.mkdir(parents=True)
            (root / 'app.py').write_text('', encoding='utf-8')
            (data / '论文.docx').write_bytes(b'x')
            self.assertFalse(is_app_data_dir(data, root))
            with self.assertRaises(ValueError):
                purge_data_dir(data, root)
            self.assertTrue((data / '论文.docx').exists())

    def test_installed_exe_marker_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data = make_install(folder, marker='群活动填表助手.exe')
            self.assertTrue(is_app_data_dir(data, root))


class Purge(unittest.TestCase):
    def test_removes_everything_including_read_only_and_keeps_siblings(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data = make_install(folder)
            read_only = data / 'backups' / 'b.zip'
            os.chmod(read_only, stat.S_IREAD)
            sibling = root / '使用说明.txt'
            sibling.write_text('keep', encoding='utf-8')
            self.assertEqual(purge_data_dir(data, root, timeout=0), [])
            self.assertFalse(data.exists())
            self.assertTrue(sibling.exists())
            self.assertTrue((root / 'app.py').exists())

    def test_missing_folder_counts_as_already_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(purge_data_dir(Path(folder) / 'data', folder), [])

    def test_locked_entries_are_retried_then_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data = make_install(folder)
            real_unlink = Path.unlink
            calls = {'n': 0}

            def flaky_unlink(self, *args, **kwargs):
                if self.name == 'tasks.sqlite3' and calls['n'] < 2:
                    calls['n'] += 1
                    raise PermissionError('locked')
                return real_unlink(self, *args, **kwargs)

            ticks = iter(range(100))
            with patch.object(Path, 'unlink', flaky_unlink):
                remaining = purge_data_dir(data, root, timeout=5, sleep=lambda _: None, clock=lambda: next(ticks))
            self.assertEqual(remaining, [])
            self.assertEqual(calls['n'], 2)
            self.assertFalse(data.exists())

    def test_timeout_reports_remaining_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data = make_install(folder)
            with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
                remaining = purge_data_dir(data, root, timeout=0, sleep=lambda _: None)
            self.assertIn('tasks.sqlite3', remaining)
            self.assertTrue((data / 'tasks.sqlite3').exists())


class Helper(unittest.TestCase):
    def test_helper_entry_point_purges_and_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data = make_install(folder)
            self.assertEqual(run_purge_helper(['app.py', '--purge-data'], root, data, quiet=True), 0)
            self.assertFalse(data.exists())

    def test_helper_refusal_returns_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'data'
            data.mkdir()
            (data / 'x.txt').write_text('x', encoding='utf-8')
            self.assertEqual(run_purge_helper(['app.py', '--purge-data'], folder, data, quiet=True), 1)
            self.assertTrue((data / 'x.txt').exists())

    def test_source_mode_command_runs_app_py_with_flag(self):
        command = helper_command(r'C:\app', 1234)
        self.assertEqual(command[-2:], ['--purge-data', '1234'])
        self.assertTrue(command[1].endswith('app.py'))


class Request(unittest.TestCase):
    def fake_app(self):
        calls = []
        return SimpleNamespace(root=None, data=Path('data'), purge_requested=False,
                               shutdown=lambda: calls.append('shutdown')), calls

    def test_declining_either_confirmation_changes_nothing(self):
        for answers in ([False], [True, False]):
            app, calls = self.fake_app()
            with patch('tkinter.messagebox.askyesno', side_effect=answers):
                self.assertFalse(request_purge(app))
            self.assertFalse(app.purge_requested)
            self.assertEqual(calls, [])

    def test_two_confirmations_request_safe_shutdown(self):
        app, calls = self.fake_app()
        with patch('tkinter.messagebox.askyesno', side_effect=[True, True]) as ask:
            self.assertTrue(request_purge(app))
        self.assertTrue(app.purge_requested)
        self.assertEqual(calls, ['shutdown'])
        self.assertEqual(ask.call_args_list[0].kwargs.get('default'), 'no')


class MenuWiring(unittest.TestCase):
    def test_more_menu_offers_clear_all_data(self):
        import tkinter as tk
        from tkinter import ttk
        from app import App

        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            app = App(root, folder)
            app.processing.clear()
            try:
                self.assertFalse(app.purge_requested)
                more = next(w for w in app.sidebar.winfo_children()
                            if isinstance(w, ttk.Menubutton) and w.cget('text') == '更多')
                menu = root.nametowidget(more.cget('menu'))
                labels = [menu.entrycget(i, 'label') for i in range(menu.index('end') + 1)]
                self.assertIn('清除全部数据并退出…', labels)
                self.assertLess(labels.index('数据清理中心'), labels.index('清除全部数据并退出…'))
            finally:
                root.update_idletasks()
                root.destroy()


if __name__ == '__main__':
    unittest.main()
