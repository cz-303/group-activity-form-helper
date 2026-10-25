"""Static composition and v0.8 compatibility. Generic links never enter its queue."""

from app_core.recognition import RecognitionEngine
from app_core.models import LinkCandidate
from app_core.url_candidates import MAX_URL_LENGTH
from providers.generic_links import GenericSchemeRecognizer, GenericWebRecognizer
from providers.document_links import DocumentRecognizer
from providers.tencent_docs import PROVIDER_ID, TencentDocsRecognizer, canonical_form_url, legacy_candidates


def create_recognition_engine():
    return RecognitionEngine(
        (TencentDocsRecognizer(), DocumentRecognizer(), GenericWebRecognizer(), GenericSchemeRecognizer())
    )


_engine = create_recognition_engine()


def canonical_legacy_url(raw):
    # Storage, navigation and authorization keep the exact old identity rule.
    return canonical_form_url(raw)


def legacy_links_from_text(text):
    urls = set()
    for candidate in legacy_candidates(text):
        # Old capture accepts arbitrary query lengths, then discards the query.
        # Normalize with its existing rule before entering the bounded new API.
        legacy_url = canonical_form_url(candidate.raw_url)
        if not legacy_url:
            continue
        if len(legacy_url) > MAX_URL_LENGTH:
            # Do not retroactively impose the new API limit on legacy task IDs.
            urls.add(legacy_url)
            continue
        result = _engine.recognize(LinkCandidate(legacy_url))
        info = result.selected
        if info and info.provider_id == PROVIDER_ID and info.kind == 'form':
            urls.add(info.normalized_url)
    return sorted(urls)
