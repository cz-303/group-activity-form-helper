"""Known document links can be retained without granting a fill capability."""

import re
from urllib.parse import urlsplit, urlunsplit
from app_core.models import LinkInfo


def document_identity(provider, url):
    """Navigation stays intact; only Tencent sheet's numeric _t cache stamp is ignored.

    Keep raw query spelling/order, permissions, tab, fragment, unknown parameters
    and malformed/repeated _t entries. Do not apply Tencent sheet rules to Kdocs shares.
    """
    u = urlsplit(url)
    parts = u.query.split('&') if u.query else []
    stamps = [p for p in parts if p.split('=', 1)[0] == '_t']
    if provider == 'tencent_docs' and len(stamps) == 1 and re.fullmatch(r'_t=[0-9]{1,20}', stamps[0]):
        parts.remove(stamps[0])
    return provider + ':' + urlunsplit((u.scheme, u.netloc, u.path, '&'.join(parts), u.fragment))


class DocumentRecognizer:
    id = 'documents.shared'

    def recognize(self, candidate):
        try:
            if (
                not isinstance(candidate.raw_url, str)
                or len(candidate.raw_url) > 8192
                or any(ord(c) < 33 or ord(c) == 127 for c in candidate.raw_url)
            ):
                return None
            u = urlsplit(candidate.raw_url)
            if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
                return None
            if u.hostname == 'docs.qq.com' and re.fullmatch(r'/sheet/[A-Za-z0-9_-]+/?', u.path):
                provider, kind = 'tencent_docs', 'spreadsheet'
            elif u.hostname in ('www.kdocs.cn', 'kdocs.cn') and re.fullmatch(r'/l/[A-Za-z0-9_-]+/?', u.path):
                provider, kind = 'kdocs', 'document'
            else:
                return None
            # Sheet/tab selection and access parameters are significant. Keep them.
            host = 'www.kdocs.cn' if provider == 'kdocs' else 'docs.qq.com'
            normalized = urlunsplit(('https', host, u.path.rstrip('/'), u.query, u.fragment))
            return LinkInfo(
                candidate.raw_url,
                normalized,
                'https',
                self.id,
                provider,
                kind,
                document_identity(provider, normalized),
                1.0,
                '文档分享地址已识别；文档类型、权限及填写能力须另行验证。',
            )
        except ValueError:
            return None
