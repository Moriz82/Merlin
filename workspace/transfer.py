"""Signed, recipient-encrypted record exchange. No database file transfer."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import sqlite3
import subprocess
import time
import uuid
import zipfile
from datetime import datetime, timezone
from jsonschema import Draft202012Validator, FormatChecker
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import Response
import httpx
from .models import EvidenceApproval, Export, Finding, Draft, Question, ConflictResolution, MergeReview, TransferSelection
from .store import Conflict, canonical, digest, private, utc
from .parsers import FORMATS, TRACKS, secret_bearing
from .origins import exact_origin

KINDS = {'asset', 'relationship', 'observation', 'finding', 'lead', 'draft', 'upload', 'question', 'comment'}
MAX_BUNDLE = 256 * 1024**2
CONTRACT_SHA256 = 'a6bffbdee78c3adc748ac1c3b430878e47f1d8506e3683cf3f11c9ec9d3ecb93'
CONTRACT_PATH = Path(__file__).resolve().parent.parent / 'contracts' / 'team-transfer-v1.schema.json'
PROVENANCE = {'source_instance', 'source_revision_id'}
ORIGIN_KINDS = {
    'Harbinger': {'asset', 'relationship', 'observation', 'finding', 'lead', 'upload', 'comment'},
    'Merlin': {'draft', 'question', 'comment'},
}
ORDINARY_ARTIFACT = 'ordinary_evidence'
RESTRICTED_HARNESS_ARTIFACT = 'restricted_harness_envelope'
HARNESS_OBSERVATION_FORMAT = 'harness_observation_v1'
HARNESS_KIND_MARKER = b'harness_observation_v1'
HARNESS_KIND_PATTERN = re.compile(
    b''.join(
        b'(?:' + re.escape(bytes((character,))) + b'|\\\\u' + f'{character:04x}'.encode('ascii') + b')'
        for character in HARNESS_KIND_MARKER
    ),
    re.IGNORECASE,
)


class ReviewMismatch(Exception):
    """The records or enrolled recipient changed after operator review."""


def restricted_harness_original(raw):
    """Conservatively identify a signed harness original without importing harness code."""
    return isinstance(raw, (bytes, bytearray, memoryview)) and bool(HARNESS_KIND_PATTERN.search(bytes(raw)))


def peer_tls_context():
    """Load an optional operator-mounted CA bundle for peer HTTPS only."""
    value = os.environ.get('APP_PEER_CA_BUNDLE', '').strip()
    if not value:
        return True
    path = Path(value)
    try:
        if not path.is_absolute() or path.is_symlink():
            raise ValueError
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError
        if os.environ.get('CONTAINERIZED') == '1':
            resolved.relative_to('/tls')
        return ssl.create_default_context(cafile=str(resolved))
    except (OSError, ValueError, ssl.SSLError) as error:
        raise ValueError('The peer CA bundle is missing or outside the approved TLS mount') from error


def _uuid(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field} must be a UUID')
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError(f'{field} must be a UUID')


def _shape(data, required, optional=()):
    keys = set(data)
    if not set(required) <= keys or not keys <= set(required) | set(optional) | PROVENANCE:
        raise ValueError('Transfer record fields do not match the admitted type')
    for field in keys & PROVENANCE:
        _uuid(data[field], field)


def validate_record_data(kind, data):
    """Validate the typed record before it crosses or enters a host boundary."""
    if not isinstance(data, dict):
        raise ValueError('Transfer record data must be an object')
    if kind == 'asset':
        _shape(data, {'label', 'kind', 'track'}, {'id', 'data', 'source_artifact', 'source_locator'})
        if not all(isinstance(data[field], str) and 0 < len(data[field]) <= 2048 for field in ('label', 'kind', 'track')) or data['track'] not in TRACKS:
            raise ValueError('Invalid asset record')
        if 'data' in data and not isinstance(data['data'], dict):
            raise ValueError('Invalid asset facts')
    elif kind == 'relationship':
        _shape(data, {'source', 'target', 'label'}, {'source_artifact'})
        _uuid(data['source'], 'source'); _uuid(data['target'], 'target')
        if not isinstance(data['label'], str) or not 0 < len(data['label']) <= 4096:
            raise ValueError('Invalid relationship label')
    elif kind == 'observation':
        _shape(data, {'subject', 'summary', 'location', 'facts'}, {'source_artifact'})
        _uuid(data['subject'], 'subject')
        if not isinstance(data['summary'], str) or len(data['summary']) > 4096 or not isinstance(data['location'], str) or len(data['location']) > 4096 or not isinstance(data['facts'], dict):
            raise ValueError('Invalid observation record')
    elif kind == 'finding':
        local = {key: value for key, value in data.items() if key not in PROVENANCE}
        Finding.model_validate(local)
    elif kind == 'lead':
        finding_fields = set(Finding.model_fields)
        _shape(data, {'title', 'observation', 'finding_id', 'finding_revision_id', 'tester_id'}, finding_fields | {'finding_id', 'finding_revision_id', 'tester_id'})
        Finding.model_validate({key: value for key, value in data.items() if key in finding_fields})
        for field in ('finding_id', 'finding_revision_id', 'tester_id'):
            _uuid(data[field], field)
    elif kind == 'draft':
        Draft.model_validate({key: value for key, value in data.items() if key not in PROVENANCE})
    elif kind == 'upload':
        _shape(data, {'filename', 'format', 'status', 'sha256', 'size', 'artifact_id', 'quarantined', 'limitations'}, {'reviewed_for_export', 'merged_review', 'harness_trust', 'artifact_class'})
        artifact_class = data.get('artifact_class', RESTRICTED_HARNESS_ARTIFACT if data.get('format') == HARNESS_OBSERVATION_FORMAT else ORDINARY_ARTIFACT)
        if artifact_class not in {ORDINARY_ARTIFACT, RESTRICTED_HARNESS_ARTIFACT}:
            raise ValueError('Invalid artifact classification')
        if artifact_class == RESTRICTED_HARNESS_ARTIFACT or data.get('format') == HARNESS_OBSERVATION_FORMAT:
            raise ValueError('The signed harness original is restricted and cannot enter a transfer bundle')
        if not isinstance(data['filename'], str) or not 0 < len(data['filename']) <= 200 or data['format'] not in FORMATS or data['status'] not in {'preview', 'merged'}:
            raise ValueError('Invalid transferable upload state')
        if not isinstance(data['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', data['sha256']) or type(data['size']) is not int or not 0 <= data['size'] <= MAX_BUNDLE:
            raise ValueError('Invalid upload integrity fields')
        _uuid(data['artifact_id'], 'artifact_id')
        if 'merged_review' in data:
            MergeReview.model_validate(data['merged_review'])
        if 'harness_trust' in data:
            raise ValueError('Harness trust metadata cannot be attached to transferable raw evidence')
        if data['quarantined'] is not False or data.get('reviewed_for_export') is not True or not isinstance(data['limitations'], list) or any(not isinstance(item, str) or len(item) > 4096 for item in data['limitations']):
            raise ValueError('Upload was not admitted for transfer')
    elif kind == 'question':
        Question.model_validate({key: value for key, value in data.items() if key not in PROVENANCE})
        _uuid(data['finding_id'], 'finding_id')
    elif kind == 'comment':
        _shape(data, {'subject_id', 'text'}, {'owner_id'})
        _uuid(data['subject_id'], 'subject_id')
        if not isinstance(data['text'], str) or not 0 < len(data['text']) <= 10000:
            raise ValueError('Invalid comment')
    else:
        raise ValueError('Unsupported transfer record kind')
    for field in ('source_artifact',):
        if data.get(field):
            _uuid(data[field], field)


def record_references(kind, data):
    references = []
    if data.get('source_artifact'):
        references.append((data['source_artifact'], {'upload'}))
    if kind == 'relationship':
        references.extend((data[field], {'asset'}) for field in ('source', 'target'))
    elif kind == 'observation':
        references.append((data['subject'], {'asset'}))
    elif kind in ('finding', 'lead'):
        references.extend((value, {'asset'}) for value in data.get('asset_ids', []))
        references.extend((value, {'upload'}) for value in data.get('evidence_ids', []))
        if kind == 'lead':
            references.append((data['finding_id'], {'finding'}))
    elif kind == 'draft':
        references.extend((value, {'upload'}) for value in data.get('evidence_ids', []))
        if data.get('lead_id'):
            references.append((data['lead_id'], {'lead'}))
    elif kind == 'question':
        references.append((data['finding_id'], {'finding'}))
    elif kind == 'comment':
        references.append((data['subject_id'], {'asset', 'upload', 'finding', 'lead', 'draft', 'question'}))
    return references


def incoming_provenance(record, bundle_source):
    return (
        record['data'].get('source_instance', bundle_source),
        record['data'].get('source_revision_id', record['revision_id']),
    )


def local_provenance(record, local_instance):
    return (
        record['data'].get('source_instance', local_instance),
        record['data'].get('source_revision_id', record['revision_id']),
    )


def record_identity(record, default_source):
    source_instance, source_revision_id = incoming_provenance(record, default_source)
    data = {key: value for key, value in record['data'].items() if key not in PROVENANCE}
    return record['kind'], data, source_instance, source_revision_id


def authorized_origin(store, record, peer, config):
    kind, data, source_instance, source_revision_id = record_identity(record, peer['id'])
    if source_instance == peer['id']:
        allowed = ORIGIN_KINDS.get(peer.get('app'))
        if not allowed or kind not in allowed:
            raise ValueError('The sending application cannot originate this record kind')
        return
    if source_instance != config['instance_id']:
        raise ValueError('The sending peer cannot forward a third-party record')
    local = store.get(record['id'])
    if not local or record_identity(local, config['instance_id']) != (kind, data, source_instance, source_revision_id):
        raise ValueError('A returned local record does not match its owned revision')


def validate_receipt(receipt, expected):
    required = {'status', 'imported', 'duplicates', 'conflicts', 'deferred', 'bundle_id', 'manifest_hash'}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ValueError('Peer receipt has an unsupported shape')
    if receipt['bundle_id'] != expected['bundle_id'] or receipt['manifest_hash'] != expected['manifest_hash']:
        raise ValueError('Peer receipt does not match the sent bundle')
    if receipt['status'] not in ('imported', 'conflict') or type(receipt['imported']) is not int or type(receipt['duplicates']) is not int or receipt['imported'] < 0 or receipt['duplicates'] < 0 or not isinstance(receipt['conflicts'], list) or not isinstance(receipt['deferred'], list):
        raise ValueError('Peer receipt has invalid counts or state')
    expected_ids = set(expected.get('record_ids', []))
    if not expected_ids:
        raise ValueError('Sent bundle metadata is incomplete')
    for field in ('conflicts', 'deferred'):
        if len(receipt[field]) != len(set(receipt[field])):
            raise ValueError('Peer receipt repeats record identifiers')
        for value in receipt[field]:
            _uuid(value, field[:-1])
            if value not in expected_ids:
                raise ValueError('Peer receipt names a record outside the sent bundle')
    if set(receipt['conflicts']) & set(receipt['deferred']):
        raise ValueError('Peer receipt record states overlap')
    if receipt['status'] == 'conflict':
        consistent = receipt['imported'] == 0 and bool(receipt['conflicts']) and receipt['duplicates'] + len(receipt['conflicts']) + len(receipt['deferred']) == len(expected_ids)
    else:
        consistent = not receipt['conflicts'] and not receipt['deferred'] and receipt['imported'] + receipt['duplicates'] == len(expected_ids)
    if not consistent:
        raise ValueError('Peer receipt state is inconsistent')
    return receipt


def validate_manifest(manifest):
    try:
        raw = CONTRACT_PATH.read_bytes()
        if hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
            raise ValueError('Transfer contract hash does not match this release')
        schema = json.loads(raw)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
        timestamps = [manifest['created_at'], *(record['updated_at'] for record in manifest['records'])]
        for value in timestamps:
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', value):
                raise ValueError('Transfer timestamps must use RFC3339 UTC with microseconds')
            if datetime.fromisoformat(value[:-1] + '+00:00').utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError('Transfer timestamp is not UTC')
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('Transfer manifest does not satisfy the versioned contract') from error


def age_bin(name='age'):
    path = shutil.which(name)
    if not path:
        raise RuntimeError('Install the pinned age tools before file exchange.')
    return path


def provision_keys(store):
    signing = store.root / 'keys' / 'signing.key'
    if not signing.exists():
        key = Ed25519PrivateKey.generate()
        fd = os.open(signing, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
    identity = store.root / 'keys' / 'age.key'
    if not identity.exists():
        subprocess.run([age_bin('age-keygen'), '-o', str(identity)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        identity.chmod(0o600)


def public_card(store):
    private(store.root / 'keys' / 'signing.key')
    private(store.root / 'keys' / 'age.key')
    key = Ed25519PrivateKey.from_private_bytes((store.root / 'keys' / 'signing.key').read_bytes())
    recipient = subprocess.check_output([age_bin('age-keygen'), '-y', str(store.root / 'keys' / 'age.key')], timeout=15, stderr=subprocess.DEVNULL).decode().strip()
    config = store.setting('config')
    return dict(schema_version=1, id=config['instance_id'], name=config['app'], app=config['app'], engagement_id=config['engagement']['id'], origin=config.get('peer_origin', config['origin']), signing_key=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode(), recipient=recipient)


def enroll(store, card, fingerprint):
    if set(card) != {'schema_version', 'id', 'name', 'app', 'engagement_id', 'origin', 'signing_key', 'recipient'} or card['schema_version'] != 1:
        raise ValueError('Unsupported peer card')
    config = store.setting('config')
    if digest(card) != fingerprint or card['engagement_id'] != config['engagement']['id']:
        raise ValueError('Peer fingerprint or engagement does not match')
    if card['app'] not in ORIGIN_KINDS or config.get('app') not in ORIGIN_KINDS or card['app'] == config['app'] or card['id'] == config['instance_id']:
        raise ValueError('Enroll one distinct Harbinger and Merlin instance')
    if not card['recipient'].startswith('age1') or len(base64.b64decode(card['signing_key'], validate=True)) != 32:
        raise ValueError('Invalid peer keys')
    exact_origin(card['origin'], store.setting('config')['mode'], 'peer')
    peers = store.setting('peers', [])
    if any(p['id'] == card['id'] and p != card for p in peers):
        raise ValueError('Peer identity changed. Review the old enrollment first.')
    if card not in peers:
        store.configure('peers', peers + [card])


def _peer_http_document(source_instance, recipient_instance, timestamp, nonce, content_length, sha256):
    return {
        'schema': 'team-peer-http-v1',
        'method': 'POST',
        'path': '/peer/inbox',
        'source_instance': source_instance,
        'recipient_instance': recipient_instance,
        'timestamp': timestamp,
        'nonce': nonce,
        'content_length': content_length,
        'sha256': sha256,
    }


def peer_http_headers(store, peer, data):
    """Authenticate a fixed peer request before the receiver reads its body."""
    if not isinstance(data, bytes) or len(data) > MAX_BUNDLE + 1024**2:
        raise ValueError('Peer request body exceeds the admitted limit')
    config = store.setting('config')
    _uuid(config['instance_id'], 'source_instance')
    _uuid(peer.get('id'), 'recipient_instance')
    timestamp = int(time.time())
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(data).hexdigest()
    document = _peer_http_document(config['instance_id'], peer['id'], timestamp, nonce, len(data), body_hash)
    signing = store.root / 'keys' / 'signing.key'
    private(signing)
    key = Ed25519PrivateKey.from_private_bytes(signing.read_bytes())
    signature = base64.b64encode(key.sign(canonical(document).encode())).decode()
    return {
        'Content-Type': 'application/octet-stream',
        'Content-Length': str(len(data)),
        'X-Team-Source': config['instance_id'],
        'X-Team-Recipient': peer['id'],
        'X-Team-Timestamp': str(timestamp),
        'X-Team-Nonce': nonce,
        'X-Team-Content-SHA256': body_hash,
        'X-Team-Signature': signature,
    }


def authorize_peer_headers(store, headers):
    """Verify and consume one signed peer request nonce before body streaming."""
    def header(name):
        value = headers.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError('Peer request authentication is incomplete')
        return value

    source = header('X-Team-Source')
    recipient = header('X-Team-Recipient')
    timestamp_text = header('X-Team-Timestamp')
    nonce = header('X-Team-Nonce')
    content_hash = header('X-Team-Content-SHA256')
    signature_text = header('X-Team-Signature')
    length_text = header('Content-Length')
    _uuid(source, 'source_instance')
    _uuid(recipient, 'recipient_instance')
    _uuid(nonce, 'nonce')
    if not timestamp_text.isdecimal() or len(timestamp_text) > 12:
        raise ValueError('Peer request timestamp is invalid')
    timestamp = int(timestamp_text)
    now = int(time.time())
    if abs(now - timestamp) > 60:
        raise ValueError('Peer request timestamp is outside the allowed window')
    if not length_text.isdecimal() or len(length_text) > 12:
        raise ValueError('Peer request length is invalid')
    content_length = int(length_text)
    if content_length > MAX_BUNDLE + 1024**2:
        raise ValueError('Peer request length exceeds the admitted limit')
    if not re.fullmatch(r'[0-9a-f]{64}', content_hash):
        raise ValueError('Peer request checksum is invalid')
    config = store.setting('config')
    if recipient != config['instance_id']:
        raise ValueError('Peer request recipient does not match this host')
    peer = next((item for item in store.setting('peers', []) if item.get('id') == source), None)
    if peer is None:
        raise ValueError('Peer request source is not enrolled')
    document = _peer_http_document(source, recipient, timestamp, nonce, content_length, content_hash)
    try:
        public = base64.b64decode(peer['signing_key'], validate=True)
        signature = base64.b64decode(signature_text, validate=True)
        if len(public) != 32 or len(signature) != 64:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public).verify(signature, canonical(document).encode())
    except (InvalidSignature, ValueError, TypeError):
        raise ValueError('Peer request signature is invalid')
    try:
        with store.tx() as connection:
            connection.execute('DELETE FROM peer_http_nonces WHERE created<?', (now - 300,))
            connection.execute('INSERT INTO peer_http_nonces VALUES(?,?,?)', (source, nonce, now))
            store.event(connection, 'paired-host', 'peer.request_authorized', [], source_instance=source, bytes=content_length)
    except sqlite3.IntegrityError:
        raise ValueError('Peer request was replayed')
    return {'source_instance': source, 'content_length': content_length, 'sha256': content_hash}


def _setting(connection, key, default=None):
    row = connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _transfer_snapshot(store, ids, recipient_id, connection):
    peers = {peer['id']: peer for peer in _setting(connection, 'peers', [])}
    if recipient_id not in peers:
        raise ValueError('Enroll the receiving host first')
    peer = peers[recipient_id]
    config = _setting(connection, 'config')
    selected = sorted(set(ids))
    if not selected:
        raise ValueError('Choose one or more transfer records')
    records, files, pending = {}, {}, list(selected)
    while pending:
        id = pending.pop()
        if id in records:
            continue
        r = store.get(id, connection)
        if not r or r['kind'] not in KINDS or len(records) >= 1000:
            raise ValueError('Unsupported or excessive transfer selection')
        d = r['data']
        validate_record_data(r['kind'], d)
        kind, _, source_instance, _ = record_identity(r, config['instance_id'])
        if source_instance == config['instance_id']:
            if kind not in ORIGIN_KINDS.get(config.get('app'), set()):
                raise ValueError('This application cannot originate the selected record kind')
        elif source_instance != recipient_id:
            raise ValueError('Forwarded records can return only to their source owner')
        if secret_bearing(canonical(d).encode()):
            raise ValueError('Review secret-bearing text before transfer')
        records[id] = r
        for reference, expected_kinds in record_references(r['kind'], d):
            referenced = store.get(reference, connection)
            if not referenced:
                raise ValueError('Transfer record has a missing referenced record')
            if referenced['kind'] not in expected_kinds:
                raise ValueError('Transfer reference points to the wrong record type')
            pending.append(reference)
        if r['kind'] == 'upload':
            if d.get('quarantined') or not d.get('reviewed_for_export'):
                raise ValueError('A host reviewer must approve each selected artifact for export')
            if d['artifact_id'] != id:
                raise ValueError('Evidence identity does not match its record')
            path = store.root / 'artifacts' / d['artifact_id']
            private(path)
            if not path.is_file() or path.stat().st_size != d['size']:
                raise ValueError('Evidence size changed after review')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != d['sha256'] or secret_bearing(raw):
                raise ValueError('Artifact changed or requires secret review')
            if restricted_harness_original(raw):
                raise ValueError('The signed harness original is restricted and cannot enter a transfer bundle')
            files[id] = raw
    ordered_records = [records[id] for id in sorted(records)]
    file_manifest = [
        dict(id=id, size=len(files[id]), sha256=hashlib.sha256(files[id]).hexdigest())
        for id in sorted(files)
    ]
    review_document = {
        'schema': 'team-transfer-review-v1',
        'contract_sha256': CONTRACT_SHA256,
        'engagement_id': config['engagement']['id'],
        'source_instance': config['instance_id'],
        'selected_record_ids': selected,
        'recipient_id': recipient_id,
        'recipient_configuration': peer,
        'records': ordered_records,
        'files': file_manifest,
    }
    return {
        'review_hash': digest(review_document),
        'selected_record_ids': selected,
        'recipient': peer,
        'config': config,
        'records': ordered_records,
        'files': files,
        'file_manifest': file_manifest,
    }


def preview_bundle(store, ids, recipient_id):
    with store.lock, store.connect() as connection:
        connection.execute('BEGIN')
        snapshot = _transfer_snapshot(store, ids, recipient_id, connection)
    selected = set(snapshot['selected_record_ids'])
    peer = snapshot['recipient']
    return {
        'review_hash': snapshot['review_hash'],
        'selected_record_ids': snapshot['selected_record_ids'],
        'recipient': {
            'id': peer['id'], 'name': peer['name'], 'app': peer['app'],
            'origin': peer['origin'],
        },
        'records': [{**record, 'selected': record['id'] in selected} for record in snapshot['records']],
        'files': snapshot['file_manifest'],
    }


def _cache_paths(store, review_hash):
    base = store.root / 'exports' / f'team-transfer-{review_hash}'
    return base.with_suffix('.age'), base.with_suffix('.json')


def _load_cached_bundle(store, review_hash):
    encrypted_path, metadata_path = _cache_paths(store, review_hash)
    if encrypted_path.exists() != metadata_path.exists():
        raise RuntimeError('A cached transfer is incomplete. Preserve the workspace and inspect it.')
    if not encrypted_path.exists():
        return None
    private(encrypted_path); private(metadata_path)
    if not encrypted_path.is_file() or not metadata_path.is_file():
        raise RuntimeError('A cached transfer path changed. Preserve the workspace and inspect it.')
    metadata = json.loads(metadata_path.read_text())
    required = {'bundle_id', 'manifest_hash', 'record_ids', 'recipient_id', 'review_hash', 'sha256', 'bytes'}
    encrypted = encrypted_path.read_bytes()
    if (
        set(metadata) != required
        or metadata.get('review_hash') != review_hash
        or metadata.get('bytes') != len(encrypted)
        or metadata.get('sha256') != hashlib.sha256(encrypted).hexdigest()
    ):
        raise RuntimeError('Cached transfer integrity failed. Preserve the workspace and inspect it.')
    return encrypted, metadata


def _write_exclusive(path, body):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError('Transfer cache write stopped')
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _persist_cached_bundle(store, review_hash, encrypted, metadata):
    encrypted_path, metadata_path = _cache_paths(store, review_hash)
    _write_exclusive(encrypted_path, encrypted)
    _write_exclusive(metadata_path, canonical(metadata).encode())
    directory_fd = os.open(encrypted_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def build_bundle(store, ids, recipient_id, actor, include_metadata=False, review_hash=None):
    with store.tx() as connection:
        snapshot = _transfer_snapshot(store, ids, recipient_id, connection)
        if review_hash is not None and snapshot['review_hash'] != review_hash:
            raise ReviewMismatch()
        if review_hash is not None:
            cached = _load_cached_bundle(store, review_hash)
            if cached is not None:
                encrypted, metadata = cached
                if (
                    metadata['recipient_id'] != recipient_id
                    or metadata['record_ids'] != [record['id'] for record in snapshot['records']]
                ):
                    raise RuntimeError('Cached transfer metadata does not match the reviewed selection.')
                metadata = {**metadata, 'peer': snapshot['recipient']}
                return (encrypted, metadata) if include_metadata else encrypted
        manifest = dict(schema_version=1, kind='record_bundle', bundle_id=str(uuid.uuid4()), engagement_id=snapshot['config']['engagement']['id'], source_instance=snapshot['config']['instance_id'], recipient_id=recipient_id, created_at=utc(), records=snapshot['records'], files=snapshot['file_manifest'])
        validate_manifest(manifest)
        key = Ed25519PrivateKey.from_private_bytes((store.root / 'keys' / 'signing.key').read_bytes())
        signature = base64.b64encode(key.sign(canonical(manifest).encode())).decode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('manifest.json', canonical(dict(manifest=manifest, signature=signature)))
            for id, raw in snapshot['files'].items():
                archive.writestr('evidence/' + id, raw)
        if stream.tell() > MAX_BUNDLE:
            raise ValueError('Bundle exceeds its limit')
        encrypted = subprocess.run([age_bin(), '-r', snapshot['recipient']['recipient']], input=stream.getvalue(), capture_output=True, timeout=60, check=True).stdout
        metadata = {
            'bundle_id': manifest['bundle_id'], 'manifest_hash': digest(manifest),
            'record_ids': [record['id'] for record in snapshot['records']],
            'recipient_id': recipient_id,
            'review_hash': snapshot['review_hash'],
            'sha256': hashlib.sha256(encrypted).hexdigest(), 'bytes': len(encrypted),
        }
        if review_hash is not None:
            _persist_cached_bundle(store, review_hash, encrypted, metadata)
        store.event(
            connection, actor, 'transfer.approved', metadata['record_ids'],
            bundle_id=manifest['bundle_id'], recipient_id=recipient_id,
            manifest_hash=metadata['manifest_hash'], review_hash=snapshot['review_hash'],
        )
        store.event(
            connection, actor, 'transfer.encrypted', [], bundle_id=manifest['bundle_id'],
            sha256=metadata['sha256'], bytes=len(encrypted),
        )
    metadata = {**metadata, 'peer': snapshot['recipient']}
    return (encrypted, metadata) if include_metadata else encrypted


def preserve_conflict_bundle(store, bundle_id, encrypted):
    """Durably retain the exact encrypted input before recording a conflict receipt."""
    path = store.root / 'conflicts' / f'transfer-conflict-{bundle_id}.age'
    expected_hash = hashlib.sha256(encrypted).hexdigest()
    if path.exists():
        private(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('Stored conflict bundle does not match this transfer')
        return path, expected_hash
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(encrypted)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError('Conflict bundle write stopped')
            view = view[written:]
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    private(path)
    return path, expected_hash


def _cleanup_unindexed_artifacts(store, artifact_ids):
    """Remove only files created by this failed import without touching committed evidence."""
    if not artifact_ids:
        return
    try:
        with store.connect() as connection:
            referenced = {
                json.loads(row['data']).get('artifact_id')
                for row in connection.execute("SELECT data FROM records WHERE kind='upload'")
            }
        removed = False
        for artifact_id in artifact_ids:
            if artifact_id in referenced:
                continue
            path = store.root / 'artifacts' / str(uuid.UUID(artifact_id))
            try:
                private(path)
            except FileNotFoundError:
                continue
            path.unlink()
            removed = True
        if removed:
            directory_fd = os.open(store.root / 'artifacts', os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        store.blocked = 'Failed import cleanup left restricted evidence that requires recovery review.'


def _cleanup_unindexed_conflict_bundle(store, path):
    """Keep conflict ciphertext only when a committed conflict record owns it."""
    if path is None:
        return
    try:
        with store.connect() as connection:
            referenced = any(
                json.loads(row['data']).get('encrypted_bundle') == path.name
                for row in connection.execute("SELECT data FROM records WHERE kind='transfer_conflict'")
            )
        if referenced:
            return
        try:
            private(path)
        except FileNotFoundError:
            return
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        store.blocked = 'Failed conflict cleanup left restricted ciphertext that requires recovery review.'


def open_bundle(store, encrypted, actor, *, inspect_only=False):
    if len(encrypted) > MAX_BUNDLE + 1024**2:
        raise ValueError('Bundle exceeds its limit')
    private(store.root / 'keys' / 'age.key')
    # Ciphertext carries no compression layer. Bound decrypted output before parsing ZIP.
    raw = subprocess.run([age_bin(), '-d', '-i', str(store.root / 'keys' / 'age.key')], input=encrypted, capture_output=True, timeout=60, check=True).stdout
    if len(raw) > MAX_BUNDLE:
        raise ValueError('Decrypted bundle exceeds its limit')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        if len(infos) > 1001 or sum(i.file_size for i in infos) > MAX_BUNDLE or any(i.compress_type != zipfile.ZIP_STORED for i in infos):
            raise ValueError('Unsupported bundle archive')
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('Invalid bundle entries')
        signed = json.loads(z.read('manifest.json'))
        if set(signed) != {'manifest', 'signature'}:
            raise ValueError('Invalid signed manifest')
        m = signed['manifest']
        validate_manifest(m)
        if set(m) != {'schema_version', 'kind', 'bundle_id', 'engagement_id', 'source_instance', 'recipient_id', 'created_at', 'records', 'files'} or m['schema_version'] != 1 or m['kind'] != 'record_bundle':
            raise ValueError('Unsupported manifest version')
        peers = {p['id']: p for p in store.setting('peers', [])}
        peer = peers.get(m['source_instance'])
        config = store.setting('config')
        if not peer or m['engagement_id'] != config['engagement']['id'] or m['recipient_id'] != config['instance_id']:
            raise ValueError('Untrusted sender, recipient, or engagement')
        Ed25519PublicKey.from_public_bytes(base64.b64decode(peer['signing_key'])).verify(base64.b64decode(signed['signature'], validate=True), canonical(m).encode())
        if len(m['records']) > 1000 or len(m['files']) > 1000:
            raise ValueError('Transfer record limit exceeded')
        file_map = {f['id']: f for f in m['files']}
        if len(file_map) != len(m['files']) or set(names) != {'manifest.json'} | {'evidence/' + id for id in file_map}:
            raise ValueError('Evidence manifest does not match the archive')
        contents = {}
        for id, f in file_map.items():
            uuid.UUID(id)
            if set(f) != {'id', 'size', 'sha256'}:
                raise ValueError('Invalid evidence manifest')
            body = z.read('evidence/' + id)
            if len(body) != f['size'] or hashlib.sha256(body).hexdigest() != f['sha256'] or secret_bearing(body):
                raise ValueError('Evidence failed integrity or secret review')
            if restricted_harness_original(body):
                raise ValueError('The signed harness original is restricted and cannot enter a transfer bundle')
            contents[id] = body
        ids = set()
        for r in m['records']:
            if set(r) != {'id', 'kind', 'revision_id', 'data', 'updated_at'} or r['kind'] not in KINDS or r['id'] in ids or not isinstance(r['data'], dict):
                raise ValueError('Invalid transfer record')
            uuid.UUID(r['id']); uuid.UUID(r['revision_id']); ids.add(r['id'])
            validate_record_data(r['kind'], r['data'])
            if secret_bearing(canonical(r['data']).encode()):
                raise ValueError('Record requires secret review')
            authorized_origin(store, r, peer, config)
            if r['kind'] == 'upload' and (r['id'] not in contents or r['data'].get('quarantined')):
                raise ValueError('Upload evidence is missing or quarantined')
        incoming = {record['id']: record for record in m['records']}
        uploads = {id: record for id, record in incoming.items() if record['kind'] == 'upload'}
        if set(contents) != set(uploads):
            raise ValueError('Evidence files and upload records do not match')
        for id, record in uploads.items():
            file = file_map[id]
            data = record['data']
            if data['artifact_id'] != id or data['size'] != file['size'] or data['sha256'] != file['sha256']:
                raise ValueError('Upload metadata does not match its evidence file')
        for r in m['records']:
            for reference, expected_kinds in record_references(r['kind'], r['data']):
                target = incoming.get(reference) or store.get(reference)
                if not target:
                    raise ValueError('Transfer record has a missing referenced record')
                if target['kind'] not in expected_kinds:
                    raise ValueError('Transfer reference points to the wrong record type')
        if inspect_only:
            return m, contents
        created_artifacts = []
        conflict_bundle = None
        try:
            with store.tx() as c:
                manifest_hash = digest(m)
                prior = c.execute('SELECT * FROM transfers WHERE id=?', (m['bundle_id'],)).fetchone()
                if prior:
                    if prior['hash'] != manifest_hash:
                        raise ValueError('Transfer ID was reused with different content')
                    return json.loads(prior['receipt'])
                existing = {r['id']: store.get(r['id'], c) for r in m['records']}
                local_instance = config['instance_id']
                duplicates = [
                    r['id'] for r in m['records']
                    if existing[r['id']] and record_identity(existing[r['id']], local_instance) == record_identity(r, m['source_instance'])
                ]
                conflicts = [r['id'] for r in m['records'] if existing[r['id']] and r['id'] not in duplicates]
                deferred = [r['id'] for r in m['records'] if not existing[r['id']]]
                if conflicts:
                    conflict_bundle, ciphertext_hash = preserve_conflict_bundle(store, m['bundle_id'], encrypted)
                    conflict = store.put(c, 'transfer_conflict', {
                        'bundle_id': m['bundle_id'], 'manifest_hash': manifest_hash,
                        'source_instance': m['source_instance'],
                        'state': 'needs_review', 'conflict_ids': conflicts,
                        'duplicate_ids': duplicates, 'deferred_ids': deferred,
                        'local_at_conflict': [existing[id] for id in conflicts],
                        'incoming': m['records'], 'files': m['files'],
                        'encrypted_bundle': conflict_bundle.name,
                        'ciphertext_sha256': ciphertext_hash,
                        'ciphertext_size': len(encrypted),
                    }, actor)
                    receipt = {'status': 'conflict', 'imported': 0, 'duplicates': len(duplicates), 'conflicts': conflicts, 'deferred': deferred, 'bundle_id': m['bundle_id'], 'manifest_hash': manifest_hash}
                    c.execute('INSERT INTO transfers VALUES(?,?,?)', (m['bundle_id'], manifest_hash, canonical(receipt)))
                    store.event(c, actor, 'transfer.conflict', [conflict['id'], *conflicts], bundle_id=m['bundle_id'], manifest_hash=manifest_hash, duplicates=len(duplicates), deferred=len(deferred), ciphertext_sha256=ciphertext_hash)
                    return receipt
                for artifact_id, body in contents.items():
                    if _persist_artifact(store, artifact_id, body):
                        created_artifacts.append(artifact_id)
                count = 0
                for r in m['records']:
                    if store.get(r['id'], c):
                        continue
                    source_instance, source_revision_id = incoming_provenance(r, m['source_instance'])
                    data = {**r['data'], 'source_instance': source_instance, 'source_revision_id': source_revision_id}
                    if r['kind'] == 'upload':
                        data['artifact_id'] = r['id']
                    store.put(c, r['kind'], data, actor, r['id'])
                    count += 1
                receipt = {'status': 'imported', 'imported': count, 'duplicates': len(m['records']) - count, 'conflicts': [], 'deferred': [], 'bundle_id': m['bundle_id'], 'manifest_hash': manifest_hash}
                c.execute('INSERT INTO transfers VALUES(?,?,?)', (m['bundle_id'], manifest_hash, canonical(receipt)))
                store.event(c, actor, 'transfer.imported', list(ids), bundle_id=m['bundle_id'], manifest_hash=manifest_hash, records=count)
        except Exception:
            _cleanup_unindexed_artifacts(store, created_artifacts)
            _cleanup_unindexed_conflict_bundle(store, conflict_bundle)
            raise
        return receipt


def conflict_view(store, item):
    data = item['data']
    local = [store.get(record_id) for record_id in data.get('conflict_ids', [])]
    return {**item, 'local': [record for record in local if record]}


def _read_preserved_conflict(store, data, actor):
    bundle_id = str(uuid.UUID(data['bundle_id']))
    name = f'transfer-conflict-{bundle_id}.age'
    if data.get('encrypted_bundle') != name:
        raise ValueError('Conflict bundle locator is invalid')
    path = store.root / 'conflicts' / name
    private(path)
    encrypted = path.read_bytes()
    if len(encrypted) != data.get('ciphertext_size') or hashlib.sha256(encrypted).hexdigest() != data.get('ciphertext_sha256'):
        raise ValueError('Conflict bundle integrity failed')
    manifest, contents = open_bundle(store, encrypted, actor, inspect_only=True)
    if (
        manifest['bundle_id'] != bundle_id
        or digest(manifest) != data.get('manifest_hash')
        or manifest['source_instance'] != data.get('source_instance')
        or canonical(manifest['records']) != canonical(data.get('incoming'))
        or canonical(manifest['files']) != canonical(data.get('files'))
    ):
        raise ValueError('Conflict record does not match its preserved bundle')
    return manifest, contents


def _persist_artifact(store, artifact_id, body):
    path = store.root / 'artifacts' / str(uuid.UUID(artifact_id))
    expected_hash = hashlib.sha256(body).hexdigest()
    if path.exists():
        private(path)
        if not path.is_file() or path.stat().st_size != len(body) or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('Local artifact ID has different content')
        return False
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError('Artifact write stopped')
            view = view[written:]
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    private(path)
    return True


def _conflict_review_binding(conflict_revision_id, manifest_hash, local_revisions):
    try:
        uuid.UUID(conflict_revision_id)
        for record_id, revision_id in local_revisions.items():
            uuid.UUID(record_id); uuid.UUID(revision_id)
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Conflict review identifiers are invalid')
    return digest({
        'schema': 'team-transfer-conflict-review-v1',
        'conflict_revision_id': conflict_revision_id,
        'manifest_hash': manifest_hash,
        'local_revisions': dict(sorted(local_revisions.items())),
    })


def resolve_conflict(store, conflict_id, decision, actor, conflict_revision_id, manifest_hash, local_revisions):
    if decision not in {'keep_local', 'accept_incoming'}:
        raise ValueError('Unsupported conflict decision')
    binding = _conflict_review_binding(conflict_revision_id, manifest_hash, local_revisions)
    original = store.get(conflict_id)
    if not original or original['kind'] != 'transfer_conflict':
        raise ValueError('Transfer conflict was not found')
    original_data = original['data']
    if original_data.get('state') != 'needs_review':
        if original_data.get('resolution') == decision and original_data.get('resolution_binding') == binding:
            return conflict_view(store, original)
        raise ReviewMismatch()
    current_local_revisions = {
        record_id: (store.get(record_id) or {}).get('revision_id')
        for record_id in original_data.get('conflict_ids', [])
    }
    if (
        original['revision_id'] != conflict_revision_id
        or original_data.get('manifest_hash') != manifest_hash
        or current_local_revisions != local_revisions
    ):
        raise ReviewMismatch()

    manifest = contents = None
    created_artifacts = []
    if decision == 'accept_incoming':
        manifest, contents = _read_preserved_conflict(store, original_data, actor)
        incoming = {record['id']: record for record in manifest['records']}
        conflict_ids = set(original_data.get('conflict_ids', []))
        duplicate_ids = set(original_data.get('duplicate_ids', []))
        deferred_ids = set(original_data.get('deferred_ids', []))
        if (
            len(incoming) != len(manifest['records'])
            or conflict_ids & duplicate_ids
            or conflict_ids & deferred_ids
            or duplicate_ids & deferred_ids
            or set(incoming) != conflict_ids | duplicate_ids | deferred_ids
        ):
            raise ValueError('Conflict record partition is invalid')
        snapshots = {record['id']: record for record in original_data.get('local_at_conflict', [])}
        if set(snapshots) != conflict_ids:
            raise ValueError('Conflict lacks a current revision binding; keep the local record')
        local_instance = store.setting('config')['instance_id']
        for record_id in conflict_ids:
            current = store.get(record_id)
            snapshot = snapshots[record_id]
            record = incoming[record_id]
            if not current or current != snapshot:
                raise ValueError('Local record changed after the conflict was recorded')
            local_origin, _ = local_provenance(current, local_instance)
            incoming_origin, incoming_revision = incoming_provenance(record, manifest['source_instance'])
            _, local_revision = local_provenance(current, local_instance)
            if local_origin != incoming_origin or incoming_origin != manifest['source_instance'] or incoming_origin == local_instance:
                raise ValueError('Only a replica can accept a revision from its original source owner')
            if local_revision == incoming_revision:
                raise ValueError('A source revision identifier cannot describe different content')
            if current['kind'] != record['kind'] or record['kind'] == 'upload':
                raise ValueError('This conflict type cannot replace the local record')
        for artifact_id, body in contents.items():
            if artifact_id not in deferred_ids and artifact_id not in duplicate_ids:
                raise ValueError('Conflicting evidence identifiers cannot replace local artifacts')
            if _persist_artifact(store, artifact_id, body):
                created_artifacts.append(artifact_id)

    try:
        with store.tx() as connection:
            item = store.get(conflict_id, connection)
            if not item or item['kind'] != 'transfer_conflict':
                raise ValueError('Transfer conflict was not found')
            data = item['data']
            if item['revision_id'] != conflict_revision_id or data.get('state') != 'needs_review':
                if data.get('resolution') == decision and data.get('resolution_binding') == binding:
                    return conflict_view(store, item)
                raise ReviewMismatch()
            locked_local_revisions = {
                record_id: (store.get(record_id, connection) or {}).get('revision_id')
                for record_id in data.get('conflict_ids', [])
            }
            if data.get('manifest_hash') != manifest_hash or locked_local_revisions != local_revisions:
                raise ReviewMismatch()
            if decision == 'accept_incoming':
                incoming = {record['id']: record for record in manifest['records']}
                snapshots = {record['id']: record for record in data['local_at_conflict']}
                local_instance = store.setting('config')['instance_id']
                for record_id in data['conflict_ids']:
                    current = store.get(record_id, connection)
                    if current != snapshots[record_id]:
                        raise ValueError('Local record changed after the conflict was recorded')
                    record = incoming[record_id]
                    source_instance, source_revision_id = incoming_provenance(record, manifest['source_instance'])
                    accepted_data = {**record['data'], 'source_instance': source_instance, 'source_revision_id': source_revision_id}
                    store.put(connection, record['kind'], accepted_data, actor, record_id, current['revision_id'])
                for record_id in data['deferred_ids']:
                    if store.get(record_id, connection):
                        raise ValueError('A deferred record appeared during conflict review')
                    record = incoming[record_id]
                    source_instance, source_revision_id = incoming_provenance(record, manifest['source_instance'])
                    accepted_data = {**record['data'], 'source_instance': source_instance, 'source_revision_id': source_revision_id}
                    if record['kind'] == 'upload':
                        accepted_data['artifact_id'] = record_id
                    store.put(connection, record['kind'], accepted_data, actor, record_id)
                for record_id in data['duplicate_ids']:
                    current = store.get(record_id, connection)
                    if not current or local_provenance(current, local_instance) != incoming_provenance(incoming[record_id], manifest['source_instance']):
                        raise ValueError('A duplicate record changed during conflict review')
                receipt = {
                    'status': 'imported',
                    'imported': len(data['conflict_ids']) + len(data['deferred_ids']),
                    'duplicates': len(data['duplicate_ids']),
                    'conflicts': [], 'deferred': [],
                    'bundle_id': data['bundle_id'], 'manifest_hash': data['manifest_hash'],
                }
                changed = connection.execute(
                    'UPDATE transfers SET receipt=? WHERE id=? AND hash=?',
                    (canonical(receipt), data['bundle_id'], data['manifest_hash']),
                ).rowcount
                if changed != 1:
                    raise ValueError('Original transfer receipt is missing or changed')
            updated = store.put(connection, 'transfer_conflict', {
                **data, 'state': 'resolved', 'resolution': decision,
                'resolution_binding': binding,
                'resolved_by': actor, 'resolved_at': utc(),
            }, actor, conflict_id, item['revision_id'])
            store.event(connection, actor, 'transfer.conflict_resolved', [conflict_id, *data.get('conflict_ids', [])], decision=decision, bundle_id=data['bundle_id'], review_hash=binding)
    except Exception:
        _cleanup_unindexed_artifacts(store, created_artifacts)
        raise
    return conflict_view(store, updated)


def routes(app, store, actor, admin, record):
    @app.get('/api/transfers/conflicts')
    def conflicts(request: Request):
        admin(request)
        items = [conflict_view(store, item) for item in store.records('transfer_conflict')]
        return {'items': items, 'total': len(items)}

    @app.post('/api/transfers/conflicts/{id}/resolve')
    def resolve(id: str, body: ConflictResolution, request: Request):
        admin(request)
        try:
            return resolve_conflict(
                store, id, body.decision, actor(request), body.conflict_revision_id,
                body.manifest_hash, body.local_revisions,
            )
        except ReviewMismatch:
            raise HTTPException(409, 'The conflict or local records changed after review. Review it again.')

    @app.post('/api/evidence/{id}/approve-export')
    def approve(id: str, body: EvidenceApproval, request: Request):
        admin(request)
        with store.tx() as c:
            r = store.get(id, c)
            if not r or r['kind'] != 'upload':
                raise HTTPException(404, 'The record was not found.')
            if r['revision_id'] != body.base_revision_id or r['data']['sha256'] != body.artifact_sha256:
                raise Conflict(r)
            if r['data'].get('quarantined') or r['data']['status'] not in ('preview', 'merged'):
                raise HTTPException(409, 'Review a successful import before approving evidence.')
            path = store.root / 'artifacts' / r['data']['artifact_id']
            try:
                private(path)
                if not path.is_file() or path.stat().st_size != r['data']['size']:
                    raise ValueError('Evidence size changed')
                checksum = hashlib.sha256()
                with path.open('rb') as source:
                    while chunk := source.read(1024 * 1024):
                        checksum.update(chunk)
                if checksum.hexdigest() != r['data']['sha256']:
                    raise ValueError('Evidence checksum changed')
            except Exception as error:
                store.blocked = 'Evidence integrity failed. Preserve the workspace and inspect the original artifact.'
                raise RuntimeError(store.blocked) from error
            r = store.put(c, 'upload', {**r['data'], 'reviewed_for_export': True}, actor(request), id, r['revision_id'])
            store.event(c, actor(request), 'evidence.export_reviewed', [id], sha256=r['data']['sha256'])
        return r

    @app.post('/api/transfers/preview')
    def preview(body: TransferSelection, request: Request):
        admin(request)
        return preview_bundle(store, body.record_ids, body.recipient_id)

    @app.post('/api/transfers/export')
    def export(body: Export, request: Request):
        admin(request)
        try:
            blob, metadata = build_bundle(
                store, body.record_ids, body.recipient_id, actor(request),
                include_metadata=True, review_hash=body.review_hash,
            )
        except ReviewMismatch:
            raise HTTPException(409, 'The records or recipient changed after review. Review this transfer again.')
        with store.tx() as connection:
            store.event(
                connection, actor(request), 'transfer.exported', metadata['record_ids'],
                bundle_id=metadata['bundle_id'], manifest_hash=metadata['manifest_hash'],
                review_hash=metadata['review_hash'], payload_hash=metadata['sha256'],
            )
        return Response(blob, media_type='application/octet-stream', headers={'Content-Disposition': 'attachment; filename="team-transfer.zip.age"'})

    @app.post('/api/transfers/import')
    async def import_file(request: Request, file: UploadFile = File(...)):
        admin(request)
        try:
            data = await file.read(MAX_BUNDLE + 1024**2 + 1)
            return await __import__('asyncio').to_thread(open_bundle, store, data, actor(request))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise HTTPException(422, 'The bundle could not be decrypted. Check the recipient and file.')
        finally:
            await file.close()

    @app.post('/api/transfers/send')
    def send(body: Export, request: Request):
        admin(request)
        try:
            data, expected = build_bundle(
                store, body.record_ids, body.recipient_id, actor(request),
                include_metadata=True, review_hash=body.review_hash,
            )
        except ReviewMismatch:
            raise HTTPException(409, 'The records or recipient changed after review. Review this transfer again.')
        peer = expected['peer']
        try:
            with httpx.Client(verify=peer_tls_context(), trust_env=False, follow_redirects=False, timeout=30) as client:
                response = client.post(peer['origin'].rstrip('/') + '/peer/inbox', content=data, headers=peer_http_headers(store, peer, data))
            if response.status_code != 200:
                raise ValueError('Peer did not return a receipt')
            receipt = validate_receipt(response.json(), expected)
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            with store.tx() as c:
                store.event(c, actor(request), 'transfer.peer_unconfirmed', [], recipient_id=body.recipient_id, bundle_id=expected['bundle_id'], manifest_hash=expected['manifest_hash'], payload_hash=hashlib.sha256(data).hexdigest())
            raise HTTPException(503, 'The peer did not confirm receipt. File import is idempotent; use the encrypted fallback.')
        with store.tx() as c:
            store.event(c, actor(request), 'transfer.peer_receipt', [], recipient_id=body.recipient_id, bundle_id=expected['bundle_id'], manifest_hash=expected['manifest_hash'], outcome=receipt['status'], payload_hash=hashlib.sha256(data).hexdigest())
        return receipt

    @app.post('/peer/inbox')
    async def peer_inbox(request: Request):
        # Middleware verifies one signed request nonce and the declared body digest
        # before this route reads bytes. The bundle signature is checked again after
        # decryption so transport admission and record provenance stay independent.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BUNDLE + 1024**2:
                raise HTTPException(413, 'Transfer limit exceeded.')
        authorization = request.state.peer_authorization
        if len(raw) != authorization['content_length'] or not __import__('hmac').compare_digest(hashlib.sha256(raw).hexdigest(), authorization['sha256']):
            raise HTTPException(422, 'The peer request body did not match its signed headers.')
        try:
            return await __import__('asyncio').to_thread(open_bundle, store, bytes(raw), 'paired-host')
        except Exception:
            raise HTTPException(422, 'The transfer did not pass recipient, signature, or schema checks.')
