"""Host setup, inspection, and backup. Never runs assessment tools."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid
from . import APP_NAME
from .store import Store, canonical, digest, private, utc
from .auth import add_user
from .origins import exact_origin, serving_port


BACKUP_TREES = ('artifacts', 'conflicts', 'exports', 'keys')
BACKUP_FILES = ('workspace.db', 'audit.jsonl', 'transcript.log')
BACKUP_EMPTY_TREES = ('staging',)


def _write_all(fd, data):
    """Write every byte, retrying only interrupted system calls."""
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except InterruptedError:
            continue
        if not isinstance(written, int) or written <= 0 or written > len(view):
            raise OSError('Backup copy stopped')
        view = view[written:]


def _regular_stat(path):
    value = Path(path).lstat()
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        raise ValueError('Backup or restore source contains a symlink or unsupported file')
    if value.st_uid != os.getuid() or value.st_mode & 0o077:
        raise ValueError('Backup or restore source has unsafe permissions')
    return value


def _read_regular(path, maximum):
    """Read one bounded private file without following a changed link."""
    path = Path(path)
    before = _regular_stat(path)
    if before.st_size > maximum:
        raise ValueError('Backup control file exceeds its limit')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise ValueError('Backup or restore source changed during inspection')
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b''.join(chunks)
        if len(value) != before.st_size or len(value) > maximum:
            raise ValueError('Backup control file changed or exceeds its limit')
        return value
    finally:
        os.close(fd)


def _digest_regular(path):
    path = Path(path)
    before = _regular_stat(path)
    checksum = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise ValueError('Backup or restore source changed during inspection')
        total = 0
        while chunk := os.read(fd, 1024 * 1024):
            checksum.update(chunk)
            total += len(chunk)
        if total != before.st_size:
            raise ValueError('Backup or restore source changed during inspection')
        return checksum.hexdigest()
    finally:
        os.close(fd)


def _manifest_file(path):
    value = _regular_stat(path)
    return {'sha256': _digest_regular(path), 'size': value.st_size}


def _matches_manifest_file(path, entry):
    return _regular_stat(path).st_size == entry['size'] and _digest_regular(path) == entry['sha256']


def _no_follow_files(root, names):
    """Return regular files below approved private trees without following links."""
    root = Path(root)
    found = []
    for name in names:
        start = root / name
        try:
            start_stat = start.lstat()
        except FileNotFoundError:
            raise ValueError(f'Missing required backup tree: {name}')
        if stat.S_ISLNK(start_stat.st_mode):
            raise ValueError('Backup or restore source contains a symlink')
        if not stat.S_ISDIR(start_stat.st_mode):
            raise ValueError(f'Backup tree is not a directory: {name}')
        if start_stat.st_uid != os.getuid() or start_stat.st_mode & 0o077:
            raise ValueError('Backup or restore source has unsafe permissions')
        pending = [start]
        while pending:
            directory = pending.pop()
            for entry in os.scandir(directory):
                path = Path(entry.path)
                value = path.lstat()
                if stat.S_ISLNK(value.st_mode):
                    raise ValueError('Backup or restore source contains a symlink')
                if stat.S_ISDIR(value.st_mode):
                    if value.st_uid != os.getuid() or value.st_mode & 0o077:
                        raise ValueError('Backup or restore source has unsafe permissions')
                    pending.append(path)
                elif stat.S_ISREG(value.st_mode):
                    if value.st_mode & 0o077:
                        raise ValueError('Backup or restore source has unsafe permissions')
                    found.append(path)
                else:
                    raise ValueError('Backup or restore source contains an unsupported file type')
    return sorted(found)


def _copy_regular(source, target):
    """Copy one lstat-checked regular file with no symlink dereference."""
    source = Path(source)
    target = Path(target)
    source_stat = _regular_stat(source)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    target_fd = -1
    try:
        opened = os.fstat(source_fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (source_stat.st_dev, source_stat.st_ino, source_stat.st_size):
            raise ValueError('Backup or restore source changed during inspection')
        target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            _write_all(target_fd, chunk)
        os.fsync(target_fd)
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        os.close(source_fd)


def _backup_payload_files(root, expect_manifest=False):
    """Validate the exact top-level backup shape and return payload files."""
    root = Path(root)
    expected_directories = set(BACKUP_TREES) | set(BACKUP_EMPTY_TREES)
    expected_files = set(BACKUP_FILES)
    if expect_manifest:
        expected_files.add('backup-manifest.json')
    seen = set()
    for entry in os.scandir(root):
        value = entry.stat(follow_symlinks=False)
        name = entry.name
        seen.add(name)
        if stat.S_ISLNK(value.st_mode):
            raise ValueError('Backup or restore source contains a symlink')
        if name in expected_directories:
            if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid() or value.st_mode & 0o077:
                raise ValueError('Backup or restore source has unsafe permissions')
        elif name in expected_files:
            _regular_stat(root / name)
        else:
            raise ValueError('The backup file list changed')
    if seen != expected_directories | expected_files:
        raise ValueError('The backup file list changed')
    for name in BACKUP_EMPTY_TREES:
        with os.scandir(root / name) as entries:
            if next(entries, None) is not None:
                raise ValueError('The backup file list changed')
    return _no_follow_files(root, BACKUP_TREES) + [root / name for name in BACKUP_FILES]


def _write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def encrypted_storage(path):
    if os.environ.get('CONTAINERIZED') == '1':
        marker = Path(path) / '.storage-verified.json'
        try:
            private(marker)
            data = json.loads(marker.read_text())
            return set(data) == {'schema_version', 'encrypted', 'verified_at'} and data['schema_version'] == 1 and data['encrypted'] is True
        except Exception:
            return False
    try:
        source = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '-T', str(path)], timeout=5, stderr=subprocess.DEVNULL).decode().strip()
        types = subprocess.check_output(['lsblk', '-sn', '-o', 'TYPE', source.split('[')[0]], timeout=5, stderr=subprocess.DEVNULL).decode().split()
        return 'crypt' in types
    except Exception:
        return False


def initialize(root, origin, engagement, mode='synthetic', engagement_id=None, peer_origin=None):
    store = Store(root)
    if store.setting('config'):
        raise ValueError('This workspace is already initialized. Keep its history.')
    origin, _ = exact_origin(origin, mode, 'browser')
    peer_origin, _ = exact_origin(peer_origin or origin, mode, 'peer')
    if mode == 'client' and not encrypted_storage(store.root):
        raise ValueError('Client mode requires an approved encrypted block-device workspace')
    store.configure('config', dict(app=APP_NAME, origin=origin, peer_origin=peer_origin, mode=mode, instance_id=str(uuid.uuid4()), engagement={'id': str(uuid.UUID(engagement_id)) if engagement_id else str(uuid.uuid4()), 'name': engagement}, initialized_at=utc()))
    return store


def backup(store, destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination == store.root or store.root in destination.parents:
        raise ValueError('Choose a new backup folder outside the workspace')
    if store.setting('config')['mode'] == 'client' and not encrypted_storage(destination.parent):
        raise ValueError('Back up client records only to approved encrypted storage')
    with store.lock:
        sources = _no_follow_files(store.root, BACKUP_TREES)
        for name in ('audit.jsonl', 'transcript.log'):
            _regular_stat(store.root / name)
        store.verify()
        staging = destination.with_name(f'.{destination.name}.backup-{uuid.uuid4()}')
        staging.mkdir(mode=0o700)
        try:
            with store.connect() as source:
                target = sqlite3.connect(staging / 'workspace.db')
                try:
                    source.backup(target)
                finally:
                    target.close()
            with sqlite3.connect(staging / 'workspace.db') as copied:
                copied.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                if copied.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
                    raise RuntimeError('The backup database could not be sealed')
            if any((staging / ('workspace.db' + suffix)).exists() for suffix in ('-wal', '-shm')):
                raise RuntimeError('The backup database could not be sealed without live sidecar files')
            (staging / 'workspace.db').chmod(0o600)
            for name in BACKUP_TREES + BACKUP_EMPTY_TREES:
                (staging / name).mkdir(mode=0o700)
            for source in sources:
                relative = source.relative_to(store.root)
                _copy_regular(source, staging / relative)
            for name in ('audit.jsonl', 'transcript.log'):
                _copy_regular(store.root / name, staging / name)
            restored = Store(staging, readonly=True)
            restored.verify()
            payload = _backup_payload_files(staging)
            files = {str(path.relative_to(staging)): _manifest_file(path) for path in payload}
            manifest = {
                'schema_version': 2, 'app': APP_NAME, 'created_at': utc(),
                'source_instance': store.setting('config')['instance_id'], 'files': files,
            }
            _write_private(staging / 'backup-manifest.json', canonical(manifest).encode())
            _backup_payload_files(staging, expect_manifest=True)
            os.replace(staging, destination)
            directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
    return {'files': len(files), 'manifest_hash': digest(manifest)}


def restore(source, destination):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if not source.is_dir() or source.is_symlink():
        raise ValueError('Choose a private backup directory')
    private(source, True)
    if destination.exists() or destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('Choose a new restore folder outside the backup')
    payload = _backup_payload_files(source, expect_manifest=True)
    manifest_path = source / 'backup-manifest.json'
    manifest = json.loads(_read_regular(manifest_path, 1024 * 1024))
    if set(manifest) != {'schema_version', 'app', 'created_at', 'source_instance', 'files'} or manifest['schema_version'] != 2 or manifest['app'] != APP_NAME or not isinstance(manifest['files'], dict):
        raise ValueError('The backup manifest is not supported by this application')
    files = manifest['files']
    expected = set(files)
    actual = {str(path.relative_to(source)) for path in payload}
    if expected != actual or not expected:
        raise ValueError('The backup file list changed')
    for relative, entry in files.items():
        relative_path = Path(relative)
        if (relative_path.is_absolute() or '..' in relative_path.parts or not isinstance(entry, dict) or set(entry) != {'sha256', 'size'}
                or not isinstance(entry['sha256'], str) or len(entry['sha256']) != 64
                or type(entry['size']) is not int or entry['size'] < 0):
            raise ValueError('The backup manifest contains an unsafe file entry')
        path = source / relative_path
        if not _matches_manifest_file(path, entry):
            raise ValueError('A backup file failed its integrity check')
    database = source / 'workspace.db'
    connection = sqlite3.connect(f'file:{database}?mode=ro&immutable=1', uri=True)
    try:
        row = connection.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        config = json.loads(row[0]) if row else None
    finally:
        connection.close()
    if not config or config.get('app') != APP_NAME or config.get('instance_id') != manifest['source_instance']:
        raise ValueError('The backup identity does not match its manifest')
    parent = destination.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if config.get('mode') == 'client' and not encrypted_storage(parent):
        raise ValueError('Restore client records only to approved encrypted storage')
    staging = destination.with_name(f'.{destination.name}.restore-{uuid.uuid4()}')
    staging.mkdir(mode=0o700)
    try:
        for name in ('artifacts', 'conflicts', 'keys', 'staging', 'exports'):
            (staging / name).mkdir(mode=0o700)
        for relative in sorted(files):
            source_path = source / relative
            target = staging / relative
            _copy_regular(source_path, target)
            if not _matches_manifest_file(target, files[relative]):
                raise ValueError('A restored file failed its integrity check')
        if config.get('mode') == 'client':
            marker_source = parent / '.storage-verified.json'
            private(marker_source)
            _copy_regular(marker_source, staging / '.storage-verified.json')
        restored = Store(staging, readonly=True)
        if restored.setting('config') != config:
            raise ValueError('Restored configuration changed')
        restored.verify()
        os.replace(staging, destination)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {'files': len(files), 'manifest_hash': digest(manifest), 'destination': str(destination)}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog=APP_NAME.lower())
    parser.add_argument('--workspace', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init'); init.add_argument('--origin', required=True); init.add_argument('--peer-origin'); init.add_argument('--engagement', required=True); init.add_argument('--engagement-id'); init.add_argument('--mode', choices=['synthetic', 'client'], default='synthetic')
    user = sub.add_parser('add-user'); user.add_argument('name'); user.add_argument('--role', required=True)
    serve = sub.add_parser('serve'); serve.add_argument('--cert', type=Path); serve.add_argument('--key', type=Path); serve.add_argument('--bind')
    sub.add_parser('verify'); sub.add_parser('info'); sub.add_parser('keygen'); sub.add_parser('peer-card')
    peer = sub.add_parser('enroll'); peer.add_argument('card', type=Path); peer.add_argument('--fingerprint', required=True)
    back = sub.add_parser('backup'); back.add_argument('destination', type=Path)
    restore_parser = sub.add_parser('restore'); restore_parser.add_argument('source', type=Path)
    gw = sub.add_parser('ghostwriter'); gw.add_argument('--origin', required=True); gw.add_argument('--report-id', type=int, required=True); gw.add_argument('--severity-id', type=int, required=True); gw.add_argument('--finding-type-id', type=int, required=True); gw.add_argument('--token-file', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            store = initialize(args.workspace, args.origin, args.engagement, args.mode, args.engagement_id, args.peer_origin)
            config = store.setting('config')
            print(canonical({'status': 'initialized', 'engagement': config['engagement'], 'instance_id': config['instance_id']})); return
        if args.command == 'restore':
            print(canonical(restore(args.source, args.workspace))); return
        store = Store(
            args.workspace,
            readonly=args.command == 'backup',
            wal_aware_readonly=args.command == 'backup',
        )
        if not store.setting('config'):
            raise ValueError('Initialize this workspace first')
        if args.command == 'add-user':
            password = getpass.getpass('Password (at least 12 characters): ')
            if getpass.getpass('Repeat password: ') != password:
                raise ValueError('Passwords did not match')
            add_user(store, args.name, args.role, password, APP_NAME)
            print('User created.')
        elif args.command == 'info':
            config = store.setting('config')
            print(canonical({'app': config['app'], 'engagement': config['engagement'], 'instance_id': config['instance_id'], 'mode': config['mode'], 'origin': config['origin'], 'peer_origin': config.get('peer_origin', config['origin'])}))
        elif args.command == 'verify':
            print(canonical({'audit': store.verify(), 'encrypted_storage': encrypted_storage(store.root), 'mode': store.setting('config')['mode']}))
        elif args.command == 'keygen':
            from .transfer import provision_keys
            provision_keys(store); print('Transfer keys created in private host storage.')
        elif args.command == 'peer-card':
            from .transfer import public_card
            card = public_card(store); print(canonical(card)); print('Fingerprint: ' + digest(card), file=sys.stderr)
        elif args.command == 'enroll':
            from .transfer import enroll
            enroll(store, json.loads(args.card.read_text()), args.fingerprint); print('Peer enrolled.')
        elif args.command == 'backup':
            print(canonical(backup(store, args.destination)))
        elif args.command == 'ghostwriter':
            if APP_NAME != 'Merlin':
                raise ValueError('Configure Ghostwriter in Merlin')
            if args.token_file:
                private(args.token_file)
                if not args.token_file.is_file():
                    raise ValueError('The token file must be a private regular file')
                token = args.token_file.read_text().strip()
            else:
                token = getpass.getpass('Scoped Ghostwriter API token: ')
            if not token.strip():
                raise ValueError('The token is empty')
            p = store.root / 'keys' / 'ghostwriter.token'
            old_token = p.read_bytes() if p.exists() and not private(p) else None
            old_config = store.setting('ghostwriter', {})
            temporary = p.with_name('.ghostwriter.token.new')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(token); f.flush(); os.fsync(f.fileno())
            os.replace(temporary, p)
            config = dict(origin=args.origin, report_id=str(args.report_id), severity_id=args.severity_id, finding_type_id=args.finding_type_id)
            try:
                store.configure('ghostwriter', config)
                from .ghostwriter_adapter import ADAPTER, verify_adapter
                schema_hash = verify_adapter(store)
            except Exception:
                store.configure('ghostwriter', old_config)
                if old_token is None:
                    p.unlink(missing_ok=True)
                else:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, 'wb') as f:
                        f.write(old_token); f.flush(); os.fsync(f.fileno())
                    os.replace(temporary, p)
                raise
            store.configure('ghostwriter', {**config, 'schema_hash': schema_hash, 'adapter_id': ADAPTER})
            print('Ghostwriter schema verified. File-attachment live acceptance is still required.')
        elif args.command == 'serve':
            import fcntl
            lockfile = open(store.root / 'server.lock', 'a')
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _, origin = exact_origin(store.setting('config')['origin'], store.setting('config')['mode'], 'browser')
            cert = args.cert or (Path(os.environ['APP_TLS_CERT']) if os.environ.get('APP_TLS_CERT') else None)
            key = args.key or (Path(os.environ['APP_TLS_KEY']) if os.environ.get('APP_TLS_KEY') else None)
            if origin.scheme == 'https' and (not cert or not key):
                raise ValueError('Provide the TLS certificate and private key')
            if key:
                private(key)
            import uvicorn
            from .server import create_app
            uvicorn.run(create_app(args.workspace), host=args.bind or os.environ.get('APP_BIND') or origin.hostname, port=serving_port(origin, os.environ.get('APP_PORT')), ssl_certfile=str(cert) if cert else None, ssl_keyfile=str(key) if key else None, workers=1, proxy_headers=False, access_log=False)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f'Cannot finish: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
