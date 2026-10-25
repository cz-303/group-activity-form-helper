"""Syntactic fallback recognizers. These classes never open or fill anything."""

from app_core.models import LinkInfo
from app_core.url_candidates import parse_url


class GenericWebRecognizer:
    id = 'generic.web'

    def recognize(self, candidate):
        parsed = parse_url(candidate.raw_url)
        if parsed.scheme not in ('http', 'https'):
            return None
        # Preserve path, query and fragment; only scheme case is normalized.
        url = parsed.scheme + ':' + candidate.raw_url.partition(':')[2]
        return LinkInfo(
            candidate.raw_url,
            url,
            parsed.scheme,
            self.id,
            None,
            'web',
            'web:' + url,
            0.25,
            '普通网页地址，尚未识别具体平台。',
        )


class GenericSchemeRecognizer:
    id = 'generic.scheme'

    def recognize(self, candidate):
        parsed = parse_url(candidate.raw_url)
        if parsed.scheme in ('http', 'https'):
            return None
        url = parsed.scheme + ':' + candidate.raw_url.partition(':')[2]
        return LinkInfo(
            candidate.raw_url,
            url,
            parsed.scheme,
            self.id,
            None,
            'scheme',
            'scheme:' + url,
            0.1,
            '仅记录协议地址；没有执行授权，须由专用处理器另行校验。',
        )
