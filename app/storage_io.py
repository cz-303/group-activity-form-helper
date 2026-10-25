"""Local atomic writes. Never truncate the destination before successful replacement."""

from contextlib import contextmanager
from pathlib import Path
import os
import tempfile


@contextmanager
def atomic_text(path, encoding='utf-8', newline=None):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline=newline) as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_bytes(path, content):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def spreadsheet_cell(value):
    text = str(value)
    # Excel can discard leading whitespace before interpreting a formula.
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text
