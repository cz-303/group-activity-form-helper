"""Original geometric notification-card icon, shared by the EXE, windows and tray."""

from PIL import Image, ImageDraw
from pathlib import Path
import sys
import logging
import tkinter as tk


def make_icon(size=256):
    s = 512
    image = Image.new('RGBA', (s, s))
    gradient = Image.new('RGBA', (s, s))
    pixels = ImageDraw.Draw(gradient)
    for y in range(s):
        t = y / (s - 1)
        pixels.line((0, y, s, y), fill=(int(60 - 40 * t), int(59 + 3 * t), int(177 - 64 * t), 255))
    mask = Image.new('L', (s, s))
    ImageDraw.Draw(mask).rounded_rectangle((8, 8, 504, 504), radius=110, fill=255)
    image.paste(gradient, (0, 0), mask)
    d = ImageDraw.Draw(image)
    d.arc((57, 57, 460, 460), 195, 332, fill='#55E7DC', width=14)
    d.rounded_rectangle((135, 123, 373, 390), radius=34, fill='#0D234D')
    d.rounded_rectangle((120, 104, 359, 369), radius=31, fill='#F0F6FF')
    d.line((165, 170, 307, 170), fill='#7287BA', width=15)
    d.line((165, 211, 268, 211), fill='#7287BA', width=15)
    d.ellipse((238, 255, 414, 431), fill='#55E7DC')
    d.line((277, 342, 307, 369, 375, 309), fill='#123451', width=23, joint='curve')
    d.polygon(
        [(399, 65), (409, 93), (437, 103), (409, 114), (399, 143), (388, 114), (359, 103), (388, 93)], fill='#FFD078'
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def install_window_icon(root):
    """Set the current window icon and the default for future dialogs."""
    ico = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'activity-helper.ico'
    if sys.platform == 'win32':
        try:
            with Image.open(ico) as resource:
                if resource.format != 'ICO':
                    raise ValueError('Invalid window icon format')
                resource.verify()
            # A default alone does not replace an existing per-window icon.
            root.iconbitmap(str(ico))
            root.iconbitmap(default=str(ico))
            return
        except (tk.TclError, OSError, ValueError):
            logging.getLogger(__name__).warning('Window ICO could not be loaded; using generated icons.')
    from PIL import ImageTk

    try:
        root._brand_photos = tuple(ImageTk.PhotoImage(make_icon(size), master=root) for size in (16, 32, 48, 128, 256))
        root.iconphoto(True, *root._brand_photos)
    except (tk.TclError, OSError, ValueError):
        logging.getLogger(__name__).exception('Window icon fallback failed.')


if __name__ == '__main__':
    folder = Path(__file__).parent
    im = make_icon(256)
    im.save(
        folder / 'activity-helper.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    im.save(folder / 'activity-helper.png')
