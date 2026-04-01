from __future__ import annotations

from urllib.parse import quote, urlparse


def normalize_proxy_url(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None

    # Already a full proxy URL.
    if "://" in value:
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            raise ValueError("Invalid proxy URL. Expected scheme://[user:pass@]host:port")
        return value

    parts = value.split(":")
    if len(parts) == 2:
        host, port = parts
        _validate_port(port)
        if not host:
            raise ValueError("Invalid proxy format. Host is required.")
        return f"http://{host}:{port}"

    if len(parts) == 4:
        host, port, username, password = parts
        _validate_port(port)
        if not host or not username:
            raise ValueError("Invalid proxy format. Host and username are required.")
        user = quote(username, safe="")
        pwd = quote(password, safe="")
        return f"http://{user}:{pwd}@{host}:{port}"

    raise ValueError("Invalid proxy format. Expected host:port or host:port:user:pass or full URL.")


def redact_proxy_url(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    host = parsed.hostname or "unknown-host"
    port = parsed.port or 0
    scheme = parsed.scheme or "http"
    if parsed.username:
        return f"{scheme}://***:***@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def _validate_port(port: str) -> None:
    try:
        value = int(port)
    except ValueError as exc:
        raise ValueError("Invalid proxy format. Port must be an integer.") from exc
    if value < 1 or value > 65535:
        raise ValueError("Invalid proxy format. Port must be between 1 and 65535.")
