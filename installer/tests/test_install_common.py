"""Installer/uninstaller core: per-user install, upgrade keeps data, uninstall with/without data, refusals."""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), os.environ.get('GFHELPER_APP_SOURCE', str(HERE.parents[1] / 'app'))]

import install_common as ic  # noqa: E402


class FakeRegistry:
    def __init__(self):
        self.values = None

    def read(self):
        return self.values

    def write(self, values):
        self.values = dict(values)

    def delete(self):
        self.values = None


def fake_shortcut(link, target, working_dir, icon, description):
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.write_text(str(target), encoding='utf-8')


def make_payload(folder, files):
    path = Path(folder) / 'payload.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return path


BASE = {
    'app/' + ic.EXE_NAME: b'exe-1',
    'app/' + ic.UNINSTALLER_NAME: b'uninstall',
    'app/_internal/lib.dll': b'lib-1',
    'app/_internal/old_only.pyd': b'old',
    'app/使用说明.txt': '说明'.encode(),
}


class InstallCycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.registry = FakeRegistry()
        self.running = False
        self.env = ic.Environment(
            start_menu=self.root / 'StartMenu' / ic.APP_FOLDER,
            desktop=self.root / 'Desktop',
            registry=self.registry,
            make_shortcut=fake_shortcut,
            is_running=lambda target: self.running,
        )
        self.target = self.root / 'Programs' / ic.APP_FOLDER

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, files=BASE, version='1.0', **kwargs):
        folder = self.root / ('payload-' + version)
        folder.mkdir(exist_ok=True)
        payload = make_payload(folder, files)
        return ic.install(payload, self.target, version, env=self.env, **kwargs)

    def test_fresh_install_creates_files_shortcuts_and_registration(self):
        result = self.install()
        self.assertEqual(result['files'], 5)
        self.assertEqual((self.target / ic.EXE_NAME).read_bytes(), b'exe-1')
        manifest = ic.read_manifest(self.target)
        self.assertEqual(manifest['version'], '1.0')
        self.assertIn('_internal/lib.dll', manifest['files'])
        self.assertTrue((self.env.start_menu / ic.APP_SHORTCUT).is_file())
        self.assertTrue((self.env.start_menu / ic.UNINSTALL_SHORTCUT).is_file())
        self.assertTrue((self.env.desktop / ic.APP_SHORTCUT).is_file())
        self.assertEqual(self.registry.values['DisplayVersion'], '1.0')
        self.assertEqual(self.registry.values['InstallLocation'], str(self.target))
        self.assertIn(ic.UNINSTALLER_NAME, self.registry.values['UninstallString'])
        self.assertFalse((self.target / ic.STAGING_NAME).exists())
        self.assertEqual(ic.installed_location(self.env), self.target)

    def test_no_desktop_shortcut_when_unchecked(self):
        self.install(desktop_shortcut=False)
        self.assertFalse((self.env.desktop / ic.APP_SHORTCUT).exists())

    def test_upgrade_replaces_program_removes_stale_files_and_keeps_data(self):
        self.install()
        data = self.target / 'data'
        (data / 'browser').mkdir(parents=True)
        (data / 'config.json').write_text('{"姓名":"测试"}', encoding='utf-8')
        new = {k: v for k, v in BASE.items() if 'old_only' not in k}
        new['app/' + ic.EXE_NAME] = b'exe-2'
        new['app/_internal/new.pyd'] = b'new'
        result = self.install(new, version='1.1')
        self.assertEqual(result['upgraded_from'], '1.0')
        self.assertEqual((self.target / ic.EXE_NAME).read_bytes(), b'exe-2')
        self.assertFalse((self.target / '_internal' / 'old_only.pyd').exists())
        self.assertTrue((self.target / '_internal' / 'new.pyd').exists())
        self.assertEqual((data / 'config.json').read_text(encoding='utf-8'), '{"姓名":"测试"}')
        self.assertTrue((data / 'browser').is_dir())
        self.assertEqual(self.registry.values['DisplayVersion'], '1.1')

    def test_refuses_while_running_without_changes(self):
        self.install()
        self.running = True
        with self.assertRaises(ic.InstallError):
            self.install(version='1.1')
        self.assertEqual(ic.read_manifest(self.target)['version'], '1.0')

    def test_refuses_foreign_or_portable_folders(self):
        self.target.mkdir(parents=True)
        (self.target / '论文.docx').write_bytes(b'x')
        with self.assertRaises(ic.InstallError):
            self.install()
        self.assertTrue((self.target / '论文.docx').exists())
        portable = self.root / 'portable'
        portable.mkdir()
        (portable / ic.EXE_NAME).write_bytes(b'old')
        with self.assertRaises(ic.InstallError):
            ic.install(make_payload(self.root, BASE), portable, '1.0', env=self.env)

    def test_reinstall_into_folder_that_only_kept_data(self):
        self.target.mkdir(parents=True)
        (self.target / 'data').mkdir()
        (self.target / 'data' / 'tasks.sqlite3').write_bytes(b'db')
        self.install()
        self.assertEqual((self.target / 'data' / 'tasks.sqlite3').read_bytes(), b'db')

    def test_payload_with_unsafe_paths_is_rejected(self):
        for bad in ('app/../evil.exe', 'app/data/config.json', 'app/C:/x.dll'):
            files = dict(BASE)
            files[bad] = b'x'
            with self.subTest(bad=bad), self.assertRaises(ic.InstallError):
                self.install(files)
        self.assertFalse((self.root / 'Programs' / 'evil.exe').exists())

    def test_payload_without_main_program_is_rejected(self):
        with self.assertRaises(ic.InstallError):
            self.install({'app/readme.txt': b'x'})

    def test_drive_root_and_relative_paths_are_rejected(self):
        for target in (Path(self.root.anchor), Path('relative')):
            with self.subTest(target=target), self.assertRaises(ic.InstallError):
                ic.check_target(target)

    def test_uninstall_keeps_data_by_default(self):
        self.install()
        data = self.target / 'data'
        data.mkdir()
        (data / 'tasks.sqlite3').write_bytes(b'db')
        result = ic.uninstall(self.target, env=self.env)
        self.assertTrue(result['data_kept'])
        self.assertEqual(result['failed'], [])
        self.assertFalse((self.target / ic.EXE_NAME).exists())
        self.assertFalse((self.target / '_internal').exists())
        self.assertFalse((self.target / ic.MANIFEST_NAME).exists())
        self.assertEqual((data / 'tasks.sqlite3').read_bytes(), b'db')
        self.assertFalse(self.env.start_menu.exists())
        self.assertFalse((self.env.desktop / ic.APP_SHORTCUT).exists())
        self.assertIsNone(self.registry.values)

    def test_uninstall_with_data_removes_everything(self):
        self.install()
        data = self.target / 'data'
        (data / 'browser' / 'Default').mkdir(parents=True)
        (data / 'config.json').write_text('{}', encoding='utf-8')
        (data / 'browser' / 'Default' / 'Cookies').write_bytes(b'c')
        result = ic.uninstall(self.target, env=self.env, delete_data=True)
        self.assertFalse(result['data_kept'])
        self.assertEqual(result['data_remaining'], [])
        self.assertTrue(result['folder_removed'])
        self.assertFalse(self.target.exists())

    def test_uninstall_keeps_files_it_did_not_install(self):
        self.install()
        extra = self.target / '我的笔记.txt'
        extra.write_text('keep', encoding='utf-8')
        result = ic.uninstall(self.target, env=self.env)
        self.assertTrue(extra.exists())
        self.assertFalse(result['folder_removed'])

    def test_uninstall_refuses_unknown_folder_or_running_app(self):
        folder = self.root / 'random'
        folder.mkdir()
        (folder / 'a.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(ic.InstallError):
            ic.uninstall(folder, env=self.env)
        self.assertTrue((folder / 'a.txt').exists())
        self.install()
        self.running = True
        with self.assertRaises(ic.InstallError):
            ic.uninstall(self.target, env=self.env)
        self.assertTrue((self.target / ic.EXE_NAME).exists())

    def test_manifest_is_valid_json_with_sorted_files(self):
        self.install()
        manifest = json.loads((self.target / ic.MANIFEST_NAME).read_text(encoding='utf-8'))
        self.assertEqual(manifest['files'], sorted(manifest['files']))


class MutexName(unittest.TestCase):
    def test_matches_app_formula(self):
        import hashlib

        folder = Path(tempfile.gettempdir()) / 'gfhelper-mutex-test'
        expected = (
            'Local\\GroupFormHelper_' + hashlib.sha256(str((folder / 'data').resolve()).lower().encode()).hexdigest()[:24]
        )
        self.assertEqual(ic.mutex_name(folder), expected)


if __name__ == '__main__':
    unittest.main()
