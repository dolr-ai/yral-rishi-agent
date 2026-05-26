import logging
import os
import re
import socket
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

_SENSITIVE_QUERY_KEYS = {
    "key", "api_key", "apikey", "token", "access_token",
    "auth", "secret", "password", "signature",
}

_URL_IN_TEXT_RE = re.compile(r"https?://\S+")


def _redact_url(url: str) -> str:
    if not isinstance(url, str) or "?" not in url:
        return url
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        cleaned = [
            (k, "[REDACTED]" if k.lower() in _SENSITIVE_QUERY_KEYS else v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunparse(parsed._replace(query=urlencode(cleaned)))
    except Exception:
        return url


def _redact_urls_in_text(text: str) -> str:
    if not isinstance(text, str) or "http" not in text:
        return text
    return _URL_IN_TEXT_RE.sub(lambda m: _redact_url(m.group(0)), text)


def _scrub_breadcrumb(crumb, _hint):
    data = crumb.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("url"), str):
        data["url"] = _redact_url(data["url"])
    msg = crumb.get("message")
    if isinstance(msg, str):
        crumb["message"] = _redact_urls_in_text(msg)
    return crumb


def _scrub_event(event, _hint):
    req = event.get("request")
    if isinstance(req, dict) and isinstance(req.get("url"), str):
        req["url"] = _redact_url(req["url"])

    tags = event.get("tags")
    if isinstance(tags, dict):
        if isinstance(tags.get("url"), str):
            tags["url"] = _redact_url(tags["url"])
    elif isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, list) and len(tag) == 2 and tag[0] == "url" and isinstance(tag[1], str):
                tag[1] = _redact_url(tag[1])

    bc = event.get("breadcrumbs")
    if isinstance(bc, dict):
        for crumb in bc.get("values", []) or []:
            _scrub_breadcrumb(crumb, None)
    return event


def init_sentry() -> None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("SENTRY_RELEASE"),
        server_name=socket.gethostname(),
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_RATE", "1.0")),
        profiles_sample_rate=float(os.environ.get("SENTRY_PROFILES_RATE", "1.0")),
        send_default_pii=False,
        attach_stacktrace=True,
        before_breadcrumb=_scrub_breadcrumb,
        before_send=_scrub_event,
    )
