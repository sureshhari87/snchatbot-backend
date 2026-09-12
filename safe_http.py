"""HTTP(S)-only requests with same-origin redirects for configured integrations."""

import urllib.error
import urllib.parse
import urllib.request


def http_origin(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Integration URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Integration URL must not contain credentials")
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if http_origin(req.full_url) != http_origin(newurl):
            raise ValueError("Cross-origin integration redirect is not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_urlopen(request, timeout=10):
    http_origin(request.full_url)
    opener = urllib.request.build_opener(SameOriginRedirect())
    return opener.open(request, timeout=timeout)
