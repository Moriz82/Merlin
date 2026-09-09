"""Exact application origins for browser, peer, and synthetic lab traffic."""
from urllib.parse import urlsplit


LOOPBACK_HOSTS = {'127.0.0.1', '::1', 'localhost'}
SYNTHETIC_HOSTS = {
    'peer': {'harbinger', 'merlin'},
    'ghostwriter': {'ghostwriter'},
}


def exact_origin(value, mode, purpose='browser'):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError('Origin is required')
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError('Use an origin without credentials, paths, query strings, or fragments')
    if parsed.scheme == 'https':
        return value.rstrip('/'), parsed
    allowed = LOOPBACK_HOSTS if purpose == 'browser' else LOOPBACK_HOSTS | SYNTHETIC_HOSTS.get(purpose, set())
    if mode == 'synthetic' and parsed.scheme == 'http' and parsed.hostname in allowed:
        return value.rstrip('/'), parsed
    raise ValueError('Use HTTPS. Synthetic workspaces may use only admitted local HTTP origins.')


def serving_port(origin, configured=None):
    expected = origin.port or (443 if origin.scheme == 'https' else 80)
    if configured in (None, ''):
        return expected
    if not isinstance(configured, str) or not configured.isdecimal() or not 1 <= int(configured) <= 65535:
        raise ValueError('APP_PORT must be a valid TCP port')
    if int(configured) != expected:
        raise ValueError('APP_PORT must match the approved browser origin port')
    return expected
