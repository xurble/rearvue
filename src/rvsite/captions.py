from urllib.parse import urlparse

import bleach

CAPTION_TAGS = frozenset(
    {"a", "br", "blockquote", "code", "em", "li", "ol", "p", "pre", "strong", "ul"}
)
CAPTION_PROTOCOLS = frozenset({"http", "https", "mailto"})


def _is_absolute_http_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _allow_caption_attribute(tag, name, value):
    if tag == "a":
        if name in {"href", "title"}:
            return True
        return name == "target" and value == "_blank"
    if tag == "blockquote":
        if name == "class":
            return value == "twitter-tweet"
        if name == "cite":
            return _is_absolute_http_url(value)
    return False


def _protect_external_context(attrs, new=False):
    if new:
        return None
    if attrs.get((None, "target")) == "_blank":
        attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


def sanitize_caption_html(value):
    """Return untrusted caption HTML reduced to RearVue's display allowlist."""
    cleaned = bleach.clean(
        value or "",
        tags=CAPTION_TAGS,
        attributes=_allow_caption_attribute,
        protocols=CAPTION_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    return bleach.linkify(
        cleaned,
        callbacks=[_protect_external_context],
        parse_email=False,
    )
