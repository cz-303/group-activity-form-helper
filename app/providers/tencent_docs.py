"""Existing Tencent Forms URL rules; unchanged legacy resource identity."""

import re
from urllib.parse import urlsplit
from app_core.models import LinkCandidate, LinkInfo

PROVIDER_ID = 'tencent_docs'
_LEGACY_URL = re.compile(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")


def canonical_form_url(raw):
    if not isinstance(raw, str):
        return None
    try:
        u = urlsplit(raw.strip())
        if u.scheme != 'https' or u.hostname != 'docs.qq.com' or u.username or u.password or u.port not in (None, 443):
            return None
        if not re.fullmatch(r'/form/page/[A-Za-z0-9_-]+/?', u.path):
            return None
        return 'https://docs.qq.com' + u.path.rstrip('/')
    except ValueError:
        return None


def legacy_candidates(text):
    """Preserve the current monitor's punctuation and extraction behaviour."""
    if not isinstance(text, str):
        return ()
    return tuple(
        LinkCandidate(raw, m.start(), m.start() + len(raw))
        for m in _LEGACY_URL.finditer(text)
        if (raw := m.group().rstrip(').,;!?]'))
    )


class TencentDocsRecognizer:
    id = 'tencent_docs.form'

    def recognize(self, candidate):
        url = canonical_form_url(candidate.raw_url)
        if not url:
            return None
        return LinkInfo(
            candidate.raw_url,
            url,
            'https',
            self.id,
            PROVIDER_ID,
            'form',
            PROVIDER_ID + ':' + url,
            1.0,
            '腾讯文档域名与收集表路径匹配；不代表表单内容可信。',
        )
