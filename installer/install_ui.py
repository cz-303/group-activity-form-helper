"""Shared look for the installer and uninstaller windows (matches the app's light palette)."""

import os
import tkinter as tk
from tkinter import ttk

FONT = 'Microsoft YaHei UI'
PALETTE = dict(
    bg='#FFFFFF',
    fg='#202123',
    muted='#60646C',
    border='#E1E3E6',
    accent='#303234',
    primary_fg='#FFFFFF',
    hover='#F0F1F2',
    warning='#805000',
    warning_bg='#FFF2D5',
    info_bg='#E8F0FF',
    info='#2456A6',
)


def enable_dpi_awareness():
    if os.name == 'nt':
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def make_root(title, width, height, icon=None):
    enable_dpi_awareness()
    root = tk.Tk()
    root.title(title)
    root.configure(background=PALETTE['bg'])
    scale = max(1.0, float(root.tk.call('tk', 'scaling')) / (96 / 72))
    w, h = int(width * scale), int(height * scale)
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    y = max(0, (root.winfo_screenheight() - h) // 3)
    root.geometry(f'{w}x{h}+{x}+{y}')
    root.minsize(w, h)
    if icon and os.path.isfile(icon):
        try:
            root.iconbitmap(icon)
        except tk.TclError:
            pass
    style = ttk.Style(root)
    style.theme_use('clam')
    p = PALETTE
    style.configure('.', background=p['bg'], foreground=p['fg'], font=(FONT, 10), bordercolor=p['border'])
    style.configure('TFrame', background=p['bg'])
    style.configure('TLabel', background=p['bg'], foreground=p['fg'])
    style.configure('Title.TLabel', font=(FONT, 16, 'bold'))
    style.configure('Muted.TLabel', foreground=p['muted'], font=(FONT, 9))
    style.configure('Warn.TLabel', foreground=p['warning'], background=p['warning_bg'], padding=(8, 6))
    style.configure('Info.TLabel', foreground=p['info'], background=p['info_bg'], padding=(8, 6))
    style.configure('TCheckbutton', background=p['bg'])
    style.map('TCheckbutton', background=[('active', p['bg'])])
    style.configure('TButton', padding=(14, 6), background=p['bg'])
    style.map('TButton', background=[('active', p['hover'])])
    style.configure('Primary.TButton', background=p['accent'], foreground=p['primary_fg'])
    style.map(
        'Primary.TButton',
        background=[('disabled', p['border']), ('active', '#44474B')],
        foreground=[('disabled', p['muted'])],
    )
    style.configure('TEntry', fieldbackground=p['bg'], padding=5)
    style.configure(
        'Horizontal.TProgressbar',
        background=p['accent'],
        troughcolor=p['hover'],
        bordercolor=p['border'],
        lightcolor=p['accent'],
        darkcolor=p['accent'],
    )
    return root


def resource_path(name):
    import sys
    from pathlib import Path

    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / name


def check(parent, text, variable, command=None):
    """Native Windows checkbox (the clam theme draws an "x" mark that looks like "unchecked")."""
    p = PALETTE
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        font=(FONT, 10),
        anchor='w',
        background=p['bg'],
        activebackground=p['bg'],
        foreground=p['fg'],
        activeforeground=p['fg'],
        highlightthickness=0,
        borderwidth=0,
        padx=0,
    )


def auto_wrap(label, margin=4):
    """Wrap text at the label's real width so lines never break early or overflow at any DPI."""
    label.bind('<Configure>', lambda event: label.configure(wraplength=max(120, event.width - margin)), add='+')
    return label
