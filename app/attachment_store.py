"""Bounded local image blobs share the task database's atomic backup and restore."""

import hashlib, io, time, warnings
from pathlib import Path
from PIL import Image

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_TOTAL_BYTES = 80 * 1024 * 1024
MAX_IMAGES = 1000


def image_bytes(source):
    if isinstance(source, Image.Image):
        image = source
        if getattr(image, 'n_frames', 1) != 1:
            raise ValueError('请选择单帧图片。')
        if image.width * image.height > MAX_PIXELS:
            raise ValueError('图片超过1200万像素。')
        out = io.BytesIO()
        image.convert('RGB').save(out, format='PNG')
        raw = out.getvalue()
    else:
        if isinstance(source, (str, Path)):
            with open(source, 'rb') as stream:
                raw = stream.read(MAX_IMAGE_BYTES + 1)
        else:
            raw = source
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('图片为空或超过8MB。')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            try:
                with Image.open(io.BytesIO(raw)) as opened:
                    if opened.format not in ('PNG', 'JPEG', 'WEBP', 'BMP') or getattr(opened, 'n_frames', 1) != 1:
                        raise ValueError('请选择单帧 PNG、JPG、WebP 或 BMP 图片。')
                    if opened.width * opened.height > MAX_PIXELS:
                        raise ValueError('图片超过1200万像素。')
                    opened.load()
                    image = opened.convert('RGB')
            except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
                raise ValueError('图片无法读取或尺寸过大。') from e
        out = io.BytesIO()
        image.save(out, format='PNG')
        raw = out.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('转换后的图片超过8MB。')
    return raw


def has_qr(raw):
    import zxingcpp

    with Image.open(io.BytesIO(raw)) as image:
        return bool(zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode))


def add_image(store, source, origin='手动导入', message_id='', qr_only=False):
    raw = image_bytes(source)
    qr = has_qr(raw)
    if qr_only and not qr:
        return None, False
    if not isinstance(origin, str) or len(origin) > 4000 or not isinstance(message_id, str) or len(message_id) > 200:
        raise ValueError('图片来源信息过长。')
    key = hashlib.sha256(raw).hexdigest()
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM attachments WHERE id=?', (key,)).fetchone():
            return key, False
        if db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0] >= MAX_IMAGES:
            raise ValueError('图片数量已达1000张，请先清理不再需要的图片。')
        total = db.execute('SELECT COALESCE(SUM(length(image)),0) FROM attachments').fetchone()[0]
        if total + len(raw) > MAX_TOTAL_BYTES:
            raise ValueError('本地图片已达80MB，请先导出并清理不再需要的图片。')
        db.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?)', (key, raw, origin, message_id, time.time(), int(qr)))
    return key, True


def images(store, url=None, pending=False):
    where = ''
    args = ()
    if url is not None:
        where = ' WHERE EXISTS(SELECT 1 FROM task_attachments t WHERE t.image_id=a.id AND t.url=?)'
        args = (url,)
    elif pending:
        where = ' WHERE NOT EXISTS(SELECT 1 FROM task_attachments t WHERE t.image_id=a.id)'
    with store.connection() as db:
        return [
            dict(r)
            for r in db.execute(
                'SELECT a.id,a.source,a.message_id,a.created,a.is_qr,length(a.image) AS size,(SELECT COUNT(*) FROM task_attachments t WHERE t.image_id=a.id) AS linked FROM attachments a'
                + where
                + ' ORDER BY a.created DESC',
                args,
            )
        ]


def get_image(store, key):
    with store.connection() as db:
        row = db.execute('SELECT image FROM attachments WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('图片已不存在，请刷新。')
        return bytes(row['image'])


def associate(store, key, url):
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if (
            not db.execute('SELECT 1 FROM attachments WHERE id=?', (key,)).fetchone()
            or not db.execute('SELECT 1 FROM tasks WHERE url=?', (url,)).fetchone()
        ):
            raise ValueError('图片或活动已不存在，请刷新后重试。')
        db.execute('INSERT OR IGNORE INTO task_attachments VALUES(?,?)', (url, key))


def detach(store, key, url):
    with store.connection() as db:
        db.execute('DELETE FROM task_attachments WHERE url=? AND image_id=?', (url, key))


def delete_image(store, key):
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM task_attachments WHERE image_id=?', (key,))
        db.execute('DELETE FROM attachments WHERE id=?', (key,))


def export_image(store, key, target, data):
    from storage_io import atomic_bytes

    target = Path(target)
    if target.suffix.lower() != '.png' or target.resolve().is_relative_to(Path(data).resolve()):
        raise ValueError('请将图片另存为软件 data 目录以外的 PNG 文件。')
    atomic_bytes(target, get_image(store, key))
