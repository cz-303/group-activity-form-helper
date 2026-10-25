"""Bounded local diagnostics, intentionally excluding exception messages and input data."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import traceback
import sqlite3
import threading
import time
from collections import OrderedDict


class UserFacingError(RuntimeError):
    """A fixed, intentional explanation safe to display without an internal traceback."""


def describe_error(error):
    if isinstance(error, PermissionError):
        return '文件被其他程序占用或当前目录没有写入权限，请关闭占用程序或移到可写目录后重试。'
    if isinstance(error, OSError):
        return '文件或程序无法访问，请检查文件是否存在、磁盘空间和访问权限。'
    if isinstance(error, sqlite3.Error):
        return '本地任务数据暂时无法访问，请关闭占用程序；若仍失败，请保留data目录并恢复备份。'
    if isinstance(error, (UserFacingError, ValueError)):
        return str(error).splitlines()[0][:300] if str(error) else '输入内容无效，请检查后重试。'
    return '操作未完成，请检查连接和程序状态后重试。'


def create_logger(data):
    logger = logging.getLogger('activity-helper.' + str(Path(data).resolve()))
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    if not logger.handlers:
        try:
            handler = RotatingFileHandler(
                Path(data) / 'diagnostics.log', maxBytes=512000, backupCount=2, encoding='utf-8'
            )
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


class ErrorRateLimiter:
    """Bounded, thread-safe suppression of repeated locations; no user values."""

    def __init__(self, interval=60, max_keys=64, clock=time.monotonic):
        self.interval = interval
        self.max_keys = max_keys
        self.clock = clock
        self.recent = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key):
        with self.lock:
            now = self.clock()
            last = self.recent.get(key)
            if last is not None and now - last < self.interval:
                return False
            self.recent[key] = now
            self.recent.move_to_end(key)
            while len(self.recent) > self.max_keys:
                self.recent.popitem(last=False)
            return True


def record_error(logger, operation, error, *, limiter=None):
    frames = traceback.extract_tb(error.__traceback__)
    locations = ' > '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in frames[-8:])
    # The same reused exception may accumulate traceback frames. Throttle by
    # the stable operation entry point, rather than its growing full trace.
    origin = (Path(frames[0].filename).name, frames[0].lineno, frames[0].name) if frames else ()
    if limiter is not None and not limiter.allow((operation, type(error).__name__, origin)):
        return
    logger.warning('%s | %s | %s', operation, type(error).__name__, locations)


def close_logger(logger):
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
