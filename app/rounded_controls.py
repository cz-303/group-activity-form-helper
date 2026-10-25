"""Nine-slice ttk borders preserve native keyboard, focus and disabled behavior."""

from PIL import Image, ImageDraw, ImageTk
from design_tokens import CARD_RADIUS, CONTROL_RADIUS, CONTROL_PADDING, FONTS, dp


def install(root, style, name, palette):
    p = palette
    radius = max(4, dp(root, CONTROL_RADIUS))
    card_radius = max(4, dp(root, CARD_RADIUS))
    key = (name, radius)
    cache = getattr(root, '_rounded_theme_assets', None)
    if cache is None:
        cache = root._rounded_theme_assets = {}
    if key not in cache:
        images, names = [], {}

        def picture(fill, outline, focus=False, center=32, radius=radius):
            # Tk tiles the image center. Tiny centers cause thousands of GDI
            # blits when large panels are resized; keep the same corner radius.
            size = radius * 2 + center
            image = Image.new('RGBA', (size * 3, size * 3))
            ImageDraw.Draw(image).rounded_rectangle(
                (1, 1, size * 3 - 2, size * 3 - 2),
                radius=radius * 3,
                fill=fill,
                outline=outline,
                width=(2 if focus else 1) * 3,
            )
            image = image.resize((size, size), Image.Resampling.LANCZOS)
            result = ImageTk.PhotoImage(image, master=root)
            images.append(result)
            return result

        for kind, fill, hover, edge in (
            ('normal', p['surface'], p['hover'], p['strong_border']),
            ('primary', p['accent'], p['primary_hover'], p['accent']),
            ('danger', p['error'], p['danger_hover'], p['error']),
            ('input', p['surface'], p['surface'], p['strong_border']),
            ('panel', p['surface'], p['surface'], p['border']),
        ):
            center = 128 if kind == 'panel' else 32
            normal = picture(fill, edge, center=center)
            active = picture(hover, edge, center=center)
            focused = picture(fill, p['accent'] if kind == 'input' else p['fg'], True, center=center)
            active_focus = picture(hover, p['accent'] if kind == 'input' else p['fg'], True, center=center)
            disabled = picture(p['neutral_bg'], p['border'], center=center)
            element = f'Rounded.{name}.{radius}.{kind}'
            style.element_create(
                element,
                'image',
                normal,
                ('disabled', disabled),
                ('pressed', 'focus', active_focus),
                ('active', 'focus', active_focus),
                ('focus', focused),
                ('pressed', active),
                ('active', active),
                border=radius,
                padding=0,
                sticky='nsew',
                width=radius * 2 + 7,
                height=radius * 2 + 7,
            )
            names[kind] = element

        # Cards and the selected sidebar item are static pictures (no state
        # maps), drawn with a large tiled centre so resizing stays cheap.
        for kind, fill, edge in (
            ('card', p['surface'], p['border']),
            ('nav', p['nav_selected'], p['nav_selected']),
        ):
            element = f'Rounded.{name}.{card_radius}.{kind}'
            style.element_create(
                element,
                'image',
                picture(fill, edge, center=128 if kind == 'card' else 32, radius=card_radius),
                border=card_radius,
                padding=0,
                sticky='nsew',
                width=card_radius * 2 + 7,
                height=card_radius * 2 + 7,
            )
            names[kind] = element

        def checkbox(selected=False, disabled=False):
            size = dp(root, 16)
            margin = dp(root, 6)
            image = Image.new('RGBA', ((size + margin) * 3, size * 3))
            draw = ImageDraw.Draw(image)
            fill = p['neutral_bg'] if disabled else p['accent'] if selected else p['surface']
            edge = p['muted'] if disabled else p['accent'] if selected else p['border']
            draw.rounded_rectangle(
                (2, 2, size * 3 - 3, size * 3 - 3), radius=dp(root, 3) * 3, fill=fill, outline=edge, width=3
            )
            if selected:
                draw.line(
                    [
                        (size * 0.22 * 3, size * 0.5 * 3),
                        (size * 0.43 * 3, size * 0.71 * 3),
                        (size * 0.78 * 3, size * 0.28 * 3),
                    ],
                    fill=p['muted'] if disabled else p['primary_fg'],
                    width=max(3, dp(root, 2) * 3),
                )
            photo = ImageTk.PhotoImage(image.resize((size + margin, size), Image.Resampling.LANCZOS), master=root)
            images.append(photo)
            return photo

        indicator = f'Rounded.{name}.{radius}.Checkbutton.indicator'
        style.element_create(
            indicator,
            'image',
            checkbox(),
            ('disabled', 'selected', checkbox(True, True)),
            ('disabled', checkbox(False, True)),
            ('selected', checkbox(True)),
            sticky='w',
        )
        names['checkbox'] = indicator
        # Strong references must live as long as this Tcl interpreter.
        cache[key] = (names, images)
    elements = cache[key][0]
    padding = tuple(dp(root, n) for n in CONTROL_PADDING)
    for button, element, fg in [
        ('TButton', 'normal', p['fg']),
        ('Primary.TButton', 'primary', p['primary_fg']),
        ('Danger.TButton', 'danger', p['primary_fg']),
    ]:
        style.layout(
            button,
            [
                (
                    elements[element],
                    {
                        'sticky': 'nsew',
                        'children': [
                            ('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})
                        ],
                    },
                )
            ],
        )
        style.configure(button, padding=padding, foreground=fg, background=p['surface'], anchor='center')
        style.map(button, background=[], foreground=[('disabled', p['muted'])])
    style.layout(
        'TEntry',
        [
            (
                elements['input'],
                {
                    'sticky': 'nsew',
                    'children': [
                        ('Entry.padding', {'sticky': 'nsew', 'children': [('Entry.textarea', {'sticky': 'nsew'})]})
                    ],
                },
            )
        ],
    )
    style.configure('TEntry', padding=padding, background=p['surface'])
    style.map('TEntry', foreground=[('disabled', p['muted'])], fieldbackground=[('disabled', p['neutral_bg'])])
    style.layout(
        'TCombobox',
        [
            (
                elements['input'],
                {
                    'sticky': 'nsew',
                    'children': [
                        ('Combobox.downarrow', {'side': 'right', 'sticky': 'ns'}),
                        (
                            'Combobox.padding',
                            {'sticky': 'nsew', 'children': [('Combobox.textarea', {'sticky': 'nsew'})]},
                        ),
                    ],
                },
            )
        ],
    )
    style.configure('TCombobox', padding=padding, arrowsize=dp(root, 13))
    style.map(
        'TCombobox',
        foreground=[('disabled', p['muted']), ('readonly', p['fg'])],
        fieldbackground=[('disabled', p['neutral_bg']), ('readonly', p['surface'])],
    )
    style.layout(
        'TMenubutton',
        [
            (
                elements['normal'],
                {
                    'sticky': 'nsew',
                    'children': [
                        (
                            'Menubutton.padding',
                            {
                                'sticky': 'nsew',
                                'children': [
                                    ('Menubutton.indicator', {'side': 'right', 'sticky': 'ns'}),
                                    ('Menubutton.label', {'side': 'left', 'sticky': 'nsew'}),
                                ],
                            },
                        )
                    ],
                },
            )
        ],
    )
    style.configure('TMenubutton', padding=padding, background=p['surface'])
    style.map('TMenubutton', foreground=[('disabled', p['muted'])])
    style.layout('TLabelframe', [(elements['panel'], {'sticky': 'nsew'})])
    # A card on the grey shell: rounded white panel, title drawn above it.
    style.layout('Card.TFrame', [(elements['card'], {'sticky': 'nsew'})])
    style.configure('Card.TFrame', background=p['bg'])
    style.layout('Card.TLabelframe', [(elements['card'], {'sticky': 'nsew'})])
    style.configure('Card.TLabelframe', background=p['bg'], labeloutside=True, labelmargins=(2, 0, 0, dp(root, 6)))
    style.configure('Card.TLabelframe.Label', background=p['bg'], foreground=p['fg'], font=FONTS['strong'])
    style.layout(
        'NavActive.TButton',
        [
            (
                elements['nav'],
                {
                    'sticky': 'nsew',
                    'children': [
                        ('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})
                    ],
                },
            )
        ],
    )
    style.configure(
        'NavActive.TButton',
        background=p['sidebar'],
        foreground=p['fg'],
        padding=(dp(root, 12), dp(root, 9)),
        anchor='w',
        font=FONTS['strong'],
    )
    style.map('NavActive.TButton', background=[], foreground=[])
    style.layout(
        'TCheckbutton',
        [
            (
                'Checkbutton.padding',
                {
                    'sticky': 'nswe',
                    'children': [
                        (elements['checkbox'], {'side': 'left', 'sticky': 'w'}),
                        (
                            'Checkbutton.focus',
                            {'side': 'left', 'sticky': 'w', 'children': [('Checkbutton.label', {'sticky': 'nswe'})]},
                        ),
                    ],
                },
            )
        ],
    )
    style.map('TCheckbutton', foreground=[('disabled', p['muted'])])
