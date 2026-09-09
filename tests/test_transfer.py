import copy
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import uuid
import zipfile
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from workspace import transfer as transfer_module
from workspace.cli import initialize, backup
from workspace.store import Store, digest, canonical
from workspace.transfer import (
    ReviewMismatch,
    authorize_peer_headers,
    build_bundle,
    enroll,
    open_bundle,
    peer_http_headers,
    preview_bundle,
    provision_keys,
    public_card,
    resolve_conflict,
    validate_receipt,
)
from workspace.client_info_stripper import minimize


def test_peer_ca_bundle_is_explicit_and_bounded(tmp_path, monkeypatch):
    source = Path(ssl.get_default_verify_paths().cafile)
    bundle = tmp_path / 'team-ca.crt'
    bundle.write_bytes(source.read_bytes())
    monkeypatch.setenv('APP_PEER_CA_BUNDLE', str(bundle))
    assert isinstance(transfer_module.peer_tls_context(), ssl.SSLContext)
    monkeypatch.setenv('CONTAINERIZED', '1')
    with pytest.raises(ValueError, match='approved TLS mount'):
        transfer_module.peer_tls_context()
    compose = (Path(__file__).resolve().parents[1] / 'compose.yaml').read_text()
    assert 'APP_PEER_CA_BUNDLE: "${APP_PEER_CA_BUNDLE:-}"' in compose


@pytest.fixture
def peers(tmp_path):
    if not shutil.which('age') or not shutil.which('age-keygen'):
        pytest.fail('Pinned age tools are required for the transfer acceptance suite')
    engagement = str(uuid.uuid4())
    a = initialize(tmp_path / 'a', 'http://127.0.0.1:8710', 'Synthetic', engagement_id=engagement)
    b = initialize(tmp_path / 'b', 'http://127.0.0.1:8711', 'Synthetic', engagement_id=engagement)
    for store, app in ((a, 'Harbinger'), (b, 'Merlin')):
        store.configure('config', {**store.setting('config'), 'app': app})
    for store in (a, b):
        provision_keys(store)
    for receiver, sender in ((a, b), (b, a)):
        card = public_card(sender)
        enroll(receiver, card, digest(card))
    return a, b


def add_finding(store):
    with store.tx() as c:
        r = store.put(c, 'finding', {'title': 'Synthetic lead', 'observation': 'Observed in a fixture', 'evidence_needed': 'Screenshot pending'}, 'tester')
        store.event(c, 'tester', 'record.created', [r['id']])
    return r


def test_peer_http_headers_authenticate_before_body_and_reject_replay(peers):
    sender, receiver = peers
    body = b'synthetic encrypted envelope bytes'
    recipient = sender.setting('peers')[0]
    headers = peer_http_headers(sender, recipient, body)
    accepted = authorize_peer_headers(receiver, headers)
    assert accepted['source_instance'] == sender.setting('config')['instance_id']
    assert accepted['content_length'] == len(body)
    assert accepted['sha256'] == __import__('hashlib').sha256(body).hexdigest()
    with pytest.raises(ValueError, match='replayed'):
        authorize_peer_headers(receiver, headers)
    changed = peer_http_headers(sender, recipient, body)
    changed['Content-Length'] = str(len(body) + 1)
    with pytest.raises(ValueError, match='signature'):
        authorize_peer_headers(receiver, changed)


def test_peer_http_headers_reject_stale_wrong_recipient_and_excess_length(peers, monkeypatch):
    sender, receiver = peers
    body = b'synthetic encrypted envelope bytes'
    recipient = sender.setting('peers')[0]

    stale = peer_http_headers(sender, recipient, body)
    signed_at = int(stale['X-Team-Timestamp'])
    monkeypatch.setattr(transfer_module.time, 'time', lambda: signed_at + 61)
    with pytest.raises(ValueError, match='allowed window'):
        authorize_peer_headers(receiver, stale)

    monkeypatch.undo()
    wrong_recipient = peer_http_headers(sender, recipient, body)
    wrong_recipient['X-Team-Recipient'] = sender.setting('config')['instance_id']
    with pytest.raises(ValueError, match='recipient'):
        authorize_peer_headers(receiver, wrong_recipient)

    excessive = peer_http_headers(sender, recipient, body)
    excessive['Content-Length'] = str(transfer_module.MAX_BUNDLE + 1024**2 + 1)
    with pytest.raises(ValueError, match='admitted limit'):
        authorize_peer_headers(receiver, excessive)


def conflict_review(store, conflict):
    return {
        'conflict_revision_id': conflict['revision_id'],
        'manifest_hash': conflict['data']['manifest_hash'],
        'local_revisions': {
            record_id: store.get(record_id)['revision_id']
            for record_id in conflict['data']['conflict_ids']
        },
    }


def rewrite_signed_bundle(encrypted, sender, receiver, mutate):
    plain = subprocess.run(
        ['age', '-d', '-i', str(receiver.root / 'keys' / 'age.key')],
        input=encrypted, capture_output=True, check=True,
    ).stdout
    with zipfile.ZipFile(io.BytesIO(plain)) as source:
        entries = {info.filename: source.read(info.filename) for info in source.infolist()}
    signed = json.loads(entries['manifest.json'])
    mutate(signed['manifest'])
    key = Ed25519PrivateKey.from_private_bytes((sender.root / 'keys' / 'signing.key').read_bytes())
    signed['signature'] = base64.b64encode(key.sign(canonical(signed['manifest']).encode())).decode()
    entries['manifest.json'] = canonical(signed).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as target:
        for name, body in entries.items():
            target.writestr(name, body)
    return subprocess.run(
        ['age', '-r', public_card(receiver)['recipient']],
        input=output.getvalue(), capture_output=True, check=True,
    ).stdout


def test_encrypted_roundtrip_and_duplicate(peers):
    a, b = peers
    r = add_finding(a)
    raw = build_bundle(a, [r['id']], b.setting('config')['instance_id'], 'host')
    assert b'Synthetic lead' not in raw
    receipt = open_bundle(b, raw, 'scribe')
    assert receipt['imported'] == 1
    assert uuid.UUID(receipt['bundle_id']) and len(receipt['manifest_hash']) == 64
    assert open_bundle(b, raw, 'scribe') == receipt
    assert len(b.records('finding')) == 1
    assert b.get(r['id'])['data']['source_revision_id'] == r['revision_id']
    assert a.get(r['id']) == r
    assert a.verify() and b.verify()


def test_reviewed_transfer_reuses_exact_ciphertext(peers):
    sender, receiver = peers
    finding = add_finding(sender)
    recipient_id = receiver.setting('config')['instance_id']
    review = preview_bundle(sender, [finding['id']], recipient_id)

    first, first_metadata = build_bundle(
        sender, [finding['id']], recipient_id, 'host',
        include_metadata=True, review_hash=review['review_hash'],
    )
    second, second_metadata = build_bundle(
        sender, [finding['id']], recipient_id, 'host',
        include_metadata=True, review_hash=review['review_hash'],
    )

    assert first == second
    assert first_metadata == second_metadata
    assert first_metadata['review_hash'] == review['review_hash']
    assert open_bundle(receiver, first, 'scribe')['bundle_id'] == first_metadata['bundle_id']
    approved = [
        json.loads(line)['body'] for line in (sender.root / 'audit.jsonl').read_text().splitlines()
        if json.loads(line)['body']['operation'] == 'transfer.approved'
    ]
    assert len(approved) == 1


def test_transfer_preview_is_stable_and_has_no_side_effects(peers):
    sender, receiver = peers
    first = add_finding(sender)
    second = add_finding(sender)
    recipient_id = receiver.setting('config')['instance_id']
    before = (sender.root / 'audit.jsonl').read_bytes()

    forward = preview_bundle(sender, [first['id'], second['id']], recipient_id)
    reverse = preview_bundle(sender, [second['id'], first['id'], first['id']], recipient_id)

    assert forward['review_hash'] == reverse['review_hash']
    assert forward['selected_record_ids'] == sorted([first['id'], second['id']])
    assert (sender.root / 'audit.jsonl').read_bytes() == before
    assert not list((sender.root / 'exports').iterdir())


def test_cached_transfer_survives_verified_backup_and_restore(peers, tmp_path):
    sender, receiver = peers
    finding = add_finding(sender)
    recipient_id = receiver.setting('config')['instance_id']
    review = preview_bundle(sender, [finding['id']], recipient_id)
    encrypted, metadata = build_bundle(
        sender, [finding['id']], recipient_id, 'host',
        include_metadata=True, review_hash=review['review_hash'],
    )
    backup_path = tmp_path / 'sender-backup'
    backup(sender, backup_path)
    restored = Store(backup_path)

    repeated, repeated_metadata = build_bundle(
        restored, [finding['id']], recipient_id, 'host',
        include_metadata=True, review_hash=review['review_hash'],
    )

    assert repeated == encrypted
    assert repeated_metadata['bundle_id'] == metadata['bundle_id']
    assert restored.verify()['events'] == sender.verify()['events']


def test_tampered_transfer_cache_blocks_restart(peers):
    sender, receiver = peers
    finding = add_finding(sender)
    recipient_id = receiver.setting('config')['instance_id']
    review = preview_bundle(sender, [finding['id']], recipient_id)
    build_bundle(sender, [finding['id']], recipient_id, 'host', review_hash=review['review_hash'])
    cached = next((sender.root / 'exports').glob('*.age'))
    cached.write_bytes(cached.read_bytes() + b'tamper')

    restarted = Store(sender.root)

    assert restarted.blocked == 'Audit integrity check failed. Preserve the workspace and inspect it.'


def test_reviewed_transfer_rejects_changed_dependency_before_approval(peers):
    sender, receiver = peers
    with sender.tx() as connection:
        asset = sender.put(connection, 'asset', {
            'label': 'Synthetic host', 'kind': 'host', 'track': 'network',
        }, 'fixture')
        finding = sender.put(connection, 'finding', {
            'title': 'Synthetic lead', 'observation': 'Observed in a fixture',
            'asset_ids': [asset['id']],
        }, 'fixture')
        sender.event(connection, 'fixture', 'transfer.fixture_created', [asset['id'], finding['id']])
    recipient_id = receiver.setting('config')['instance_id']
    review = preview_bundle(sender, [finding['id']], recipient_id)
    with sender.tx() as connection:
        sender.put(connection, 'asset', {
            **asset['data'], 'label': 'Changed after review',
        }, 'fixture', asset['id'], asset['revision_id'])
        sender.event(connection, 'fixture', 'asset.fixture_changed', [asset['id']])
    before = (sender.root / 'audit.jsonl').read_text()

    with pytest.raises(ReviewMismatch):
        build_bundle(
            sender, [finding['id']], recipient_id, 'host',
            review_hash=review['review_hash'],
        )

    assert (sender.root / 'audit.jsonl').read_text() == before
    assert not list((sender.root / 'exports').iterdir())


def test_reviewed_transfer_rejects_changed_peer_before_approval(peers):
    sender, receiver = peers
    finding = add_finding(sender)
    recipient_id = receiver.setting('config')['instance_id']
    review = preview_bundle(sender, [finding['id']], recipient_id)
    peer = sender.setting('peers')[0]
    sender.configure('peers', [{**peer, 'origin': 'http://127.0.0.1:8799'}])
    before = (sender.root / 'audit.jsonl').read_text()

    with pytest.raises(ReviewMismatch):
        build_bundle(
            sender, [finding['id']], recipient_id, 'host',
            review_hash=review['review_hash'],
        )

    assert (sender.root / 'audit.jsonl').read_text() == before
    assert not list((sender.root / 'exports').iterdir())


def test_wrong_recipient_and_tampered_ciphertext(peers):
    a, b = peers
    r = add_finding(a)
    raw = build_bundle(a, [r['id']], b.setting('config')['instance_id'], 'host')
    with pytest.raises(Exception):
        open_bundle(a, raw, 'host')
    damaged = bytearray(raw); damaged[-10] ^= 1
    with pytest.raises(Exception):
        open_bundle(b, bytes(damaged), 'scribe')
    assert not b.records('finding')


def test_peer_fingerprint_required(peers):
    a, b = peers
    with pytest.raises(ValueError):
        enroll(a, public_card(b), 'wrong')


def test_enrollment_rejects_same_app_and_self_identity(peers):
    a, b = peers
    same_app = {**public_card(b), 'app': 'Harbinger', 'name': 'Harbinger'}
    with pytest.raises(ValueError, match='Harbinger and Merlin'):
        enroll(a, same_app, digest(same_app))
    self_card = {**public_card(b), 'id': a.setting('config')['instance_id']}
    with pytest.raises(ValueError, match='distinct'):
        enroll(a, self_card, digest(self_card))


@pytest.mark.parametrize('secret_text', [
    'password=synthetic-secret',
    'api_key=synthetic-secret',
    'client_secret=synthetic-secret',
    'token=synthetic-secret',
    'Authorization: Bearer synthetic-secret',
])
def test_secret_prose_cannot_export(peers, secret_text):
    a, b = peers
    r = add_finding(a)
    with a.tx() as c:
        a.put(c, 'finding', {**r['data'], 'observation': secret_text}, 'tester', r['id'], r['revision_id'])
        a.event(c, 'tester', 'record.updated', [r['id']])
    with pytest.raises(ValueError):
        build_bundle(a, [r['id']], b.setting('config')['instance_id'], 'host')


def test_conflicting_transfer_preserves_existing(peers):
    a, b = peers
    r = add_finding(a)
    recipient = b.setting('config')['instance_id']
    open_bundle(b, build_bundle(a, [r['id']], recipient, 'host'), 'scribe')
    with a.tx() as c:
        a.put(c, 'finding', {**r['data'], 'title': 'Later revision'}, 'tester', r['id'], r['revision_id'])
        a.event(c, 'tester', 'record.updated', [r['id']])
    conflicting_bundle = build_bundle(a, [r['id']], recipient, 'host')
    receipt = open_bundle(b, conflicting_bundle, 'scribe')
    assert receipt['status'] == 'conflict'
    assert receipt['duplicates'] == 0 and receipt['deferred'] == []
    assert b.get(r['id'])['data']['title'] == 'Synthetic lead'
    conflicts = b.records('transfer_conflict')
    assert len(conflicts) == 1
    assert conflicts[0]['data']['incoming'][0]['data']['title'] == 'Later revision'
    assert open_bundle(b, conflicting_bundle, 'scribe') == receipt
    assert any(json.loads(line)['body']['operation'] == 'transfer.conflict' for line in (b.root / 'audit.jsonl').read_text().splitlines())


def test_keep_local_rejects_a_record_changed_after_conflict_review(peers):
    sender, receiver = peers
    original = add_finding(sender)
    recipient_id = receiver.setting('config')['instance_id']
    open_bundle(receiver, build_bundle(sender, [original['id']], recipient_id, 'host'), 'scribe')
    with sender.tx() as connection:
        revised = sender.put(connection, 'finding', {
            **original['data'], 'title': 'Incoming owner revision',
        }, 'tester', original['id'], original['revision_id'])
        sender.event(connection, 'tester', 'finding.fixture_changed', [revised['id']])
    open_bundle(receiver, build_bundle(sender, [revised['id']], recipient_id, 'host'), 'scribe')
    conflict = receiver.records('transfer_conflict')[0]
    review = conflict_review(receiver, conflict)
    current = receiver.get(original['id'])
    with receiver.tx() as connection:
        receiver.put(connection, 'finding', {
            **current['data'], 'notes': 'Local note added after review',
        }, 'scribe', current['id'], current['revision_id'])
        receiver.event(connection, 'scribe', 'finding.local_changed', [current['id']])
    before = (receiver.root / 'audit.jsonl').read_text()

    with pytest.raises(ReviewMismatch):
        resolve_conflict(
            receiver, conflict['id'], 'keep_local', 'lead-scribe', **review,
        )

    assert receiver.get(conflict['id'])['data']['state'] == 'needs_review'
    assert (receiver.root / 'audit.jsonl').read_text() == before


def test_replica_can_accept_owner_revision_with_deferred_evidence(peers):
    a, b = peers
    original = add_finding(a)
    recipient = b.setting('config')['instance_id']
    open_bundle(b, build_bundle(a, [original['id']], recipient, 'host'), 'scribe')

    upload_id = str(uuid.uuid4())
    body = b'synthetic reviewed evidence\n'
    artifact = a.root / 'artifacts' / upload_id
    artifact.write_bytes(body)
    artifact.chmod(0o600)
    import hashlib
    with a.tx() as connection:
        upload = a.put(connection, 'upload', {
            'filename': 'reviewed.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': upload_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True,
        }, 'tester', upload_id)
        revised = a.put(connection, 'finding', {
            **original['data'], 'observation': 'Owner supplied a reviewed update',
            'evidence_ids': [upload_id], 'evidence_needed': '',
        }, 'tester', original['id'], original['revision_id'])
        a.event(connection, 'tester', 'finding.updated', [revised['id'], upload['id']])

    encrypted = build_bundle(a, [revised['id']], recipient, 'host')
    receipt = open_bundle(b, encrypted, 'scribe')
    assert receipt['status'] == 'conflict' and receipt['deferred'] == [upload_id]
    conflict = b.records('transfer_conflict')[0]

    review = conflict_review(b, conflict)
    resolved = resolve_conflict(b, conflict['id'], 'accept_incoming', 'lead-scribe', **review)
    assert resolved['data']['state'] == 'resolved'
    assert resolved['data']['resolution'] == 'accept_incoming'
    imported = b.get(original['id'])
    assert imported['data']['observation'] == 'Owner supplied a reviewed update'
    assert imported['data']['source_instance'] == a.setting('config')['instance_id']
    assert imported['data']['source_revision_id'] == revised['revision_id']
    assert b.get(upload_id)['kind'] == 'upload'
    assert (b.root / 'artifacts' / upload_id).read_bytes() == body
    final_receipt = open_bundle(b, encrypted, 'scribe')
    assert final_receipt == {
        'status': 'imported', 'imported': 2, 'duplicates': 0,
        'conflicts': [], 'deferred': [],
        'bundle_id': receipt['bundle_id'], 'manifest_hash': receipt['manifest_hash'],
    }
    assert resolve_conflict(b, conflict['id'], 'accept_incoming', 'lead-scribe', **review)['revision_id'] == resolved['revision_id']
    assert a.get(original['id'])['revision_id'] == revised['revision_id']
    assert b.verify()['events'] > 0


def _conflict_with_deferred_evidence(peers):
    sender, receiver = peers
    original = add_finding(sender)
    recipient = receiver.setting('config')['instance_id']
    open_bundle(receiver, build_bundle(sender, [original['id']], recipient, 'host'), 'scribe')
    upload_id = str(uuid.uuid4())
    body = b'synthetic deferred conflict evidence\n'
    (sender.root / 'artifacts' / upload_id).write_bytes(body)
    (sender.root / 'artifacts' / upload_id).chmod(0o600)
    import hashlib
    with sender.tx() as connection:
        upload = sender.put(connection, 'upload', {
            'filename': 'deferred.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': upload_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True,
        }, 'tester', upload_id)
        revised = sender.put(connection, 'finding', {
            **original['data'], 'observation': 'Owner revised the lead',
            'evidence_ids': [upload_id], 'evidence_needed': '',
        }, 'tester', original['id'], original['revision_id'])
        sender.event(connection, 'tester', 'finding.updated', [revised['id'], upload['id']])
    receipt = open_bundle(receiver, build_bundle(sender, [revised['id']], recipient, 'host'), 'scribe')
    assert receipt['status'] == 'conflict' and receipt['deferred'] == [upload_id]
    return receiver, receiver.records('transfer_conflict')[0], upload_id, body


@pytest.mark.parametrize('failure_site', ('put', 'event', 'commit'))
def test_failed_accept_incoming_cleans_only_new_evidence(peers, monkeypatch, failure_site):
    receiver, conflict, upload_id, _ = _conflict_with_deferred_evidence(peers)
    existing_id = str(uuid.uuid4())
    existing_body = b'pre-existing local evidence\n'
    (receiver.root / 'artifacts' / existing_id).write_bytes(existing_body)
    (receiver.root / 'artifacts' / existing_id).chmod(0o600)
    review = conflict_review(receiver, conflict)
    if failure_site == 'put':
        monkeypatch.setattr(receiver, 'put', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('injected put failure')))
    elif failure_site == 'event':
        monkeypatch.setattr(receiver, 'event', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('injected event failure')))
    else:
        original_tx = receiver.tx

        @contextlib.contextmanager
        def fail_commit():
            with original_tx() as connection:
                yield connection
                raise RuntimeError('injected commit failure')

        monkeypatch.setattr(receiver, 'tx', fail_commit)

    with pytest.raises(RuntimeError, match=f'injected {failure_site} failure'):
        resolve_conflict(receiver, conflict['id'], 'accept_incoming', 'lead-scribe', **review)
    assert receiver.get(upload_id) is None
    assert not (receiver.root / 'artifacts' / upload_id).exists()
    assert (receiver.root / 'artifacts' / existing_id).read_bytes() == existing_body
    assert receiver.get(conflict['id'])['data']['state'] == 'needs_review'


def test_accept_incoming_cleanup_failure_preserves_primary_error_and_blocks_workspace(peers, monkeypatch):
    receiver, conflict, upload_id, _ = _conflict_with_deferred_evidence(peers)
    review = conflict_review(receiver, conflict)
    monkeypatch.setattr(receiver, 'put', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('injected put failure')))
    original_fsync = transfer_module.os.fsync
    calls = 0

    def fail_cleanup_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('injected cleanup fsync failure')
        return original_fsync(fd)

    monkeypatch.setattr(transfer_module.os, 'fsync', fail_cleanup_fsync)
    with pytest.raises(RuntimeError, match='injected put failure'):
        resolve_conflict(receiver, conflict['id'], 'accept_incoming', 'lead-scribe', **review)
    assert not (receiver.root / 'artifacts' / upload_id).exists()
    assert receiver.blocked == 'Failed import cleanup left restricted evidence that requires recovery review.'


def test_accept_incoming_rejects_a_different_source_collision(peers):
    a, b = peers
    record_id = str(uuid.uuid4())
    with a.tx() as connection:
        incoming = a.put(connection, 'finding', {
            'title': 'Incoming owner', 'observation': 'Synthetic incoming record',
        }, 'tester', record_id)
        a.event(connection, 'tester', 'finding.created', [record_id])
    with b.tx() as connection:
        local = b.put(connection, 'finding', {
            'title': 'Local owner', 'observation': 'Synthetic local record',
        }, 'scribe', record_id)
        b.event(connection, 'scribe', 'finding.created', [record_id])

    receipt = open_bundle(
        b,
        build_bundle(a, [incoming['id']], b.setting('config')['instance_id'], 'host'),
        'scribe',
    )
    assert receipt['status'] == 'conflict'
    conflict = b.records('transfer_conflict')[0]
    review = conflict_review(b, conflict)
    with pytest.raises(ValueError, match='owner|source'):
        resolve_conflict(b, conflict['id'], 'accept_incoming', 'lead-scribe', **review)
    assert b.get(record_id)['revision_id'] == local['revision_id']
    assert resolve_conflict(b, conflict['id'], 'keep_local', 'lead-scribe', **review)['data']['resolution'] == 'keep_local'


def test_roundtrip_preserves_origin_and_does_not_conflict(peers):
    harbinger, merlin = peers
    finding = add_finding(harbinger)
    open_bundle(
        merlin,
        build_bundle(harbinger, [finding['id']], merlin.setting('config')['instance_id'], 'captain'),
        'scribe',
    )
    with merlin.tx() as connection:
        question = merlin.put(connection, 'question', {
            'finding_id': finding['id'], 'text': 'Please collect a synthetic screenshot.',
        }, 'scribe')
        merlin.event(connection, 'scribe', 'question.created', [question['id'], finding['id']])

    receipt = open_bundle(
        harbinger,
        build_bundle(merlin, [question['id']], harbinger.setting('config')['instance_id'], 'lead-scribe'),
        'captain',
    )
    assert receipt['status'] == 'imported'
    assert receipt['imported'] == 1 and receipt['duplicates'] == 1
    assert receipt['conflicts'] == [] and receipt['deferred'] == []
    assert harbinger.get(finding['id'])['revision_id'] == finding['revision_id']
    assert harbinger.get(question['id'])['data']['text'] == 'Please collect a synthetic screenshot.'
    assert not harbinger.records('transfer_conflict')


def test_signed_cross_app_record_origination_is_rejected(peers):
    harbinger, merlin = peers
    finding = add_finding(harbinger)
    h_to_m = build_bundle(harbinger, [finding['id']], merlin.setting('config')['instance_id'], 'captain')

    def harbinger_claims_draft(manifest):
        manifest['records'][0]['kind'] = 'draft'
        manifest['records'][0]['data'] = {'title': 'Unauthorized draft', 'description': 'Synthetic'}

    with pytest.raises(ValueError, match='cannot originate'):
        open_bundle(merlin, rewrite_signed_bundle(h_to_m, harbinger, merlin, harbinger_claims_draft), 'scribe')

    with merlin.tx() as connection:
        draft = merlin.put(connection, 'draft', {'title': 'Scribe draft', 'description': 'Synthetic'}, 'scribe')
        merlin.event(connection, 'scribe', 'draft.created', [draft['id']])
    m_to_h = build_bundle(merlin, [draft['id']], harbinger.setting('config')['instance_id'], 'lead-scribe')

    def merlin_claims_finding(manifest):
        manifest['records'][0]['kind'] = 'finding'
        manifest['records'][0]['data'] = {'title': 'Unauthorized finding', 'observation': 'Synthetic'}

    with pytest.raises(ValueError, match='cannot originate'):
        open_bundle(harbinger, rewrite_signed_bundle(m_to_h, merlin, harbinger, merlin_claims_finding), 'captain')


def test_same_source_revision_with_changed_body_is_a_non_accepting_conflict(peers):
    harbinger, merlin = peers
    finding = add_finding(harbinger)
    recipient = merlin.setting('config')['instance_id']
    open_bundle(merlin, build_bundle(harbinger, [finding['id']], recipient, 'captain'), 'scribe')
    repeated = build_bundle(harbinger, [finding['id']], recipient, 'captain')

    def change_body(manifest):
        manifest['records'][0]['data']['title'] = 'Changed body with reused revision'

    changed = rewrite_signed_bundle(repeated, harbinger, merlin, change_body)
    receipt = open_bundle(merlin, changed, 'scribe')
    assert receipt['status'] == 'conflict' and receipt['duplicates'] == 0
    conflict = merlin.records('transfer_conflict')[0]
    review = conflict_review(merlin, conflict)
    with pytest.raises(ValueError, match='revision identifier'):
        resolve_conflict(merlin, conflict['id'], 'accept_incoming', 'lead-scribe', **review)
    assert merlin.get(finding['id'])['data']['title'] == 'Synthetic lead'


def test_mixed_conflict_retains_complete_bundle_and_reports_deferred_records(peers):
    a, b = peers
    recipient = b.setting('config')['instance_id']
    with a.tx() as connection:
        asset = a.put(connection, 'asset', {'label': 'Fixture host', 'kind': 'host', 'track': 'network'}, 'fixture')
        finding = a.put(connection, 'finding', {
            'title': 'Synthetic lead', 'observation': 'Observed in a fixture',
            'asset_ids': [asset['id']], 'evidence_needed': 'Evidence pending',
        }, 'fixture')
        a.event(connection, 'fixture', 'finding.fixture_created', [asset['id'], finding['id']])
    open_bundle(b, build_bundle(a, [finding['id']], recipient, 'host'), 'scribe')

    upload_id = str(uuid.uuid4())
    body = b'fixture observation bytes\n'
    artifact = a.root / 'artifacts' / upload_id
    artifact.write_bytes(body)
    artifact.chmod(0o600)
    import hashlib
    with a.tx() as connection:
        upload = a.put(connection, 'upload', {
            'filename': 'fixture.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': upload_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True,
        }, 'fixture', upload_id)
        revised = a.put(connection, 'finding', {
            **finding['data'], 'observation': 'Later reviewed observation',
            'evidence_ids': [upload_id], 'evidence_needed': '',
        }, 'fixture', finding['id'], finding['revision_id'])
        a.event(connection, 'fixture', 'finding.fixture_updated', [revised['id'], upload['id']])

    encrypted = build_bundle(a, [revised['id']], recipient, 'host')
    receipt = open_bundle(b, encrypted, 'scribe')
    assert receipt['status'] == 'conflict' and receipt['imported'] == 0
    assert receipt['duplicates'] == 1
    assert receipt['conflicts'] == [finding['id']]
    assert receipt['deferred'] == [upload_id]
    assert b.get(upload_id) is None
    assert not (b.root / 'artifacts' / upload_id).exists()
    conflict = b.records('transfer_conflict')[0]['data']
    assert {record['id'] for record in conflict['incoming']} == {asset['id'], finding['id'], upload_id}
    assert conflict['conflict_ids'] == [finding['id']]
    assert conflict['duplicate_ids'] == [asset['id']]
    assert conflict['deferred_ids'] == [upload_id]
    assert conflict['files'] == [{'id': upload_id, 'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body)}]
    saved = b.root / 'conflicts' / conflict['encrypted_bundle']
    assert saved.read_bytes() == encrypted and saved.stat().st_mode & 0o077 == 0
    assert conflict['ciphertext_sha256'] == hashlib.sha256(encrypted).hexdigest()
    backup_path = b.root.parent / 'conflict-backup'
    backup(b, backup_path)
    restored = Store(backup_path)
    restored_conflict = restored.records('transfer_conflict')[0]['data']
    assert (restored.root / 'conflicts' / restored_conflict['encrypted_bundle']).read_bytes() == encrypted
    assert restored.verify()['events'] == b.verify()['events']
    conflict_record = b.records('transfer_conflict')[0]
    review = conflict_review(b, conflict_record)
    resolved = resolve_conflict(b, conflict_record['id'], 'keep_local', 'host-reviewer', **review)
    assert resolved['data']['state'] == 'resolved' and resolved['data']['resolution'] == 'keep_local'
    assert resolve_conflict(b, conflict_record['id'], 'keep_local', 'host-reviewer', **review)['revision_id'] == resolved['revision_id']
    assert saved.read_bytes() == encrypted and b.verify()['events'] > restored.verify()['events']
    assert open_bundle(b, encrypted, 'scribe') == receipt


def test_signed_malformed_record_is_rejected_before_storage(peers):
    a, b = peers
    with a.tx() as connection:
        asset = a.put(connection, 'asset', {'label': 'Fixture host', 'kind': 'host', 'track': 'network'}, 'fixture')
        a.event(connection, 'fixture', 'asset.fixture_created', [asset['id']])
    encrypted = build_bundle(a, [asset['id']], b.setting('config')['instance_id'], 'host')
    malformed = rewrite_signed_bundle(encrypted, a, b, lambda manifest: manifest['records'][0].update(data={}))
    with pytest.raises(ValueError):
        open_bundle(b, malformed, 'scribe')
    assert not b.records('asset')


def test_signed_upload_metadata_must_match_the_evidence_file(peers):
    a, b = peers
    upload_id = str(uuid.uuid4())
    body = b'synthetic evidence\n'
    (a.root / 'artifacts' / upload_id).write_bytes(body)
    (a.root / 'artifacts' / upload_id).chmod(0o600)
    import hashlib
    with a.tx() as connection:
        upload = a.put(connection, 'upload', {
            'filename': 'fixture.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': upload_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True,
        }, 'fixture', upload_id)
        a.event(connection, 'fixture', 'upload.fixture_created', [upload_id])
    encrypted = build_bundle(a, [upload['id']], b.setting('config')['instance_id'], 'host')
    mismatched = rewrite_signed_bundle(encrypted, a, b, lambda manifest: manifest['records'][0]['data'].update(sha256='b' * 64))
    with pytest.raises(ValueError):
        open_bundle(b, mismatched, 'scribe')
    assert not b.records('upload') and not list((b.root / 'artifacts').iterdir())


def test_merged_upload_review_metadata_roundtrips_between_repositories(peers):
    sender, receiver = peers
    upload_id = str(uuid.uuid4())
    body = b'synthetic reviewed import\n'
    (sender.root / 'artifacts' / upload_id).write_bytes(body)
    (sender.root / 'artifacts' / upload_id).chmod(0o600)
    import hashlib
    merged_review = {
        'upload_revision_id': str(uuid.uuid4()),
        'preview_revision_id': str(uuid.uuid4()),
        'preview_hash': hashlib.sha256(b'synthetic preview').hexdigest(),
    }
    with sender.tx() as connection:
        upload = sender.put(connection, 'upload', {
            'filename': 'reviewed.json', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': upload_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True, 'merged_review': merged_review,
        }, 'fixture', upload_id)
        sender.event(connection, 'fixture', 'upload.fixture_created', [upload_id])
    encrypted = build_bundle(sender, [upload['id']], receiver.setting('config')['instance_id'], 'host')
    receipt = open_bundle(receiver, encrypted, 'scribe')
    assert receipt['status'] == 'imported'
    assert receiver.get(upload_id)['data']['merged_review'] == merged_review
    assert (receiver.root / 'artifacts' / upload_id).read_bytes() == body


@pytest.mark.parametrize('failure_site', ('put', 'event', 'commit'))
def test_failed_import_does_not_leave_new_unindexed_evidence(peers, monkeypatch, failure_site):
    sender, receiver = peers
    upload_id = str(uuid.uuid4())
    existing_id = str(uuid.uuid4())
    body = b'synthetic imported evidence\n'
    existing_body = b'pre-existing local evidence\n'
    (receiver.root / 'artifacts' / existing_id).write_bytes(existing_body)
    (receiver.root / 'artifacts' / existing_id).chmod(0o600)
    (sender.root / 'artifacts' / upload_id).write_bytes(body)
    (sender.root / 'artifacts' / upload_id).chmod(0o600)
    import hashlib
    with sender.tx() as connection:
        upload = sender.put(connection, 'upload', {
            'filename': 'fixture.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': upload_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True,
        }, 'fixture', upload_id)
        sender.event(connection, 'fixture', 'upload.fixture_created', [upload_id])
    encrypted = build_bundle(sender, [upload['id']], receiver.setting('config')['instance_id'], 'host')

    if failure_site == 'put':
        monkeypatch.setattr(receiver, 'put', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('injected put failure')))
    elif failure_site == 'event':
        monkeypatch.setattr(receiver, 'event', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('injected event failure')))
    else:
        original_tx = receiver.tx

        @contextlib.contextmanager
        def fail_commit():
            with original_tx() as connection:
                yield connection
                raise RuntimeError('injected commit failure')

        monkeypatch.setattr(receiver, 'tx', fail_commit)

    with pytest.raises(RuntimeError, match=f'injected {failure_site} failure'):
        open_bundle(receiver, encrypted, 'scribe')
    assert receiver.get(upload_id) is None
    assert not (receiver.root / 'artifacts' / upload_id).exists()
    assert (receiver.root / 'artifacts' / existing_id).read_bytes() == existing_body


def test_failed_conflict_import_does_not_leave_unindexed_ciphertext(peers, monkeypatch):
    sender, receiver = peers
    incoming = add_finding(sender)
    with receiver.tx() as connection:
        receiver.put(connection, 'finding', {
            'title': 'Local conflict', 'observation': 'Synthetic local record',
        }, 'scribe', incoming['id'])
        receiver.event(connection, 'scribe', 'finding.fixture_created', [incoming['id']])
    encrypted = build_bundle(sender, [incoming['id']], receiver.setting('config')['instance_id'], 'host')
    monkeypatch.setattr(receiver, 'event', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('injected event failure')))

    with pytest.raises(RuntimeError, match='injected event failure'):
        open_bundle(receiver, encrypted, 'scribe')
    assert not receiver.records('transfer_conflict')
    assert not list((receiver.root / 'conflicts').iterdir())


def test_signed_reference_must_point_to_the_expected_record_kind(peers):
    a, b = peers
    with a.tx() as connection:
        asset = a.put(connection, 'asset', {'label': 'Fixture host', 'kind': 'host', 'track': 'network'}, 'fixture')
        finding = a.put(connection, 'finding', {
            'title': 'Synthetic finding', 'observation': 'Fixture', 'asset_ids': [asset['id']],
        }, 'fixture')
        a.event(connection, 'fixture', 'finding.fixture_created', [asset['id'], finding['id']])
    encrypted = build_bundle(a, [finding['id']], b.setting('config')['instance_id'], 'host')
    def wrong_kind(manifest):
        transferred_finding = next(record for record in manifest['records'] if record['kind'] == 'finding')
        transferred_finding['data']['asset_ids'] = [transferred_finding['id']]
    malformed = rewrite_signed_bundle(encrypted, a, b, wrong_kind)
    with pytest.raises(ValueError):
        open_bundle(b, malformed, 'scribe')
    assert not b.records('finding') and not b.records('asset')


def test_peer_receipt_must_bind_bundle_and_manifest():
    record_id = str(uuid.uuid4())
    expected = {'bundle_id': str(uuid.uuid4()), 'manifest_hash': 'a' * 64, 'record_ids': [record_id]}
    valid = {k: expected[k] for k in ('bundle_id', 'manifest_hash')} | {'status': 'imported', 'imported': 1, 'duplicates': 0, 'conflicts': [], 'deferred': []}
    assert validate_receipt(valid, expected) == valid
    with pytest.raises(ValueError):
        validate_receipt({**valid, 'manifest_hash': 'b' * 64}, expected)
    with pytest.raises(ValueError):
        validate_receipt({'status': 'delivered'}, expected)


def test_minimized_facts_remain_confidential():
    rows = [{'id': 'private-host', 'kind': 'asset', 'data': {'label': 'private.example.test', 'kind': 'service', 'track': 'network', 'data': {'port': 443, 'protocol': 'tcp', 'password': 'synthetic-only'}}}]
    result = minimize(rows, b'synthetic-key-for-testing-only-1234')
    encoded = json.dumps(result)
    assert 'private.example.test' not in encoded and 'synthetic-only' not in encoded
    assert result['output']['classification'] == 'client-confidential-minimized'
    assert result['receipt']['disclosure_authorized'] is False
    assert result['output']['assets'][0]['facts'] == {'port': 443, 'protocol': 'tcp'}
