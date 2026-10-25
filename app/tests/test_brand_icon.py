import ctypes
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import brand_icon


@unittest.skipUnless(sys.platform == 'win32', 'Windows native window icons')
class WindowIcons(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.user32 = ctypes.WinDLL('user32')
        self.user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.user32.GetAncestor.restype = ctypes.c_void_p
        self.user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_size_t, ctypes.c_ssize_t]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.user32.GetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.user32.GetClassLongPtrW.restype = ctypes.c_size_t

    def handles(self, window):
        window.geometry("120x80+0+0")
        window.deiconify()
        self.root.update()
        hwnd = self.user32.GetAncestor(window.winfo_id(), 2)
        # WM_GETICON may return zero for an inherited class icon.
        return tuple(self.user32.SendMessageW(hwnd, 0x7F, kind, 0)
                     or self.user32.GetClassLongPtrW(hwnd, class_index)
                     for kind, class_index in ((0, -34), (1, -14)))

    def test_main_and_future_dialog_have_native_icons(self):
        before = self.handles(self.root)
        brand_icon.install_window_icon(self.root)
        main = self.handles(self.root)
        self.assertTrue(all(main), main)
        self.assertNotEqual(main, before)
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        self.assertTrue(all(self.handles(dialog)))

    def test_packaged_resource_in_chinese_path(self):
        with tempfile.TemporaryDirectory(prefix='图标资源-') as folder:
            icon = Path(folder) / 'activity-helper.ico'
            icon.write_bytes((Path(brand_icon.__file__).parent / icon.name).read_bytes())
            with patch.object(sys, '_MEIPASS', folder, create=True):
                brand_icon.install_window_icon(self.root)
            self.assertTrue(all(self.handles(self.root)))

    def test_missing_and_corrupt_resource_fall_back_without_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            for corrupt in (False, True):
                if corrupt:
                    (Path(folder) / 'activity-helper.ico').write_bytes(b'not an icon')
                with patch.object(sys, '_MEIPASS', folder, create=True):
                    with self.assertLogs('brand_icon', level='WARNING'):
                        brand_icon.install_window_icon(self.root)
                self.assertEqual(len(self.root._brand_photos), 5)
                self.assertTrue(all(self.handles(self.root)))


if __name__ == '__main__':
    unittest.main()
