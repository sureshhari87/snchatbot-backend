"""Optional hosted product videos; metadata validation only, no remote fetching."""

from urllib.parse import urlsplit


def video_urls(raw):
    if raw is None or raw == "":
        return []
    values = raw.splitlines() if isinstance(raw, str) else raw
    if not isinstance(values, list) or len(values) > 5:
        raise ValueError("Use at most 5 product video URLs")
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Product video URLs must be text")
        value = value.strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            valid = (
                len(value) <= 2048
                and parsed.scheme == "https"
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not any(char.isspace() or ord(char) < 32 for char in value)
            )
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use a direct HTTPS product video URL without credentials")
        if value not in result:
            result.append(value)
    return result
