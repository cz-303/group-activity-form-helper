import tkinter as tk
from tkinter import ttk

from design_tokens import PALETTES, FONTS, STATES, dp
from rounded_controls import install


def apply_theme(root, name='light', target=None):
    name = name if name in PALETTES else 'light'
    p = PALETTES[name]
    style = ttk.Style(root)
    key = (name, dp(root, 8))
    if getattr(root, '_applied_theme_key', None) != key:
        if style.theme_use() != 'clam':
            style.theme_use('clam')
        style.configure(
            '.',
            background=p['surface'],
            foreground=p['fg'],
            font=FONTS['body'],
            bordercolor=p['border'],
            lightcolor=p['border'],
            darkcolor=p['border'],
        )
        style.configure(
            'Horizontal.TProgressbar',
            background=p['accent'],
            troughcolor=p['surface'],
            bordercolor=p['border'],
            lightcolor=p['accent'],
            darkcolor=p['accent'],
        )
        style.configure('TFrame', background=p['surface'])
        style.configure('TLabel', background=p['surface'], foreground=p['fg'])
        # Grey window shell around the white cards.
        style.configure('Shell.TFrame', background=p['bg'])
        style.configure('Shell.TLabel', background=p['bg'], foreground=p['fg'])
        style.configure('ShellMuted.TLabel', background=p['bg'], foreground=p['muted'], font=FONTS['caption'])
        style.configure('ShellTitle.TLabel', background=p['bg'], foreground=p['fg'], font=FONTS['title'])
        style.configure('TSeparator', background=p['border'])
        style.configure('Divider.TFrame', background=p['border'])
        style.configure('TPanedwindow', background=p['surface'])
        style.configure('Sash', sashthickness=dp(root, 12), gripcount=0, background=p['surface'],
                        lightcolor=p['surface'], darkcolor=p['surface'], bordercolor=p['surface'])
        style.configure('TButton', padding=(12, 7), background=p['surface'], foreground=p['fg'], borderwidth=1)
        style.map('TButton', background=[('active', p['hover'])], foreground=[('disabled', p['muted'])])
        style.configure('Primary.TButton', background=p['accent'], foreground=p['primary_fg'])
        style.map(
            'Primary.TButton',
            background=[('disabled', p['border']), ('active', p['selected'])],
            foreground=[('disabled', p['muted']), ('active', p['fg'])],
        )
        style.configure('TMenubutton', padding=(9, 7), background=p['surface'], arrowcolor=p['muted'])
        style.configure('TEntry', fieldbackground=p['surface'], foreground=p['fg'], insertcolor=p['fg'], padding=6)
        style.configure(
            'TCombobox',
            fieldbackground=p['surface'],
            background=p['surface'],
            foreground=p['fg'],
            padding=5,
            arrowsize=13,
            arrowcolor=p['muted'],
        )
        style.configure(
            'Vertical.TScrollbar',
            background=p['strong_border'],
            troughcolor=p['surface'],
            arrowcolor=p['muted'],
            bordercolor=p['surface'],
            lightcolor=p['strong_border'],
            darkcolor=p['strong_border'],
            gripcount=0,
            arrowsize=dp(root, 12),
        )
        style.map(
            'Vertical.TScrollbar',
            background=[('active', p['muted'])],
            lightcolor=[('active', p['muted'])],
            darkcolor=[('active', p['muted'])],
        )
        style.map(
            'TCombobox',
            fieldbackground=[('readonly', p['surface'])],
            foreground=[('readonly', p['fg'])],
            selectbackground=[('readonly', p['surface'])],
            selectforeground=[('readonly', p['fg'])],
        )
        style.configure(
            'Treeview',
            background=p['surface'],
            fieldbackground=p['surface'],
            foreground=p['fg'],
            rowheight=dp(root, 40),
            borderwidth=0,
            bordercolor=p['surface'],
            lightcolor=p['surface'],
            darkcolor=p['surface'],
        )
        style.configure(
            'Treeview.Heading',
            background=p['surface'],
            foreground=p['muted'],
            font=FONTS['caption'],
            padding=(dp(root, 8), dp(root, 6)),
            anchor='w',
            relief='flat',
            bordercolor=p['border'],
            lightcolor=p['surface'],
            darkcolor=p['border'],
        )
        style.map('Treeview.Heading', background=[('active', p['hover'])], relief=[])
        style.map('Treeview', background=[('selected', p['selected'])], foreground=[('selected', p['fg'])])
        style.configure(
            'TNotebook',
            background=p['surface'],
            borderwidth=0,
            bordercolor=p['border'],
            lightcolor=p['surface'],
            darkcolor=p['surface'],
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            'TNotebook.Tab',
            padding=(dp(root, 12), dp(root, 7)),
            background=p['surface'],
            foreground=p['muted'],
            bordercolor=p['surface'],
            lightcolor=p['surface'],
            darkcolor=p['surface'],
        )
        style.map(
            'TNotebook.Tab',
            background=[('selected', p['surface']), ('active', p['hover'])],
            foreground=[('selected', p['accent'])],
            lightcolor=[('selected', p['accent'])],
            bordercolor=[('selected', p['surface'])],
            expand=[],
        )
        style.configure('TLabelframe', background=p['surface'], bordercolor=p['border'], borderwidth=1)
        style.configure('TLabelframe.Label', foreground=p['fg'], background=p['surface'], font=FONTS['strong'])
        style.configure('TCheckbutton', background=p['surface'], foreground=p['fg'])
        style.map('TCheckbutton', background=[('active', p['surface'])])
        style.configure('TRadiobutton', background=p['surface'], foreground=p['fg'])
        style.map('TRadiobutton', background=[('active', p['surface'])])
        install(root, style, name, p)
        style.configure('Sidebar.TFrame', background=p['sidebar'])
        style.configure('Sidebar.TLabel', background=p['sidebar'], foreground=p['fg'])
        style.configure('SidebarMuted.TLabel', background=p['sidebar'], foreground=p['muted'])
        style.layout(
            'Sidebar.TButton',
            [
                (
                    'Button.padding',
                    {
                        'sticky': 'nsew',
                        'children': [
                            ('Button.focus', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})
                        ],
                    },
                )
            ],
        )
        style.configure(
            'Sidebar.TButton',
            background=p['sidebar'],
            foreground=p['fg'],
            padding=(dp(root, 12), dp(root, 9)),
            anchor='w',
        )
        style.map(
            'Sidebar.TButton',
            background=[('active', p['nav_hover']), ('pressed', p['nav_hover'])],
            foreground=[('disabled', p['muted'])],
        )
        style.layout(
            'Sidebar.TMenubutton',
            [
                (
                    'Menubutton.padding',
                    {
                        'sticky': 'nsew',
                        'children': [
                            ('Menubutton.indicator', {'side': 'right', 'sticky': 'ns'}),
                            ('Menubutton.label', {'sticky': 'nsew'}),
                        ],
                    },
                )
            ],
        )
        style.configure(
            'Sidebar.TMenubutton',
            background=p['sidebar'],
            foreground=p['fg'],
            padding=(dp(root, 12), dp(root, 9)),
            anchor='w',
            arrowcolor=p['muted'],
        )
        style.map('Sidebar.TMenubutton', background=[('active', p['nav_hover'])], foreground=[('disabled', p['muted'])])
        style.configure('SidebarHeading.TLabel', background=p['sidebar'], foreground=p['muted'], font=FONTS['caption'])
        for label, font in [
            ('Title', 'title'),
            ('Dialog', 'dialog'),
            ('Section', 'section'),
            ('Caption', 'caption'),
            ('Strong', 'strong'),
        ]:
            style.configure(label + '.TLabel', font=FONTS[font])
        style.configure('Muted.TLabel', foreground=p['muted'])
        style.configure('Caption.TLabel', foreground=p['muted'])
        style.configure('Accent.TLabel', foreground=p['accent'])
        style.configure('Toast.TFrame', background=p['surface'])
        for state, (_, fg, bg) in STATES.items():
            style.configure(
                f'Status.{state}.TLabel', foreground=p[fg], background=p[bg], padding=(dp(root, 8), dp(root, 4))
            )
        root.option_add('*TCombobox*Listbox.font', FONTS['body'])
        root.option_add('*TCombobox*Listbox.background', p['surface'])
        root.option_add('*TCombobox*Listbox.foreground', p['fg'])
        root.option_add('*TCombobox*Listbox.selectBackground', p['selected'])
        root.option_add('*TCombobox*Listbox.selectForeground', p['fg'])
        root._applied_theme_key = key

    def walk(widget):
        try:
            if isinstance(widget, tk.Tk):
                widget.configure(background=p['bg'])
            elif isinstance(widget, tk.Toplevel):
                # Dialogs are plain white surfaces; only the main window has the grey shell.
                widget.configure(background=p['surface'])
            elif isinstance(widget, tk.Canvas):
                widget.configure(background=p['surface'])
            elif isinstance(widget, tk.Text):
                widget.configure(
                    background=p['surface'],
                    foreground=p['fg'],
                    insertbackground=p['fg'],
                    selectbackground=p['selected'],
                    selectforeground=p['fg'],
                    font=FONTS['body'],
                    padx=dp(root, 8),
                    pady=dp(root, 8),
                    borderwidth=0,
                    highlightthickness=1,
                    highlightbackground=p['strong_border'],
                    highlightcolor=p['accent'],
                )
            elif isinstance(widget, tk.Menu):
                widget.configure(
                    font=FONTS['body'],
                    background=p['surface'],
                    foreground=p['fg'],
                    activebackground=p['selected'],
                    activeforeground=p['fg'],
                    relief='flat',
                    activeborderwidth=0,
                )
            elif isinstance(widget, ttk.Treeview):
                widget.tag_configure('muted', foreground=p['muted'])
                widget.tag_configure('error', foreground=p['error'])
            elif isinstance(widget, (ttk.Entry, ttk.Combobox)):
                widget.configure(font=FONTS['body'])
            # Explicit semantic styles survive both theme switches and new dialogs.
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            walk(child)

    walk(root if target is None else target)


def themed(app, window=None):
    apply_theme(app.root, app.get_config().get('theme', 'light'), target=window)
