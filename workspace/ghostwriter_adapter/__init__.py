"""Reviewed report-specific Ghostwriter proposals with uncertain-send handling."""
import hashlib
import hmac
import json
import uuid
import httpx
from fastapi import HTTPException, Request
from markdown_it import MarkdownIt
from .. import APP_NAME
from ..models import DeliveryAction, DeliveryReconcile, DeliverySelection, Review
from ..store import digest, private
from ..origins import exact_origin

ADAPTER = 'ghostwriter-reportedFinding-v7.2.6-3'
FIELDS = {'title', 'description', 'impact', 'mitigation', 'references', 'reportId', 'severityId', 'findingTypeId', 'position', 'extraFields', 'complete'}
PROBE = '''query MerlinSchema { __type(name:"reportedFinding_insert_input") { inputFields { name } } __schema { mutationType { fields { name } } } }'''
CREATE = '''mutation MerlinCreate($object: reportedFinding_insert_input!) { insert_reportedFinding_one(object:$object) { id reportId title extraFields } }'''
LOOKUP = '''query MerlinReceipt($id:bigint!) { reportedFinding_by_pk(id:$id) { id reportId title extraFields } }'''


def html(text):
    md = MarkdownIt('commonmark', {'html': False, 'linkify': False}).enable('table')
    md.add_render_rule('image', lambda renderer, tokens, idx, options, env: '[Attach reviewed evidence in Ghostwriter]')
    return md.render(text)


def prepare_graphql(store, query, variables, operation, *, config=None, mode=None):
    config = config or store.setting('ghostwriter', {})
    if not config:
        raise RuntimeError('Configure an approved Ghostwriter test report first.')
    origin = config['origin']
    try:
        origin, _ = exact_origin(origin, mode or config.get('mode') or store.setting('config', {}).get('mode'), 'ghostwriter')
    except ValueError as error:
        raise RuntimeError('Ghostwriter requires an exact HTTPS origin, except for its synthetic local container.') from error
    token_path = store.root / 'keys' / 'ghostwriter.token'
    try:
        private(token_path)
        token = token_path.read_text().strip()
    except OSError as error:
        raise RuntimeError('The scoped Ghostwriter token is unavailable.') from error
    if not token:
        raise RuntimeError('The scoped Ghostwriter token is empty.')
    client = None
    try:
        client = httpx.Client(trust_env=False, follow_redirects=False, timeout=30)
        request = client.build_request('POST', origin.rstrip('/') + '/v1/graphql', json={'operationName': operation, 'query': query, 'variables': variables}, headers={'Authorization': 'Bearer ' + token})
        return client, request
    except Exception:
        if client is not None:
            client.close()
        raise


def execute_graphql(prepared):
    client, request = prepared
    response = None
    try:
        response = client.send(request, stream=True)
        try:
            if response.status_code != 200:
                raise ValueError('Ghostwriter rejected the request')
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 1024 * 1024:
                    raise ValueError('Ghostwriter response exceeded its limit')
        finally:
            response.close()
    finally:
        client.close()
    data = json.loads(raw)
    if data.get('errors') or not isinstance(data.get('data'), dict):
        raise ValueError('Ghostwriter did not confirm the operation')
    return data['data']


def request_graphql(store, query, variables, operation, *, config=None, mode=None):
    return execute_graphql(prepare_graphql(store, query, variables, operation, config=config, mode=mode))


def verify_adapter(store):
    result = request_graphql(store, PROBE, {}, 'MerlinSchema')
    fields = {f['name'] for f in (result.get('__type') or {}).get('inputFields', [])}
    mutations = {f['name'] for f in result.get('__schema', {}).get('mutationType', {}).get('fields', [])}
    if not FIELDS <= fields or 'insert_reportedFinding_one' not in mutations:
        raise RuntimeError('This Ghostwriter schema is not supported by the installed adapter.')
    return digest({'adapter': ADAPTER, 'fields': sorted(fields), 'mutation': 'insert_reportedFinding_one'})


def verify_evidence(path, size, sha256):
    try:
        private(path)
        if not path.is_file():
            raise RuntimeError('Evidence is not a regular file')
        total = 0
        checksum = hashlib.sha256()
        with path.open('rb') as source:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > size:
                    return False
                checksum.update(chunk)
        return total == size and checksum.hexdigest() == sha256
    except OSError as error:
        raise RuntimeError('A selected evidence artifact is unavailable.') from error


def verify_delivery_evidence(store, delivery, connection=None):
    evidence_ids = delivery.get('evidence_ids', [])
    manifest = delivery.get('payload', {}).get('extraFields', {}).get('merlin_evidence_manifest', [])
    if digest(manifest) != delivery.get('evidence_manifest_hash') or [item.get('id') for item in manifest] != evidence_ids:
        raise RuntimeError('The reviewed evidence manifest changed. Review a new proposal.')
    expected_state = 'manual_required' if evidence_ids else 'none'
    if delivery.get('attachment_state') != expected_state:
        raise RuntimeError('The reviewed attachment state changed. Review a new proposal.')
    for expected in manifest:
        if set(expected) != {'id', 'filename', 'sha256', 'size'}:
            raise RuntimeError('The reviewed evidence manifest is invalid.')
        item = store.get(expected['id'], connection)
        if not item or item['kind'] != 'upload':
            raise RuntimeError('A reviewed evidence record is unavailable.')
        metadata = item['data']
        current = {key: metadata.get(key) for key in ('filename', 'sha256', 'size')}
        if metadata.get('quarantined') or not metadata.get('reviewed_for_export') or current != {key: expected[key] for key in current}:
            raise RuntimeError('A reviewed evidence record changed. Review a new proposal.')
        if not verify_evidence(store.root / 'artifacts' / metadata.get('artifact_id', ''), metadata.get('size'), metadata.get('sha256')):
            raise RuntimeError('A reviewed evidence artifact changed. Review a new proposal.')


def confirmed_remote(result, delivery_id, delivery):
    payload = delivery['payload']
    return bool(
        isinstance(result, dict)
        and result.get('id') is not None
        and str(result.get('reportId')) == delivery['report_id']
        and result.get('title') == payload['title']
        and result.get('extraFields') == payload['extraFields']
        and payload['extraFields'].get('merlin_proposal_id') == delivery_id
        and payload['extraFields'].get('merlin_revision_id') == delivery['revision_id']
    )


def setting(connection, key, default=None):
    row = connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def destination(config, application_config, report_id):
    if str(config.get('report_id')) != report_id:
        raise HTTPException(422, 'Select the report approved on this host.')
    if not config.get('schema_hash') or config.get('adapter_id') != ADAPTER:
        raise RuntimeError('Verify the current Ghostwriter adapter before review.')
    try:
        origin, _ = exact_origin(config['origin'], application_config.get('mode'), 'ghostwriter')
        return {
            'origin': origin.rstrip('/'),
            'report_id': str(config['report_id']),
            'severity_id': int(config['severity_id']),
            'finding_type_id': int(config['finding_type_id']),
            'schema_hash': str(config['schema_hash']),
            'adapter_id': str(config['adapter_id']),
            'mode': str(application_config['mode']),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError('The reviewed Ghostwriter connection is incomplete or invalid.') from error


def payload(store, draft, proposal_id, target, connection):
    d = draft['data']
    evidence = []
    for evidence_id in d.get('evidence_ids', []):
        item = store.get(evidence_id, connection)
        if not item or item['kind'] != 'upload':
            raise RuntimeError('A selected evidence record is unavailable.')
        metadata = item['data']
        if metadata.get('quarantined') or not metadata.get('reviewed_for_export'):
            raise RuntimeError('A host reviewer must approve each selected evidence item before delivery review.')
        path = store.root / 'artifacts' / metadata.get('artifact_id', '')
        if not verify_evidence(path, metadata.get('size'), metadata.get('sha256')):
            raise RuntimeError('A selected evidence artifact changed. Review it again before delivery.')
        evidence.append({'id': evidence_id, 'filename': metadata['filename'], 'sha256': metadata['sha256'], 'size': metadata['size']})
    extra_fields = {'merlin_proposal_id': proposal_id, 'merlin_revision_id': draft['revision_id']}
    if evidence:
        extra_fields['merlin_evidence_manifest'] = evidence
        extra_fields['merlin_evidence_attachment'] = 'manual_required'
    return dict(title=d['title'], description=html(d['description']), impact=html(d['impact']), mitigation=html(d['remediation']), references=html(d['references']), reportId=int(target['report_id']), severityId=target['severity_id'], findingTypeId=target['finding_type_id'], position=0, complete=False, extraFields=extra_fields)


def build_proposal(store, connection, selection, proposal_id):
    config = setting(connection, 'ghostwriter', {})
    application_config = setting(connection, 'config', {})
    target = destination(config, application_config, selection.report_id)
    draft = store.get(selection.draft_id, connection)
    if not draft or draft['kind'] != 'draft':
        raise HTTPException(404, 'The draft was not found.')
    if draft['revision_id'] != selection.draft_revision_id:
        raise HTTPException(409, 'The displayed draft revision changed. Preview the current saved draft before approval.')
    outgoing = payload(store, draft, proposal_id, target, connection)
    evidence_manifest = outgoing['extraFields'].get('merlin_evidence_manifest', [])
    attachment_state = 'manual_required' if evidence_manifest else 'none'
    descriptor = {
        'schema_version': 1,
        'adapter_id': ADAPTER,
        'proposal_id': proposal_id,
        'draft_id': draft['id'],
        'draft_revision_id': draft['revision_id'],
        'destination': target,
        'payload': outgoing,
        'attachment_state': attachment_state,
    }
    return {
        'proposal_id': proposal_id,
        'proposal_hash': digest(descriptor),
        'adapter_id': ADAPTER,
        'draft_id': draft['id'],
        'draft_revision_id': draft['revision_id'],
        'destination_origin': target['origin'],
        'report_id': target['report_id'],
        'payload_hash': digest(outgoing),
        'payload': outgoing,
        'evidence_manifest': evidence_manifest,
        'evidence_manifest_hash': digest(evidence_manifest),
        'attachment_state': attachment_state,
        'destination': target,
    }


def routes(app, store, actor, admin, record):
    def merlin(request):
        if APP_NAME != 'Merlin':
            raise HTTPException(403, 'Ghostwriter delivery belongs to Merlin.')
        admin(request)

    @app.get('/api/deliveries')
    def deliveries():
        values = store.records('delivery')
        # Do not send stored outgoing payloads to status-only clients.
        return {'items': [{**r, 'data': {k: v for k, v in r['data'].items() if k != 'payload'}} for r in values], 'total': len(values)}

    @app.get('/api/deliveries/{id}')
    def delivery(id: str):
        r = record(id, 'delivery')
        return {**r, 'data': {k: v for k, v in r['data'].items() if k != 'payload'}}

    @app.post('/api/deliveries/preview')
    def preview(body: DeliverySelection, request: Request):
        merlin(request)
        proposal_id = str(uuid.uuid4())
        with store.tx() as c:
            proposal = build_proposal(store, c, body, proposal_id)
        return proposal

    @app.post('/api/deliveries/review')
    def review(body: Review, request: Request):
        merlin(request)
        delivery_id = body.proposal_id
        exact_retry = False
        with store.tx() as c:
            proposal = build_proposal(store, c, body, delivery_id)
            if not hmac.compare_digest(proposal['proposal_hash'], body.proposal_hash):
                raise HTTPException(409, 'The delivery preview changed. Preview the current draft and destination again.')
            existing = store.get(delivery_id, c)
            if existing:
                data = existing['data']
                exact_retry = bool(
                    existing['kind'] == 'delivery'
                    and data.get('proposal_hash') == body.proposal_hash
                    and data.get('payload_hash') == proposal['payload_hash']
                    and data.get('draft_id') == proposal['draft_id']
                    and data.get('revision_id') == proposal['draft_revision_id']
                    and data.get('report_id') == proposal['report_id']
                )
                if not exact_retry:
                    raise HTTPException(409, 'The proposal identifier is already bound to different content.')
            for prior in c.execute("SELECT id,data FROM records WHERE kind='delivery'"):
                if prior['id'] == delivery_id:
                    continue
                d = json.loads(prior['data'])
                if d.get('draft_id') != proposal['draft_id']:
                    continue
                if d.get('status') in ('reviewed', 'sending', 'uncertain', 'reconciling'):
                    raise HTTPException(409, 'This draft has an active or uncertain delivery. Resolve it before another review.')
                if d.get('status') == 'delivered':
                    raise HTTPException(409, 'This draft was delivered. Make later wording changes in Ghostwriter or create a separately reviewed finding.')
            if not exact_retry:
                evidence_ids = [item['id'] for item in proposal['evidence_manifest']]
                store.put(c, 'delivery', dict(
                    status='reviewed',
                    draft_id=proposal['draft_id'],
                    revision_id=proposal['draft_revision_id'],
                    report_id=proposal['report_id'],
                    proposal_hash=proposal['proposal_hash'],
                    payload_hash=proposal['payload_hash'],
                    payload=proposal['payload'],
                    destination=proposal['destination'],
                    config_hash=digest(proposal['destination']),
                    approved_by=actor(request),
                    evidence_ids=evidence_ids,
                    evidence_manifest_hash=proposal['evidence_manifest_hash'],
                    attachment_state=proposal['attachment_state'],
                ), actor(request), delivery_id)
                store.event(c, actor(request), 'delivery.reviewed', [delivery_id, proposal['draft_id']], proposal_hash=proposal['proposal_hash'], payload_hash=proposal['payload_hash'], report_id=proposal['report_id'])
        return delivery(delivery_id)

    @app.post('/api/deliveries/{id}/send')
    def send(id: str, body: DeliveryAction, request: Request):
        merlin(request)
        prepared = None
        try:
            with store.tx() as c:
                r = store.get(id, c)
                if not r or r['kind'] != 'delivery' or r['data']['status'] != 'reviewed':
                    raise HTTPException(409, 'Only a reviewed, unsent proposal can be sent.')
                d = r['data']
                if r['revision_id'] != body.delivery_revision_id or not hmac.compare_digest(str(d.get('payload_hash', '')), body.payload_hash):
                    raise HTTPException(409, 'The displayed delivery revision changed. Reload it before sending.')
                current = store.get(d['draft_id'], c)
                current_target = destination(setting(c, 'ghostwriter', {}), setting(c, 'config', {}), d['report_id'])
                if (
                    not current
                    or current['revision_id'] != d['revision_id']
                    or d.get('destination') != current_target
                    or d.get('config_hash') != digest(current_target)
                    or digest(d.get('payload')) != d['payload_hash']
                ):
                    raise HTTPException(409, 'The draft or connection changed. Review a new proposal.')
                verify_delivery_evidence(store, d, c)
                dispatch_target = dict(current_target)
                prepared = prepare_graphql(store, CREATE, {'object': d['payload']}, 'MerlinCreate', config=dispatch_target, mode=dispatch_target['mode'])
                r = store.put(c, 'delivery', {**d, 'status': 'sending'}, actor(request), id, r['revision_id'])
                store.event(c, actor(request), 'delivery.dispatch', [id], delivery_revision_id=body.delivery_revision_id, payload_hash=d['payload_hash'])
        except Exception:
            if prepared is not None:
                prepared[0].close()
            raise
        state, remote_id = 'uncertain', None
        try:
            result = execute_graphql(prepared).get('insert_reportedFinding_one')
            if confirmed_remote(result, id, d):
                remote_id, state = result['id'], 'delivered'
        except Exception:
            # An error after dispatch does not prove the server performed no mutation.
            state = 'uncertain'
        with store.tx() as c:
            store.put(c, 'delivery', {**r['data'], 'status': state, 'remote_id': remote_id}, actor(request), id, r['revision_id'])
            store.event(c, actor(request), 'delivery.result', [id], outcome=state, remote_id=remote_id)
        return delivery(id)

    @app.post('/api/deliveries/{id}/reconcile')
    def reconcile(id: str, body: DeliveryReconcile, request: Request):
        merlin(request)
        with store.tx() as c:
            r = store.get(id, c)
            if not r or r['kind'] != 'delivery' or r['data']['status'] != 'uncertain':
                raise HTTPException(409, 'Only uncertain delivery requires reconciliation.')
            d = r['data']
            if r['revision_id'] != body.delivery_revision_id or not hmac.compare_digest(str(d.get('payload_hash', '')), body.payload_hash):
                raise HTTPException(409, 'The displayed delivery revision changed. Reload it before reconciliation.')
            target = d.get('destination')
            if not isinstance(target, dict) or d.get('config_hash') != digest(target) or digest(d.get('payload')) != d.get('payload_hash'):
                raise HTTPException(409, 'The reviewed delivery record changed. Preserve it and inspect the audit log.')
            pending = store.put(c, 'delivery', {**d, 'status': 'reconciling'}, actor(request), id, r['revision_id'])
            store.event(c, actor(request), 'delivery.reconcile_dispatch', [id], delivery_revision_id=body.delivery_revision_id, payload_hash=d['payload_hash'], remote_id=body.remote_id)
        result = None
        lookup_failed = False
        try:
            result = request_graphql(store, LOOKUP, {'id': body.remote_id}, 'MerlinReceipt', config=target, mode=target['mode']).get('reportedFinding_by_pk')
        except Exception:
            lookup_failed = True
        confirmed = not lookup_failed and confirmed_remote(result, id, d)
        with store.tx() as c:
            current = store.get(id, c)
            if not current or current['revision_id'] != pending['revision_id'] or current['data'].get('status') != 'reconciling':
                raise HTTPException(409, 'The delivery changed during reconciliation. Reload its current state.')
            state = 'delivered' if confirmed else 'uncertain'
            store.put(c, 'delivery', {**current['data'], 'status': state, 'remote_id': body.remote_id if confirmed else d.get('remote_id')}, actor(request), id, current['revision_id'])
            store.event(c, actor(request), 'delivery.reconcile_result', [id], outcome=state if confirmed else 'not_confirmed', remote_id=body.remote_id)
        if lookup_failed:
            raise HTTPException(503, 'Ghostwriter did not confirm the lookup. The proposal remains uncertain.')
        if not confirmed:
            raise HTTPException(409, 'This remote finding does not prove receipt. The proposal remains uncertain.')
        return delivery(id)
