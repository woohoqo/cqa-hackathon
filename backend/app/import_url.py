"""Validate organizer links without normalizing their signed query strings."""
from urllib.parse import urlsplit

import httpx


def valid_organizer_url(value: str) -> bool:
    if not value or len(value) > 8192 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    if '\\' in value:
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or (port is not None and not 1 <= port <= 65535)):
            return False
        # Also reject host/port forms the actual HTTP client cannot send.
        parsed = httpx.URL(value)
        return bool(parsed.host)
    except (ValueError, httpx.InvalidURL):
        return False
