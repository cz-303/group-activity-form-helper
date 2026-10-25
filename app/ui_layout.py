"""Shared scrollable settings panels for small screens and larger font scaling."""

import ctypes
import logging
import sys
import tkinter as tk
from tkinter import ttk
from design_tokens import dp


class IdleUpdate:
    """Coalesce layout events in one idle callback, owned by its widget."""

    def __init__(self, widget, callback):
        self.widget, self.callback, self.timer, self.event = widget, callback, None, None
        widget.bind('<Destroy>', self.close, add='+')

    def request(self, event):
        self.event = event
        if self.timer is None:
            self.timer = self.widget.after_idle(self.run)

    def run(self):
        self.timer = None
        if self.widget.winfo_exists():
            self.callback(self.event)

    def close(self, event):
        if event.widget is self.widget and self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None


class ResizeUpdate(IdleUpdate):
    """At most one pending 40ms resize update; always apply the latest event."""

    def request(self, event):
        self.event = event
        if self.timer is None:
            self.timer = self.widget.after(40, self.run)


def set_wrap(label, width):
    if int(label.cget('wraplength')) != width:
        label.configure(wraplength=width)


def bind_wrap(container, label, width):
    job = ResizeUpdate(container, lambda event: set_wrap(label, width(event)))
    container.bind('<Configure>', job.request, add='+')
    return job


def filter_inline(width, threshold, previous, margin):
    if previous is None:
        return width >= threshold
    return width >= threshold - margin if previous else width >= threshold + margin


class ListDetailLayout:
    """Keep existing widgets and selections while switching between two panes and one."""

    def __init__(self, panes, list_panel, detail_panel, on_change):
        self.panes, self.list_panel, self.detail_panel = panes, list_panel, detail_panel
        self.on_change = on_change
        self.wide = None
        self.detail_requested = False
        self.layout_key = None

    def update_width(self, width):
        wide = filter_inline(width, dp(self.panes, 840), self.wide, dp(self.panes, 24))
        if wide != self.wide:
            self.wide = wide
            self.apply()

    def show_detail(self):
        self.detail_requested = True
        self.apply()

    def show_list(self):
        self.detail_requested = False
        self.apply()

    def apply(self):
        key = (self.wide, self.detail_requested)
        if key == self.layout_key:
            return
        self.layout_key = key
        wide = self.wide is not False
        desired = (
            (self.list_panel, self.detail_panel)
            if wide
            else (self.detail_panel if self.detail_requested else self.list_panel,)
        )
        if tuple(map(str, self.panes.panes())) != tuple(map(str, desired)):
            for panel in self.panes.panes():
                self.panes.forget(panel)
            for panel in desired:
                self.panes.add(panel, weight=3 if panel is self.list_panel else 2)
        self.detail_panel.configure(padding=(dp(self.panes, 14) if wide else 0, 0, 0, 0))
        self.on_change(wide, self.detail_requested)


class LazyNotebook(ttk.Notebook):
    """Build each settings panel once; unopened values remain in configuration."""

    def __init__(self, parent, on_built, **kwargs):
        super().__init__(parent, **kwargs)
        self.builders = {}
        self.built = set()
        self.failed = set()
        self.on_built = on_built
        self.bind('<<NotebookTabChanged>>', lambda event: self.ensure_selected())

    def add_lazy(self, title, builder):
        frame = ttk.Frame(self)
        self.add(frame, text=title)
        self.builders[str(frame)] = (frame, builder)
        return frame

    def ensure_selected(self):
        key = self.select()
        if not key or key in self.built:
            return
        frame, builder = self.builders[key]
        try:
            builder(frame)
            self.on_built(frame)
        except Exception:
            self.failed.add(key)
            for child in frame.winfo_children():
                child.destroy()
            ttk.Label(frame, text='此设置页加载失败，请关闭设置后重试。', padding=16).pack(anchor='w')
            raise
        self.built.add(key)
        self.failed.discard(key)


def settings_tab(notebook, title, padding=16, parent=None):
    outer = parent if parent is not None else ttk.Frame(notebook)
    if parent is None:
        notebook.add(outer, text=title)
    canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
    scroll = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    body = ttk.Frame(canvas, padding=padding)
    window = canvas.create_window(0, 0, window=body, anchor='nw')
    last_region = [None]
    last_width = [None]

    def update_region(event):
        region = canvas.bbox('all')
        if region != last_region[0]:
            last_region[0] = region
            canvas.configure(scrollregion=region)

    def update_width(event):
        if event.width != last_width[0]:
            last_width[0] = event.width
            canvas.itemconfigure(window, width=event.width)

    body.bind('<Configure>', ResizeUpdate(body, update_region).request)
    canvas.bind('<Configure>', ResizeUpdate(canvas, update_width).request)
    return body


def fit_window(window, width, height, minimum=(600, 400)):
    width, height = dp(window, width), dp(window, height)
    minimum = tuple(dp(window, n) for n in minimum)
    width = min(width, max(400, window.winfo_screenwidth() - 80))
    height = min(height, max(300, window.winfo_screenheight() - 100))
    window.minsize(min(minimum[0], width), min(minimum[1], height))
    window.geometry(f'{width}x{height}')


def enable_high_dpi():
    """Set system DPI awareness before Tk creation; never changes OS display settings."""
    import sys

    if sys.platform != 'win32':
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def work_area(parent):
    """Use the parent monitor's usable area, including negative monitor origins."""
    if sys.platform == 'win32':
        try:

            class RECT(ctypes.Structure):
                _fields_ = [(name, ctypes.c_long) for name in ('left', 'top', 'right', 'bottom')]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ('cbSize', ctypes.c_ulong),
                    ('rcMonitor', RECT),
                    ('rcWork', RECT),
                    ('dwFlags', ctypes.c_ulong),
                ]

            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            user32.MonitorFromWindow.restype = ctypes.c_void_p
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
            user32.GetMonitorInfoW.restype = ctypes.c_bool
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            monitor = user32.MonitorFromWindow(parent.winfo_id(), 2)
            if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            rect = info.rcWork
            return rect.left, rect.top, rect.right, rect.bottom
        except (OSError, AttributeError, tk.TclError):
            logging.getLogger(__name__).warning('Monitor work area unavailable; using Tk screen bounds.')
    x, y = parent.winfo_vrootx(), parent.winfo_vrooty()
    return x, y, x + parent.winfo_vrootwidth(), y + parent.winfo_vrootheight()


def centered_position(parent_rect, size, bounds, margin=16):
    x, y, width, height = parent_rect
    dialog_width, dialog_height = size
    left, top, right, bottom = bounds
    max_x = max(left + margin, right - dialog_width - margin)
    max_y = max(top + margin, bottom - dialog_height - margin)
    return (
        max(left + margin, min(x + (width - dialog_width) // 2, max_x)),
        max(top + margin, min(y + (height - dialog_height) // 2, max_y)),
    )


def center_window(window, parent):
    """Center an already sized dialog and keep it within the parent monitor."""
    window.update_idletasks()
    bounds = work_area(parent)
    margin = dp(window, 16)
    width = min(window.winfo_width(), bounds[2] - bounds[0] - margin * 2)
    height = min(window.winfo_height(), bounds[3] - bounds[1] - margin * 2)
    minimum = window.minsize()
    window.minsize(min(minimum[0], width), min(minimum[1], height))
    rect = (parent.winfo_rootx(), parent.winfo_rooty(), parent.winfo_width(), parent.winfo_height())
    x, y = centered_position(rect, (width, height), bounds, margin)
    window.geometry(f'{width}x{height}+{x}+{y}')
