"""Conservative, offline URL extraction; resource identity belongs to providers."""

import re
from urllib.parse import urlsplit
from .models import LinkCandidate

MAX_TEXT_LENGTH = 500_000
MAX_URL_LENGTH = 8192
MAX_CANDIDATES = 1000
_URL = re.compile(r'(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}:[^\s<>"，。；！？、（）【】]+')
_SCHEME = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*:')


def parse_url(raw):
    """Validate syntax only. A parsed URI is never permission to launch it."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_URL_LENGTH:
        raise ValueError('链接为空、类型不正确或超过8192字符。')
    if not _SCHEME.match(raw) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ValueError('链接协议或字符不合法。')
    if '\\' in raw:
        raise ValueError('链接不能包含反斜杠。')
    try:
        parsed = urlsplit(raw)
        if parsed.scheme in ('http', 'https'):
            if not parsed.netloc or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                raise ValueError('网页链接缺少主机或包含登录凭据。')
            parsed.port  # Validate malformed / out-of-range ports.
        elif len(parsed.scheme) == 1 or not raw.partition(':')[2]:
            raise ValueError('不是有效的应用链接。')
    except ValueError:
        raise ValueError('链接格式不正确。') from None
    return parsed


def extract_candidates(text):
    if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
        raise ValueError('通知须为文字，且一次最多50万字符。')
    candidates = []
    for match in _URL.finditer(text):
        raw = match.group().rstrip('.,;!?')
        for closing, opening in [(')', '('), (']', '['), ('}', '{')]:
            while raw.endswith(closing) and raw.count(closing) > raw.count(opening):
                raw = raw[:-1]
        candidates.append(LinkCandidate(raw, match.start(), match.start() + len(raw)))
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError('一次最多识别1000个链接，请分批处理。')
    return tuple(candidates)
