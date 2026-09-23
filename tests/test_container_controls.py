import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from workspace import APP_NAME
from workspace.auth import add_user
from workspace.cli import backup, encrypted_storage, initialize, restore
from workspace.isolation import queue_parser
from workspace.parser_service import atomic_file
from workspace.server import create_app
from workspace.store import Store
from workspace.transfer import enroll, peer_http_headers, provision_keys, public_card


NMAP = b'<nmaprun><host><address addr="192.0.2.44" addrtype="ipv4"/><ports><port protocol="tcp" portid="80"><state state="open"/><service name="http"/></port></ports></host><runstats><finished exit="success"/></runstats></nmaprun>'


def test_parser_result_is_published_only_after_complete_short_writes(tmp_path, monkeypatch):
    target = tmp_path / 'job.response'
    payload = b'{"complete":true,"records":[' + b'"fixture",' * 4096 + b'"done"]}'
    real_write = os.write
    real_replace = os.replace
    replace_seen = []

    def short_write(fd, value):
        return real_write(fd, bytes(value[:max(1, len(value) // 3)]))

    def checked_replace(source, destination):
        assert not target.exists()
        assert Path(source).read_bytes() == payload
        replace_seen.append(True)
        return real_replace(source, destination)

    monkeypatch.setattr(os, 'write', short_write)
    monkeypatch.setattr(os, 'replace', checked_replace)
    atomic_file(target, payload)
    assert replace_seen == [True]
    assert target.read_bytes() == payload


def test_failed_parser_publication_leaves_no_final_or_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / 'job.response'
    monkeypatch.setattr(os, 'fsync', lambda _fd: (_ for _ in ()).throw(OSError('fixture fsync failure')))
    with pytest.raises(OSError):
        atomic_file(target, b'partial fixture')
    assert not target.exists()
    assert not list(tmp_path.iterdir())


@pytest.fixture
def store(tmp_path):
    return initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic practice', engagement_id='00000000-0000-4000-8000-000000000001')


@pytest.fixture
def client(store):
    role = 'captain' if APP_NAME == 'Harbinger' else 'lead_scribe'
    add_user(store, 'host', role, 'synthetic-test-password', APP_NAME)
    value = TestClient(create_app(store.root), base_url='http://127.0.0.1:8710', headers={'Origin': 'http://127.0.0.1:8710'})
    response = value.post('/api/login', json={'name': 'host', 'password': 'synthetic-test-password'})
    assert response.status_code == 200
    value.headers['X-CSRF-Token'] = response.json()['csrf']
    with value:
        yield value


def preview_delivery(client, draft, report_id='7'):
    return client.post('/api/deliveries/preview', json={
        'draft_id': draft['id'],
        'draft_revision_id': draft['revision_id'],
        'report_id': report_id,
    })


def review_preview(client, preview):
    body = preview.json()
    return client.post('/api/deliveries/review', json={
        'draft_id': body['draft_id'],
        'draft_revision_id': body['draft_revision_id'],
        'report_id': body['report_id'],
        'proposal_id': body['proposal_id'],
        'proposal_hash': body['proposal_hash'],
    })


def review_delivery(client, draft, report_id='7'):
    preview = preview_delivery(client, draft, report_id)
    assert preview.status_code == 200, preview.text
    return review_preview(client, preview)


def send_delivery(client, delivery, **overrides):
    body = {
        'delivery_revision_id': delivery['revision_id'],
        'payload_hash': delivery['data']['payload_hash'],
        **overrides,
    }
    return client.post('/api/deliveries/' + delivery['id'] + '/send', json=body)


def reconcile_delivery(client, delivery, remote_id, **overrides):
    body = {
        'delivery_revision_id': delivery['revision_id'],
        'payload_hash': delivery['data']['payload_hash'],
        'remote_id': str(remote_id),
        **overrides,
    }
    return client.post('/api/deliveries/' + delivery['id'] + '/reconcile', json=body)


def install_ghostwriter_token(store):
    path = store.root / 'keys' / 'ghostwriter.token'
    path.write_text('synthetic-scoped-token')
    path.chmod(0o600)
    return path


def completed_graphql(result):
    def execute(prepared):
        prepared[0].close()
        return result
    return execute


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin verifies the Ghostwriter role schema')
def test_ghostwriter_probe_accepts_server_owned_added_as_blank_field(store, monkeypatch):
    from workspace.ghostwriter_adapter import verify_adapter
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1,
    })
    install_ghostwriter_token(store)
    live_insert_fields = {
        'title', 'description', 'impact', 'mitigation', 'references', 'reportId',
        'severityId', 'findingTypeId', 'position', 'extraFields', 'complete',
    }
    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', completed_graphql({
        '__type': {'inputFields': [{'name': name} for name in sorted(live_insert_fields)]},
        '__schema': {'mutationType': {'fields': [{'name': 'insert_reportedFinding_one'}]}},
    }))
    assert len(verify_adapter(store)) == 64


def audit_operations(store):
    return [json.loads(line)['body']['operation'] for line in (store.root / 'audit.jsonl').read_text().splitlines()]


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin uses the Ghostwriter transport')
def test_execute_graphql_reads_and_closes_real_httpx_response():
    from workspace.ghostwriter_adapter import execute_graphql

    responses = []

    def handler(request):
        response = httpx.Response(200, json={'data': {'receipt': 'synthetic'}}, request=request)
        responses.append(response)
        return response

    client = httpx.Client(transport=httpx.MockTransport(handler))
    request = client.build_request('POST', 'http://ghostwriter.test/v1/graphql', json={})

    assert execute_graphql((client, request)) == {'receipt': 'synthetic'}
    assert responses[0].is_closed
    assert client.is_closed


def install_evidence(store, body, filename, *, reviewed=True, quarantined=False):
    evidence_id = str(uuid.uuid4())
    artifact = store.root / 'artifacts' / evidence_id
    artifact.write_bytes(body)
    artifact.chmod(0o600)
    with store.tx() as connection:
        item = store.put(connection, 'upload', {
            'filename': filename, 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': evidence_id, 'quarantined': quarantined, 'limitations': [],
            'reviewed_for_export': reviewed,
        }, 'fixture', evidence_id)
        store.event(connection, 'fixture', 'evidence.fixture_created', [evidence_id])
    return item


def test_inline_evidence_images_require_review_and_safe_local_bytes(client, store):
    store = client.app.state.store
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=')
    approved = install_evidence(store, png, 'fixture.png')
    rendered = client.get(f"/api/evidence/{approved['id']}/render")
    assert rendered.status_code == 200
    assert rendered.content == png
    assert rendered.headers['content-type'] == 'image/png'
    assert rendered.headers['content-disposition'].startswith('inline;')
    assert rendered.headers['x-content-type-options'] == 'nosniff'
    assert audit_operations(store)[-1] == 'evidence.rendered'

    pending = install_evidence(store, png, 'pending.png', reviewed=False)
    assert client.get(f"/api/evidence/{pending['id']}/render").status_code == 409
    svg = install_evidence(store, b'<svg xmlns="http://www.w3.org/2000/svg"><script>fixture</script></svg>', 'blocked.svg')
    assert client.get(f"/api/evidence/{svg['id']}/render").status_code == 415

    anonymous = TestClient(client.app, base_url='http://127.0.0.1:8710', headers={'Origin': 'http://127.0.0.1:8710'})
    assert anonymous.get(f"/api/evidence/{approved['id']}/render").status_code == 401


def test_container_storage_marker_is_private_and_typed(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTAINERIZED', '1')
    marker = tmp_path / '.storage-verified.json'
    marker.write_text(json.dumps({'encrypted': True, 'schema_version': 1, 'verified_at': '2026-09-08T00:00:00.000000Z'}))
    marker.chmod(0o600)
    assert encrypted_storage(tmp_path)
    marker.chmod(0o644)
    assert not encrypted_storage(tmp_path)


@pytest.mark.parametrize('subtree,kind', [
    ('artifacts', 'root'), ('conflicts', 'root'), ('exports', 'root'), ('keys', 'root'),
    ('artifacts', 'nested'), ('conflicts', 'nested'), ('exports', 'nested'), ('keys', 'nested'),
])
def test_backup_rejects_symlinks_without_copying_external_fixture_bytes(tmp_path, subtree, kind):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    external = tmp_path / 'external-secret'; external.write_bytes(b'external synthetic secret')
    target = store.root / subtree
    if kind == 'root':
        target.rmdir(); target.symlink_to(external)
    else:
        (target / 'nested').symlink_to(external)
    destination = tmp_path / 'backup'
    with pytest.raises(ValueError, match='symlink'):
        backup(store, destination)
    assert not destination.exists() and external.read_bytes() == b'external synthetic secret'


@pytest.mark.parametrize('subtree,kind', [
    ('artifacts', 'root'), ('conflicts', 'root'), ('exports', 'root'), ('keys', 'root'),
    ('artifacts', 'nested'), ('conflicts', 'nested'), ('exports', 'nested'), ('keys', 'nested'),
])
def test_restore_rejects_source_symlinks_before_creating_destination(tmp_path, subtree, kind):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    source = tmp_path / 'backup'; backup(store, source)
    external = tmp_path / 'external-secret'; external.write_bytes(b'external synthetic secret')
    target = source / subtree
    if kind == 'root':
        target.rmdir(); target.symlink_to(external)
    else:
        (target / 'nested').symlink_to(external)
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='symlink'):
        restore(source, destination)
    assert not destination.exists()


def test_restore_rejects_unexpected_empty_backup_directory(tmp_path):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    source = tmp_path / 'backup'; backup(store, source)
    (source / 'unexpected').mkdir(mode=0o700)
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='file list'):
        restore(source, destination)
    assert not destination.exists()


def test_backup_copy_retries_interrupted_and_short_writes(tmp_path, monkeypatch):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    real_write = os.write
    calls = 0

    def interrupted_then_short(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError
        return real_write(fd, data[:max(1, len(data) // 3)])

    monkeypatch.setattr(os, 'write', interrupted_then_short)
    result = backup(store, tmp_path / 'backup')
    assert result['files'] >= 3 and calls > 3
    assert restore(tmp_path / 'backup', tmp_path / 'restored')['files'] == result['files']


def test_backup_copy_failure_never_publishes_partial_destination(tmp_path, monkeypatch):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    destination = tmp_path / 'backup'
    monkeypatch.setattr(os, 'write', lambda *_: (_ for _ in ()).throw(OSError('synthetic disk failure')))
    with pytest.raises(OSError, match='synthetic disk failure'):
        backup(store, destination)
    assert not destination.exists()


def test_backup_seals_database_without_sqlite_sidecars(tmp_path):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    destination = tmp_path / 'backup'
    backup(store, destination)
    assert not (destination / 'workspace.db-wal').exists()
    assert not (destination / 'workspace.db-shm').exists()
    with sqlite3.connect(f'file:{destination / "workspace.db"}?mode=ro&immutable=1', uri=True) as copied:
        assert copied.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'delete'
    assert not (destination / 'workspace.db-wal').exists()
    assert not (destination / 'workspace.db-shm').exists()


def test_default_readonly_store_still_rejects_sqlite_sidecars(tmp_path):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    (store.root / 'workspace.db-wal').touch(mode=0o600)
    with pytest.raises(RuntimeError, match='sidecar'):
        Store(store.root, readonly=True)


def test_wal_aware_readonly_store_rejects_unsafe_sidecars(tmp_path):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    external = tmp_path / 'external'; external.write_bytes(b'synthetic')
    (store.root / 'workspace.db-wal').symlink_to(external)
    with pytest.raises(RuntimeError, match='permissions'):
        Store(store.root, readonly=True, wal_aware_readonly=True)


def test_restore_reads_backup_identity_with_immutable_sqlite_uri(tmp_path, monkeypatch):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    source = tmp_path / 'backup'
    backup(store, source)
    import workspace.cli as cli
    real_connect = cli.sqlite3.connect
    observed = []

    def track_connect(database, *args, **kwargs):
        observed.append((database, kwargs.copy()))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(cli.sqlite3, 'connect', track_connect)
    restore(source, tmp_path / 'restored')
    assert observed[0] == (f'file:{source.absolute() / "workspace.db"}?mode=ro&immutable=1', {'uri': True})


@pytest.mark.parametrize(('mutation', 'extra_byte'), [('source', b''), ('staged', b'X')])
def test_restore_rejects_changed_bytes_before_publishing(tmp_path, monkeypatch, mutation, extra_byte):
    store = initialize(tmp_path / 'state', 'http://127.0.0.1:8710', 'Synthetic')
    source = tmp_path / 'backup'; backup(store, source)
    original = (source / 'audit.jsonl').read_bytes()
    changed = b'X' * len(original) + extra_byte
    import workspace.cli as cli
    copy_regular = cli._copy_regular

    def mutate_copy(source_path, target):
        source_path, target = Path(source_path), Path(target)
        if source_path == source / 'audit.jsonl' and mutation == 'source':
            source_path.write_bytes(changed)
        copy_regular(source_path, target)
        if source_path == source / 'audit.jsonl' and mutation == 'staged':
            target.write_bytes(changed)

    monkeypatch.setattr(cli, '_copy_regular', mutate_copy)
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='restored file failed'):
        restore(source, destination)
    assert not destination.exists()


def test_client_http_peer_request_is_rejected_before_nonce_or_audit_write(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTAINERIZED', '1')
    receiver_root = tmp_path / 'receiver'; receiver_root.mkdir(mode=0o700)
    marker = receiver_root / '.storage-verified.json'
    marker.write_text(json.dumps({'schema_version': 1, 'encrypted': True, 'verified_at': '2026-09-09T00:00:00.000000Z'})); marker.chmod(0o600)
    receiver = initialize(receiver_root, 'https://receiver.test', 'Synthetic', mode='client', peer_origin='https://peer.test')
    sender = initialize(tmp_path / 'sender', 'https://sender.test', 'Synthetic', engagement_id=receiver.setting('config')['engagement']['id'])
    sender.configure('config', {**sender.setting('config'), 'app': 'Harbinger'})
    provision_keys(receiver); provision_keys(sender)
    enroll(receiver, public_card(sender), __import__('workspace.store', fromlist=['digest']).digest(public_card(sender)))
    enroll(sender, public_card(receiver), __import__('workspace.store', fromlist=['digest']).digest(public_card(receiver)))
    body = b'synthetic signed peer body'; headers = peer_http_headers(sender, sender.setting('peers')[0], body)
    response = TestClient(create_app(receiver.root), base_url='http://peer.test').post('/peer/inbox', content=body, headers=headers)
    assert response.status_code == 403
    with receiver.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM peer_http_nonces').fetchone()[0] == 0
    assert 'peer.request_authorized' not in (receiver.root / 'audit.jsonl').read_text()


def test_client_backup_restore_preserves_container_storage_marker(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTAINERIZED', '1')

    def marked(path):
        path.mkdir(mode=0o700)
        marker = path / '.storage-verified.json'
        marker.write_text(json.dumps({'encrypted': True, 'schema_version': 1, 'verified_at': '2026-09-08T00:00:00.000000Z'}))
        marker.chmod(0o600)
        return path

    source = marked(tmp_path / 'source')
    store = initialize(source, 'https://merlin.example.test', 'Client restore fixture', mode='client')
    with store.tx() as connection:
        draft = store.put(connection, 'draft', {'title': 'Client restore fixture'}, 'fixture')
        store.event(connection, 'fixture', 'draft.fixture_created', [draft['id']])
    backup_parent = marked(tmp_path / 'backup-parent')
    backup_path = backup_parent / 'backup'
    backup(store, backup_path)
    restore_parent = marked(tmp_path / 'restore-parent')
    restored_path = restore_parent / 'state'
    restore(backup_path, restored_path)
    restored = Store(restored_path)
    assert restored.setting('config')['mode'] == 'client'
    assert restored.get(draft['id'])['revision_id'] == draft['revision_id']
    assert encrypted_storage(restored_path)


@pytest.mark.skipif(APP_NAME != 'Harbinger', reason='Only Harbinger runs an import parser')
def test_parser_queue_roundtrip_and_cleanup(tmp_path):
    queue = tmp_path / 'queue'
    queue.mkdir(mode=0o700)
    source = tmp_path / 'input.xml'
    source.write_bytes(NMAP)
    project = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, '-B', '-m', 'workspace.parser_service', str(queue)],
        cwd=project,
        env={'PATH': os.environ.get('PATH', '/usr/bin'), 'PYTHONPATH': str(project)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        time.sleep(0.2)
        assert process.poll() is None
        result = queue_parser(source, 'nmap_xml', queue)
        assert result['complete'] is True
        assert any(item['label'] == '192.0.2.44' for item in result['assets'])
        assert not list(queue.iterdir())
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_lead_drafting_boundary(client, store):
    store = client.app.state.store
    with store.tx() as connection:
        lead = store.put(connection, 'lead', {
            'title': 'Synthetic lead',
            'observation': 'A controlled fixture exposed a service.',
            'impact': 'Review is required.',
            'evidence_ids': [],
        }, 'fixture')
        store.event(connection, 'fixture', 'lead.received', [lead['id']])
    response = client.post('/api/leads/' + lead['id'] + '/draft')
    if APP_NAME == 'Harbinger':
        assert response.status_code == 403
        return
    assert response.status_code == 200
    first = response.json()
    assert first['data']['lead_id'] == lead['id']
    assert first['data']['description'] == lead['data']['observation']
    again = client.post('/api/leads/' + lead['id'] + '/draft')
    assert again.status_code == 200 and again.json()['id'] == first['id']


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Merlin owns immutable lead-backed drafts')
def test_browser_cannot_forge_or_replace_a_draft_lead(client, store):
    forged = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Forged draft', 'lead_id': 'forged-lead',
    }})
    assert forged.status_code == 403
    store = client.app.state.store
    with store.tx() as connection:
        lead = store.put(connection, 'lead', {
            'title': 'Synthetic lead', 'observation': 'Observed fixture.', 'evidence_ids': [],
        }, 'fixture')
        store.event(connection, 'fixture', 'lead.received', [lead['id']])
    draft = client.post('/api/leads/' + lead['id'] + '/draft').json()
    changed = client.put('/api/records/' + draft['id'], json={
        'base_revision_id': draft['revision_id'],
        'data': {**draft['data'], 'lead_id': 'another-lead'},
    })
    assert changed.status_code == 403
    assert store.get(draft['id'])['data']['lead_id'] == lead['id']


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin delivers reviewed drafts')
def test_delivery_blocks_parallel_and_later_revision(client, store):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999',
        'report_id': '7',
        'severity_id': 1,
        'finding_type_id': 1,
        'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    created = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Synthetic draft', 'description': 'Description', 'impact': 'Impact',
        'remediation': 'Fix', 'references': '', 'evidence_ids': [], 'owner_id': '', 'lead_id': '',
    }}).json()
    first_preview = preview_delivery(client, created)
    assert first_preview.status_code == 200
    first = review_preview(client, first_preview)
    assert first.status_code == 200
    exact_retry = review_preview(client, first_preview)
    assert exact_retry.status_code == 200
    assert exact_retry.json()['id'] == first.json()['id']
    parallel_preview = preview_delivery(client, created)
    assert parallel_preview.status_code == 200
    parallel = review_preview(client, parallel_preview)
    assert parallel.status_code == 409
    edited = client.put('/api/records/' + created['id'], json={
        'base_revision_id': created['revision_id'],
        'data': {**created['data'], 'description': 'Later description'},
    })
    assert edited.status_code == 200
    assert store.get(first.json()['id'])['data']['status'] == 'rejected'
    later = review_delivery(client, edited.json())
    assert later.status_code == 200


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin reviews Ghostwriter drafts')
def test_delivery_review_rejects_a_stale_displayed_revision_from_another_session(client, store):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    displayed = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Displayed draft', 'description': 'First saved revision', 'evidence_ids': [],
    }}).json()
    second = TestClient(client.app, base_url='http://127.0.0.1:8710', headers={'Origin': 'http://127.0.0.1:8710'})
    login = second.post('/api/login', json={'name': 'host', 'password': 'synthetic-test-password'})
    assert login.status_code == 200
    second.headers['X-CSRF-Token'] = login.json()['csrf']
    preview = preview_delivery(client, displayed)
    assert preview.status_code == 200
    changed = second.put('/api/records/' + displayed['id'], json={
        'base_revision_id': displayed['revision_id'],
        'data': {**displayed['data'], 'description': 'Second session revision'},
    })
    assert changed.status_code == 200
    stale = review_preview(client, preview)
    assert stale.status_code == 409
    assert store.records('delivery') == []


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin reviews Ghostwriter drafts')
def test_delivery_preview_binds_exact_payload_and_destination_without_review_side_effects(client, store):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    config = {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    }
    store.configure('ghostwriter', config)
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Exact preview', 'description': 'Rendered **description**',
        'impact': 'Rendered impact', 'remediation': 'Rendered fix',
        'references': 'https://example.test/reference', 'evidence_ids': [],
    }}).json()
    preview = preview_delivery(client, draft)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['draft_id'] == draft['id']
    assert body['draft_revision_id'] == draft['revision_id']
    assert body['report_id'] == '7'
    assert body['destination_origin'] == config['origin']
    assert body['adapter_id'] == ADAPTER
    assert body['payload']['title'] == 'Exact preview'
    assert '<strong>description</strong>' in body['payload']['description']
    assert body['payload']['extraFields']['merlin_proposal_id'] == body['proposal_id']
    assert len(body['proposal_hash']) == 64
    assert len(body['payload_hash']) == 64
    assert store.records('delivery') == []

    store.configure('ghostwriter', {**config, 'severity_id': 2})
    event_count = store.verify()['events']
    stale = review_preview(client, preview)
    assert stale.status_code == 409
    assert store.verify()['events'] == event_count
    assert store.records('delivery') == []


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin dispatches Ghostwriter drafts')
def test_delivery_send_binding_rejects_stale_client_before_network(client, store, monkeypatch):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    config = {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    }
    store.configure('ghostwriter', config)
    install_ghostwriter_token(store)
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Dispatch binding', 'description': 'Description', 'evidence_ids': [],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    calls = []

    def request(prepared):
        calls.append(True)
        prepared[0].close()
        stored = store.get(reviewed['id'])['data']
        return {'insert_reportedFinding_one': {
            'id': 77, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': stored['payload']['extraFields'],
        }}

    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', request)
    stale = send_delivery(client, reviewed, delivery_revision_id=str(uuid.uuid4()))
    assert stale.status_code == 409
    assert calls == []
    assert store.get(reviewed['id'])['data']['status'] == 'reviewed'

    store.configure('ghostwriter', {**config, 'finding_type_id': 2})
    changed_destination = send_delivery(client, reviewed)
    assert changed_destination.status_code == 409
    assert calls == []
    store.configure('ghostwriter', config)

    delivered = send_delivery(client, reviewed)
    assert delivered.status_code == 200
    assert delivered.json()['data']['status'] == 'delivered'
    assert len(calls) == 1


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin dispatches Ghostwriter drafts')
def test_post_dispatch_persistence_failure_recovers_as_uncertain(client, store, monkeypatch):
    from workspace.ghostwriter_adapter import ADAPTER

    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    install_ghostwriter_token(store)
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Post-dispatch recovery', 'description': 'Synthetic description', 'evidence_ids': [],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    stored = store.get(reviewed['id'])['data']
    dispatches = []

    def confirmed(prepared):
        dispatches.append(True)
        prepared[0].close()
        return {'insert_reportedFinding_one': {
            'id': 88, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': stored['payload']['extraFields'],
        }}

    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', confirmed)
    real_put = store.put

    def fail_final_put(connection, kind, data, actor, id=None, base=None):
        if kind == 'delivery' and data.get('status') in ('delivered', 'uncertain'):
            raise OSError('synthetic final persistence failure')
        return real_put(connection, kind, data, actor, id, base)

    monkeypatch.setattr(store, 'put', fail_final_put)
    with pytest.raises(OSError, match='synthetic final persistence failure'):
        send_delivery(client, reviewed)
    assert dispatches == [True]
    assert store.get(reviewed['id'])['data']['status'] == 'sending'

    monkeypatch.setattr(store, 'put', real_put)
    recovered = create_app(store.root).state.store
    current = recovered.get(reviewed['id'])
    assert current['data']['status'] == 'uncertain'
    assert audit_operations(recovered)[-1] == 'operation.interrupted'


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin dispatches Ghostwriter drafts')
def test_ambiguous_ghostwriter_timeout_stays_uncertain_without_automatic_retry(client, store, monkeypatch):
    from workspace.ghostwriter_adapter import ADAPTER

    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    install_ghostwriter_token(store)
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Ambiguous timeout', 'description': 'Synthetic description', 'evidence_ids': [],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    attempts = []

    def timeout(prepared):
        attempts.append(True)
        prepared[0].close()
        raise httpx.ReadTimeout('synthetic ambiguous timeout')

    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', timeout)
    uncertain = send_delivery(client, reviewed)
    assert uncertain.status_code == 200
    assert uncertain.json()['data']['status'] == 'uncertain'
    assert attempts == [True]

    repeated = send_delivery(client, uncertain.json())
    assert repeated.status_code == 409
    assert attempts == [True]


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin dispatches Ghostwriter drafts')
def test_delivery_local_preflight_failure_does_not_enter_uncertain_state(client, store, monkeypatch):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Preflight binding', 'description': 'Description', 'evidence_ids': [],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    before = audit_operations(store)
    client_calls = []

    def unexpected_client(*args, **kwargs):
        client_calls.append(True)
        raise AssertionError('HTTP client construction must not begin without a token')

    monkeypatch.setattr('workspace.ghostwriter_adapter.httpx.Client', unexpected_client)
    missing_token = send_delivery(client, reviewed)
    assert missing_token.status_code == 503
    assert missing_token.json()['detail'] == 'The scoped Ghostwriter token is unavailable.'
    assert store.get(reviewed['id'])['data']['status'] == 'reviewed'
    assert audit_operations(store) == before
    assert client_calls == []

    install_ghostwriter_token(store)
    constructor_calls = []

    def failed_client(*args, **kwargs):
        constructor_calls.append(True)
        raise RuntimeError('synthetic client construction failure')

    monkeypatch.setattr('workspace.ghostwriter_adapter.httpx.Client', failed_client)
    client_failure = send_delivery(client, reviewed)
    assert client_failure.status_code == 503
    assert store.get(reviewed['id'])['data']['status'] == 'reviewed'
    assert audit_operations(store) == before
    assert constructor_calls == [True]
    assert 'delivery.dispatch' not in audit_operations(store)[len(before):]


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin dispatches Ghostwriter drafts')
def test_ghostwriter_token_and_authorization_header_never_enter_readable_outputs(client, store, monkeypatch, caplog):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    token_path = install_ghostwriter_token(store)
    token = token_path.read_text()
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Secret isolation', 'description': 'Synthetic description', 'evidence_ids': [],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    stored = store.get(reviewed['id'])['data']

    def confirmed(prepared):
        request = prepared[1]
        assert request.headers['authorization'] == 'Bearer ' + token
        prepared[0].close()
        return {'insert_reportedFinding_one': {
            'id': 73, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': stored['payload']['extraFields'],
        }}

    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', confirmed)
    response = send_delivery(client, reviewed)
    assert response.status_code == 200
    stale = send_delivery(client, reviewed)
    assert stale.status_code == 409
    exposed = b'\n'.join([
        (store.root / 'audit.jsonl').read_bytes(),
        (store.root / 'transcript.log').read_bytes(),
        json.dumps(response.json(), sort_keys=True).encode(),
        json.dumps(stale.json(), sort_keys=True).encode(),
        caplog.text.encode(),
    ])
    assert token.encode() not in exposed
    assert b'Authorization' not in exposed
    assert b'Bearer ' not in exposed


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin prepares evidence-bound Ghostwriter proposals')
def test_delivery_binds_reviewed_evidence_without_sending_file_bytes(client, store, monkeypatch):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    install_ghostwriter_token(store)
    evidence_id = __import__('uuid').uuid4().hex
    evidence_id = str(__import__('uuid').UUID(evidence_id))
    evidence_body = b'fixture evidence body\n'
    artifact = store.root / 'artifacts' / evidence_id
    artifact.write_bytes(evidence_body)
    artifact.chmod(0o600)
    with store.tx() as connection:
        upload = store.put(connection, 'upload', {
            'filename': 'fixture.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(evidence_body).hexdigest(), 'size': len(evidence_body),
            'artifact_id': evidence_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True,
        }, 'fixture', evidence_id)
        store.event(connection, 'fixture', 'evidence.fixture_created', [upload['id']])
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Evidence-bound draft', 'description': 'Description', 'impact': 'Impact',
        'remediation': 'Fix', 'references': '', 'evidence_ids': [evidence_id],
    }}).json()
    reviewed = review_delivery(client, draft)
    assert reviewed.status_code == 200, reviewed.text
    visible = reviewed.json()['data']
    assert visible['attachment_state'] == 'manual_required'
    assert visible['evidence_ids'] == [evidence_id]
    assert 'payload' not in visible
    stored = store.get(reviewed.json()['id'])['data']
    assert 'addedAsBlank' not in stored['payload']
    manifest = stored['payload']['extraFields']['merlin_evidence_manifest']
    assert manifest == [{'id': evidence_id, 'filename': 'fixture.txt', 'sha256': hashlib.sha256(evidence_body).hexdigest(), 'size': len(evidence_body)}]
    assert stored['payload']['extraFields']['merlin_evidence_attachment'] == 'manual_required'
    assert evidence_body.decode().strip() not in json.dumps(stored['payload'])
    artifact.write_bytes(b'changed after review\n')
    blocked = send_delivery(client, reviewed.json())
    assert blocked.status_code == 503
    assert store.get(reviewed.json()['id'])['data']['status'] == 'reviewed'
    artifact.write_bytes(evidence_body)
    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', completed_graphql({
        'insert_reportedFinding_one': {
            'id': 41, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': stored['payload']['extraFields'],
        }
    }))
    delivered = send_delivery(client, reviewed.json())
    assert delivered.status_code == 200
    assert delivered.json()['data']['status'] == 'delivered'
    assert delivered.json()['data']['attachment_state'] == 'manual_required'


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin prepares evidence-bound Ghostwriter proposals')
def test_delivery_rejects_unreviewed_or_changed_evidence(client, store):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    evidence_id = str(__import__('uuid').uuid4())
    body = b'fixture evidence\n'
    artifact = store.root / 'artifacts' / evidence_id
    artifact.write_bytes(body)
    artifact.chmod(0o600)
    with store.tx() as connection:
        upload = store.put(connection, 'upload', {
            'filename': 'fixture.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': evidence_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': False,
        }, 'fixture', evidence_id)
        store.event(connection, 'fixture', 'evidence.fixture_created', [upload['id']])
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Blocked evidence draft', 'description': 'Description', 'evidence_ids': [evidence_id],
    }}).json()
    assert preview_delivery(client, draft).status_code == 503
    with store.tx() as connection:
        current = store.get(evidence_id, connection)
        store.put(connection, 'upload', {**current['data'], 'reviewed_for_export': True}, 'fixture', evidence_id, current['revision_id'])
        store.event(connection, 'fixture', 'evidence.fixture_reviewed', [evidence_id])
    artifact.write_bytes(b'changed evidence\n')
    assert preview_delivery(client, draft).status_code == 503


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin verifies Ghostwriter receipts')
@pytest.mark.parametrize('remote_id', [42, 9007199254740993])
def test_delivery_requires_exact_remote_metadata_and_reconciliation(client, store, monkeypatch, remote_id):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    install_ghostwriter_token(store)
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Receipt-bound draft', 'description': 'Description', 'evidence_ids': [],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    stored = store.get(reviewed['id'])['data']
    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', completed_graphql({
        'insert_reportedFinding_one': {
            'id': remote_id, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': {'merlin_proposal_id': reviewed['id']},
        }
    }))
    uncertain = send_delivery(client, reviewed)
    assert uncertain.status_code == 200 and uncertain.json()['data']['status'] == 'uncertain'
    reconciliation_calls = []
    def lookup(store, query, variables, operation, **kwargs):
        reconciliation_calls.append(variables)
        return {'reportedFinding_by_pk': {
            'id': remote_id, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': {**stored['payload']['extraFields'], 'merlin_revision_id': 'wrong'},
        }}
    monkeypatch.setattr('workspace.ghostwriter_adapter.request_graphql', lookup)
    stale = reconcile_delivery(client, uncertain.json(), remote_id, delivery_revision_id=reviewed['revision_id'])
    assert stale.status_code == 409
    assert reconciliation_calls == []
    rejected = reconcile_delivery(client, uncertain.json(), remote_id)
    assert rejected.status_code == 409
    assert reconciliation_calls == [{'id': remote_id}]
    current = store.get(reviewed['id'])
    assert current['data']['status'] == 'uncertain'
    monkeypatch.setattr('workspace.ghostwriter_adapter.request_graphql', lambda store, query, variables, operation, **kwargs: {
        'reportedFinding_by_pk': {
            'id': remote_id, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': stored['payload']['extraFields'],
        }
    })
    delivered = reconcile_delivery(client, current, remote_id)
    assert delivered.status_code == 200
    assert delivered.json()['data']['status'] == 'delivered'
    assert delivered.json()['data']['remote_id'] == str(remote_id)


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin reconciles Ghostwriter receipts')
def test_reconciliation_failure_requires_current_revision_before_retry(client, store, monkeypatch):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    install_ghostwriter_token(store)
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Retry-bound receipt', 'description': 'Description', 'evidence_ids': [],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    stored = store.get(reviewed['id'])['data']
    monkeypatch.setattr('workspace.ghostwriter_adapter.execute_graphql', completed_graphql({
        'insert_reportedFinding_one': {
            'id': 51, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': {'merlin_proposal_id': reviewed['id']},
        }
    }))
    uncertain = send_delivery(client, reviewed).json()
    lookup_calls = []

    def unavailable(*args, **kwargs):
        lookup_calls.append(True)
        raise RuntimeError('synthetic lookup failure')

    monkeypatch.setattr('workspace.ghostwriter_adapter.request_graphql', unavailable)
    failed = reconcile_delivery(client, uncertain, 51)
    assert failed.status_code == 503
    assert lookup_calls == [True]
    current = store.get(reviewed['id'])
    assert current['data']['status'] == 'uncertain'
    assert current['revision_id'] != uncertain['revision_id']

    stale = reconcile_delivery(client, uncertain, 51)
    assert stale.status_code == 409
    assert lookup_calls == [True]

    monkeypatch.setattr('workspace.ghostwriter_adapter.request_graphql', lambda store, query, variables, operation, **kwargs: {
        'reportedFinding_by_pk': {
            'id': 51, 'reportId': '7', 'title': stored['payload']['title'],
            'extraFields': stored['payload']['extraFields'],
        }
    })
    delivered = reconcile_delivery(client, current, 51)
    assert delivered.status_code == 200
    assert delivered.json()['data']['status'] == 'delivered'


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin recovers Ghostwriter deliveries')
def test_startup_recovers_sending_and_reconciling_deliveries_as_uncertain(tmp_path):
    recovery = initialize(
        tmp_path / 'recovery-state',
        'http://127.0.0.1:8710',
        'Synthetic recovery',
        engagement_id='00000000-0000-4000-8000-000000000002',
    )
    original_revisions = {}
    with recovery.tx() as connection:
        for status in ('sending', 'reconciling'):
            item = recovery.put(connection, 'delivery', {
                'status': status,
                'draft_id': str(uuid.uuid4()),
                'revision_id': str(uuid.uuid4()),
                'report_id': '7',
                'payload_hash': hashlib.sha256(status.encode()).hexdigest(),
                'payload': {'title': status},
                'config_hash': hashlib.sha256((status + '-config').encode()).hexdigest(),
                'approved_by': 'fixture',
            }, 'fixture')
            original_revisions[item['id']] = item['revision_id']
            recovery.event(connection, 'fixture', 'delivery.fixture_created', [item['id']])

    create_app(recovery.root)

    recovered = recovery.records('delivery')
    assert len(recovered) == 2
    assert all(item['data']['status'] == 'uncertain' for item in recovered)
    assert all(item['revision_id'] != original_revisions[item['id']] for item in recovered)
    assert audit_operations(recovery).count('operation.interrupted') == 2


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin rechecks evidence at dispatch')
def test_delivery_refuses_deleted_evidence_after_review(client, store):
    from workspace.ghostwriter_adapter import ADAPTER
    store = client.app.state.store
    store.configure('ghostwriter', {
        'origin': 'http://127.0.0.1:9999', 'report_id': '7', 'severity_id': 1,
        'finding_type_id': 1, 'schema_hash': 'fixture-schema', 'adapter_id': ADAPTER,
    })
    evidence_id = str(__import__('uuid').uuid4())
    body = b'fixture evidence\n'
    artifact = store.root / 'artifacts' / evidence_id
    artifact.write_bytes(body)
    artifact.chmod(0o600)
    with store.tx() as connection:
        store.put(connection, 'upload', {
            'filename': 'fixture.txt', 'format': 'manual_json', 'status': 'merged',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'artifact_id': evidence_id, 'quarantined': False, 'limitations': [],
            'reviewed_for_export': True,
        }, 'fixture', evidence_id)
        store.event(connection, 'fixture', 'evidence.fixture_created', [evidence_id])
    draft = client.post('/api/records', json={'kind': 'draft', 'data': {
        'title': 'Deleted evidence draft', 'description': 'Description', 'evidence_ids': [evidence_id],
    }}).json()
    reviewed = review_delivery(client, draft).json()
    artifact.unlink()
    assert send_delivery(client, reviewed).status_code == 503
    assert store.get(reviewed['id'])['data']['status'] == 'reviewed'


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin stores Ghostwriter delivery payloads')
def test_delivery_payload_is_absent_from_every_browser_read_route(client, store):
    store = client.app.state.store
    with store.tx() as connection:
        delivery = store.put(connection, 'delivery', {
            'status': 'reviewed', 'draft_id': 'draft-fixture', 'revision_id': 'revision-fixture',
            'report_id': '7', 'payload_hash': 'fixture-hash',
            'payload': {'description': 'client-confidential-fixture'},
            'config_hash': 'fixture-config', 'approved_by': 'fixture',
        }, 'fixture')
        store.event(connection, 'fixture', 'delivery.fixture_created', [delivery['id']])

    generic_list = client.get('/api/records?kind=delivery').json()['items'][0]
    generic_item = client.get('/api/records/' + delivery['id']).json()
    revisions = client.get('/api/records/' + delivery['id'] + '/revisions').json()['items']
    dedicated = client.get('/api/deliveries/' + delivery['id']).json()
    for exposed in [generic_list, generic_item, dedicated, *revisions]:
        assert 'payload' not in exposed['data']
        assert 'client-confidential-fixture' not in json.dumps(exposed)
    assert store.get(delivery['id'])['data']['payload']['description'] == 'client-confidential-fixture'


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin owns draft delivery state')
def test_reviewed_delivery_is_invalidated_by_a_draft_edit(client, store):
    store = client.app.state.store
    with store.tx() as connection:
        draft = store.put(connection, 'draft', {
            'title': 'Synthetic draft', 'description': 'First', 'impact': '', 'remediation': '',
            'references': '', 'evidence_ids': [], 'owner_id': '', 'lead_id': '',
        }, 'fixture')
        delivery = store.put(connection, 'delivery', {
            'status': 'reviewed', 'draft_id': draft['id'], 'revision_id': draft['revision_id'],
            'report_id': '7', 'payload_hash': 'fixture', 'payload': {}, 'config_hash': 'fixture',
            'approved_by': 'fixture',
        }, 'fixture')
        store.event(connection, 'fixture', 'delivery.fixture_created', [draft['id'], delivery['id']])
    edited = client.put('/api/records/' + draft['id'], json={
        'base_revision_id': draft['revision_id'],
        'data': {**draft['data'], 'description': 'Changed'},
    })
    assert edited.status_code == 200
    changed = store.get(delivery['id'])
    assert changed['data']['status'] == 'rejected'
    assert changed['data']['reason'] == 'draft_changed'


@pytest.mark.skipif(APP_NAME != 'Merlin', reason='Only Merlin owns draft delivery state')
@pytest.mark.parametrize('status', ['sending', 'uncertain', 'reconciling', 'delivered'])
def test_terminal_or_uncertain_delivery_blocks_draft_edit(client, store, status):
    store = client.app.state.store
    with store.tx() as connection:
        draft = store.put(connection, 'draft', {
            'title': 'Synthetic draft', 'description': 'First', 'impact': '', 'remediation': '',
            'references': '', 'evidence_ids': [], 'owner_id': '', 'lead_id': '',
        }, 'fixture')
        delivery = store.put(connection, 'delivery', {
            'status': status, 'draft_id': draft['id'], 'revision_id': draft['revision_id'],
            'report_id': '7', 'payload_hash': 'fixture', 'payload': {}, 'config_hash': 'fixture',
            'approved_by': 'fixture',
        }, 'fixture')
        store.event(connection, 'fixture', 'delivery.fixture_created', [draft['id'], delivery['id']])
    edited = client.put('/api/records/' + draft['id'], json={
        'base_revision_id': draft['revision_id'],
        'data': {**draft['data'], 'description': 'Changed'},
    })
    assert edited.status_code == 409
    assert store.get(draft['id'])['revision_id'] == draft['revision_id']
