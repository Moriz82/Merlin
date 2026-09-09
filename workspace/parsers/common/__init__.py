"""Shared parsing policy and normalized result helpers.

This package contains no collector or transport code.  Importers receive raw
bytes from the dispatcher and return the same normalized dictionaries used by
the original parser.
"""
import base64
import io
import re
import stat
from pathlib import PurePosixPath
from urllib.parse import urlsplit
import zipfile


TRACKS = ('network', 'web', 'linux', 'windows_ad', 'database_service')
MAX_RECORDS = 250000
SECRET_NAME = r'(?:pass(?:word|wd)?|pwd|secret|client[-_ ]?secret|(?:x[-_ ]?)?api[-_ ]?key|access[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|id[-_ ]?token|token|authorization|proxy[-_ ]?authorization|cookie|set[-_ ]?cookie|session(?:[-_ ]?id)?|private[-_ ]?key|credentials?)'
SECRET = re.compile(rf'(?i)(?:["\']?{SECRET_NAME}["\']?\s*[:=]\s*["\']?\S+|\bbearer\s+[A-Za-z0-9._~+/=-]{{8,}}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
NAMED_SECRET = re.compile(rf'(?i)"name"\s*:\s*"{SECRET_NAME}"')


def secret_bearing(raw):
    text = raw.decode('utf-8', 'replace')
    return bool(SECRET.search(text) or NAMED_SECRET.search(text))


def safe_text(raw, limit=65536):
    text = raw[:limit].decode('utf-8', 'replace')
    return ''.join(c if c in '\n\t' or ord(c) >= 32 and ord(c) != 127 else f'\\x{ord(c):02x}' for c in text)


def archive(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > 1024**3:
            raise ValueError('Archive exceeds the collection limit')
        names = set()
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            if name in names or not parts or name.startswith('/') or '\\' in name or ':' in name or any(p in ('..', '.') for p in parts) or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Unsafe or duplicate archive entry')
            names.add(name)
            if info.is_dir():
                continue
            if not name.lower().endswith('.json') or info.flag_bits & 1:
                raise ValueError('Only unencrypted JSON files are admitted in BloodHound archives')
            body = z.read(info)
            if body.startswith((b'PK\x03\x04', b'SQLite format 3')):
                raise ValueError('Nested archives and database files are not admitted')
            yield name, body


class ParseContext:
    """Mutable normalized result state shared by dedicated importers."""

    def __init__(self, result):
        self.result = result
        self.known = set()

    def asset(self, id, label, kind='host', track='network', **facts):
        id, label = str(id), str(label)
        if len(id) > 2048 or len(label) > 2048:
            raise ValueError('Asset identifier exceeds the limit')
        if id not in self.known:
            self.known.add(id)
            self.result['assets'].append(dict(id=id, label=label, kind=kind, track=track, data=facts))
        return id

    def relation(self, source, target, label):
        self.result['relationships'].append(dict(source=source, target=target, label=label))

    def observed(self, subject, summary, location, **facts):
        self.result['observations'].append(dict(subject=subject, summary=str(summary)[:4096], location=str(location), facts=facts))

    def service(self, host, port, protocol, name='', product='', version=''):
        port = int(port)
        if not 1 <= port <= 65535 or protocol not in ('tcp', 'udp', 'sctp'):
            raise ValueError('Invalid service endpoint')
        track = 'database_service' if name.lower() in ('mysql', 'postgresql', 'ms-sql-s', 'mongodb', 'redis', 'oracle') else 'network'
        sid = self.asset(f'{host}/{protocol}/{port}', f'{port}/{protocol} {name}'.strip(), 'service', track, port=port, protocol=protocol, name=name, product=product, version=version)
        self.relation(host, sid, 'Observed service')

    def web(self, url, status=None):
        u = urlsplit(str(url))
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            raise ValueError('Invalid or credential-bearing URL')
        origin = f'{u.scheme}://{u.netloc}'
        aid = self.asset(origin, origin, 'application', 'web')
        rid = self.asset(origin + (u.path or '/'), u.path or '/', 'route', 'web', status=status)
        if aid != rid:
            self.relation(aid, rid, 'Imported route')
        if u.query:
            self.result['limitations'].append('URL query values are omitted from normal views.')
        return rid


def new_result(raw):
    return dict(assets=[], relationships=[], observations=[], limitations=[], complete=True, quarantined=secret_bearing(raw))


__all__ = ['MAX_RECORDS', 'ParseContext', 'TRACKS', 'archive', 'new_result', 'safe_text', 'secret_bearing']
