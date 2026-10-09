"""HTTP destination policy shared by page fetching and browser requests."""
import ipaddress
import socket
import urllib.parse
import urllib.request
from orchestration.tools.policy import current_policy

MAX_RESPONSE_BYTES = 2_000_000


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Only absolute HTTP/HTTPS URLs are supported.')
    if parsed.username or parsed.password:
        raise ValueError('Credentials must not be embedded in URLs.')
    host = parsed.hostname.lower()
    if host in current_policy().local_hosts:
        return url.strip()
    addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise PermissionError('Private, loopback, and special network destinations are blocked.')
    return url.strip()


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_open(request, timeout=15):
    validate_url(request.full_url)
    return urllib.request.build_opener(CheckedRedirect()).open(request, timeout=timeout)


def bounded_read(response):
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError('HTTP response exceeds the 2 MB limit.')
    return data
