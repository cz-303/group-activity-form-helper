from ui_wait import settle_view
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from settings import validate_settings, load_settings, save_settings
from storage_io import atomic_text, spreadsheet_cell
from core import DEFAULTS, SAMPLE
from task_store import TaskStore
from backup_support import make_backup, inspect_backup, stage_restore, apply_pending
from diagnostics import create_logger, record_error, close_logger


class Configuration(unittest.TestCase):
    def test_release_has_blank_identity_and_paused_default(self):
        self.assertTrue(all(not DEFAULTS[k] for k in ('姓名', '学号', '班级', '学院', '联系电话', 'group', 'client_path')))
        self.assertFalse(validate_settings({})['auto_resume'])

    def test_bad_types_do_not_become_valid_text_or_enabled_switch(self):
        for value in ([], None, 'config', {'auto_resume':'false'}, {'姓名':None},
                      {'max_tabs':True}, {'poll_seconds':3.5}, {'theme':[]}, {'deadline_minutes':[True]}):
            with self.subTest(value=value), self.assertRaises(ValueError):validate_settings(value)

    def test_unicode_and_no_shared_mutable_defaults(self):
        first=validate_settings({'姓名':'测试同学🌙', 'custom_fields':{'研究方向':'生态🌿'}})
        first['deadline_minutes'].clear()
        self.assertEqual(validate_settings({})['deadline_minutes'], [60,30])
        with tempfile.TemporaryDirectory(prefix='中文用户-') as folder:
            path=Path(folder)/'中文配置.json';save_settings(path,first | {'deadline_minutes':[30]})
            loaded,warning=load_settings(path)
            self.assertFalse(warning);self.assertEqual(loaded['姓名'],'测试同学🌙')

    def test_corrupt_configuration_preserved_and_processing_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json';path.write_bytes(b'{broken')
            config,warning=load_settings(path)
            self.assertTrue(warning);self.assertFalse(config['auto_resume']);self.assertEqual(path.read_bytes(),b'{broken')

    def test_permission_failure_does_not_truncate_previous_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json';save_settings(path,{})
            before=path.read_bytes()
            with patch('storage_io.os.replace',side_effect=PermissionError('private path')):
                with self.assertRaises(PermissionError):save_settings(path,{'姓名':'new'})
            self.assertEqual(path.read_bytes(),before);self.assertEqual(len(list(Path(folder).iterdir())),1)


class DataSafety(unittest.TestCase):
    def test_csv_formula_whitespace_and_emoji(self):
        for value in ('=1+1',' +SUM(A1)','\t=1','\n@bad',' -2'):
            self.assertTrue(spreadsheet_cell(value).startswith("'"))
        self.assertEqual(spreadsheet_cell('中秋🌙'),'中秋🌙')

    def test_interrupted_export_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'名单.csv';path.write_bytes(b'original')
            with self.assertRaises(RuntimeError):
                with atomic_text(path) as stream:
                    stream.write('partial');raise RuntimeError('fail')
            self.assertEqual(path.read_bytes(),b'original')

    def test_inspection_store_does_not_reset_active_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tasks.sqlite3';first=TaskStore(path);first.add_many([SAMPLE]);first.claim()
            second=TaskStore(path);self.assertEqual(second.state(SAMPLE),'opening')
            second.recover_inflight();self.assertEqual(first.state(SAMPLE),'pending')

    def test_diagnostics_exclude_values_paths_and_error_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            logger=create_logger(folder)
            try:raise ValueError('测试秘密学号 C:/Users/private https://example.invalid/token')
            except ValueError as error:record_error(logger,'unit-test',error)
            close_logger(logger)
            log=(Path(folder)/'diagnostics.log').read_text(encoding='utf-8')
            self.assertIn('ValueError',log)
            for secret in ('秘密','private','https:',folder):self.assertNotIn(secret,log)


class Restore(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)
        self.store=TaskStore(self.data/'tasks.sqlite3');self.store.add_many([SAMPLE])
        self.archive=make_backup(self.store,self.data,validate_settings({}),'manual')
    def tearDown(self):self.temp.cleanup()

    def rewrite(self,sql):
        with ZipFile(self.archive) as archive:files={n:archive.read(n) for n in archive.namelist()}
        dbfile=self.data/'tampered.sqlite3';dbfile.write_bytes(files['tasks.sqlite3'])
        db=sqlite3.connect(dbfile)
        try:
            with db:db.execute(sql)
        finally:db.close()
        files['tasks.sqlite3']=dbfile.read_bytes()
        manifest=json.loads(files['manifest.json']);manifest['sha256']['tasks.sqlite3']=hashlib.sha256(files['tasks.sqlite3']).hexdigest()
        files['manifest.json']=json.dumps(manifest).encode()
        with ZipFile(self.archive,'w',ZIP_DEFLATED) as archive:
            for name,content in files.items():archive.writestr(name,content)

    def test_recomputed_hash_does_not_bypass_business_validation(self):
        self.rewrite("UPDATE tasks SET deadline='2026-99-99'")
        with self.assertRaises(ValueError):stage_restore(self.archive,self.data,validate_settings)
        self.assertFalse((self.data/'restore_pending.zip').exists());self.assertEqual(self.store.task(SAMPLE)['deadline'],'')

    def test_invalid_json_shape_refused(self):
        self.rewrite("UPDATE tasks SET checklist='{}'")
        with self.assertRaises(ValueError):inspect_backup(self.archive,validate_settings)

    def test_triggers_refused_before_migration(self):
        self.rewrite("CREATE TRIGGER unexpected AFTER UPDATE ON tasks BEGIN DELETE FROM tasks; END")
        with self.assertRaises(ValueError):inspect_backup(self.archive,validate_settings)

    def test_broken_current_config_does_not_block_valid_restore(self):
        (self.data/'config.json').write_bytes(b'{broken')
        stage_restore(self.archive,self.data,validate_settings);apply_pending(self.data,validate_settings)
        self.assertFalse(load_settings(self.data/'config.json')[1])
        preserved=list((self.data/'backups').glob('config-invalid-*.json'))
        self.assertEqual(len(preserved),1);self.assertEqual(preserved[0].read_bytes(),b'{broken')

    def test_failed_install_rolls_back_original_pair(self):
        self.store.add_many(['https://docs.qq.com/form/page/Second'])
        save_settings(self.data/'config.json', {'姓名':'恢复前'})
        stage_restore(self.archive,self.data,validate_settings)
        from storage_io import atomic_bytes
        failed=[False]
        def fail_once(path,content):
            if Path(path)==self.data/'config.json' and not failed[0]:
                failed[0]=True;raise PermissionError('file in use')
            return atomic_bytes(path,content)
        with patch('backup_support.atomic_bytes', side_effect=fail_once):
            with self.assertRaises(PermissionError):apply_pending(self.data,validate_settings)
        self.assertEqual(len(self.store.rows()),2)
        self.assertEqual(load_settings(self.data/'config.json')[0]['姓名'],'恢复前')


class WindowLifecycle(unittest.TestCase):
    def test_small_window_keeps_status_preview_and_save_accessible(self):
        import tkinter as tk
        from tkinter import ttk
        from app import App
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();app=App(root,folder)
            try:
                root.geometry('1000x700');settle_view(app);root.update();root.update_idletasks()
                self.assertGreater(app.preview_button.winfo_height(),20)
                self.assertLessEqual(app.preview_button.winfo_rooty()+app.preview_button.winfo_height(),root.winfo_rooty()+root.winfo_height())
                app.open_settings();window=app.settings_window;window.geometry('760x510');root.update();root.update_idletasks()
                button=next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='保存设置')
                self.assertGreater(button.winfo_height(),20)
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),window.winfo_rooty()+window.winfo_height())
            finally:root.update_idletasks();root.update_idletasks();root.destroy()

    def test_bad_event_does_not_stop_scheduling_and_destroy_cancels_callbacks(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();app=App(root,folder)
            root.after_cancel(app.tick_timer)
            app.events.put(())
            app.tick()
            self.assertTrue(root.tk.call('after','info'))
            root.update_idletasks();root.update_idletasks();root.destroy()
            self.assertFalse(root.tk.call('after','info'))
            self.assertTrue(app.quit_event.is_set())


if __name__=='__main__':unittest.main()
