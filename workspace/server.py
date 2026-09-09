"""Human-operated workspace API. No route starts an assessment tool."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import tempfile
import uuid
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from . import APP_NAME
from . import auth, models
from .store import Store, Conflict, canonical, digest, private
from .parsers import FORMATS, safe_text, secret_bearing
from .isolation import run_parser


def create_app(root):
    store = Store(root)
    # Multipart spools must stay on the same approved storage as raw artifacts.
    tempfile.tempdir = str(store.root / 'staging')
    config = store.setting('config')
    if not config or config.get('app') != APP_NAME:
        raise RuntimeError('Initialize this application workspace first.')
    if config['mode'] == 'client':
        from .cli import encrypted_storage
        if not encrypted_storage(store.root):
            raise RuntimeError('Client storage is not on a verified encrypted block device.')
    origin = config['origin'].rstrip('/')
    cookie = APP_NAME.lower() + '_session'
    file_slot = asyncio.Semaphore(1)
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    # Keep interrupted jobs visible; never repeat a parser or delivery automatically.
    for kind in ('upload', 'delivery'):
        for r in store.records(kind):
            interrupted = r['data'].get('status') == 'parsing' if kind == 'upload' else r['data'].get('status') in ('sending', 'reconciling')
            if interrupted:
                with store.tx() as c:
                    data = {**r['data'], 'status': 'needs_review' if kind == 'upload' else 'uncertain'}
                    store.put(c, kind, data, 'recovery', r['id'], r['revision_id'])
                    store.event(c, 'recovery', 'operation.interrupted', [r['id']])

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={'detail': {'message': 'Another user saved a revision. Compare both versions.', 'current': exc.current}})

    @app.exception_handler(RuntimeError)
    async def runtime_handler(request, exc):
        return JSONResponse(status_code=503, content={'detail': str(exc)})

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse(status_code=422, content={'detail': 'The input did not match the supported format. Review the fields and limits.'})

    @app.middleware('http')
    async def boundary(request, call_next):
        response = None
        expected = urlsplit(origin)
        allowed_hosts = {expected.netloc}
        if request.url.path == '/peer/inbox':
            allowed_hosts.add(urlsplit(config.get('peer_origin', origin)).netloc)
        if request.headers.get('host') not in allowed_hosts:
            return JSONResponse(status_code=400, content={'detail': 'Use the configured application address.'})
        if config['mode'] == 'client' and request.url.scheme != 'https':
            return JSONResponse(status_code=403, content={'detail': 'HTTPS is required.'})
        if request.url.path == '/peer/inbox' and request.method == 'POST':
            try:
                from .transfer import authorize_peer_headers
                request.state.peer_authorization = authorize_peer_headers(store, request.headers)
            except ValueError:
                return JSONResponse(status_code=401, content={'detail': 'Peer request authentication failed.'})
            except RuntimeError:
                return JSONResponse(status_code=503, content={'detail': 'Peer request auditing is unavailable.'})
        request.state.session = auth.session(store, request.cookies.get(cookie))
        if request.url.path.startswith('/api/'):
            if request.url.path not in ('/api/session', '/api/login') and not request.state.session:
                response = JSONResponse(status_code=401, content={'detail': 'Sign in to continue.'})
            if request.method not in ('GET', 'HEAD'):
                if request.headers.get('origin') != origin:
                    response = JSONResponse(status_code=403, content={'detail': 'The request origin is not allowed.'})
                elif request.url.path != '/api/login' and (not request.state.session or not __import__('hmac').compare_digest(request.headers.get('x-csrf-token', ''), request.state.session['csrf'])):
                    response = JSONResponse(status_code=403, content={'detail': 'Reload the page before you save.'})
                size = request.headers.get('content-length')
                maximum = 256 * 1024**2 + 65536 if request.url.path in ('/api/uploads', '/api/transfers/import') else 512 * 1024
                if size and (not size.isdigit() or int(size) > maximum):
                    response = JSONResponse(status_code=413, content={'detail': 'The request exceeds its size limit.'})
                if request.headers.get('content-type') and size is None:
                    response = JSONResponse(status_code=411, content={'detail': 'Send the file with a declared content length.'})
        if response is None:
            if request.url.path in ('/api/uploads', '/api/transfers/import', '/peer/inbox') and request.method == 'POST':
                if file_slot.locked():
                    return JSONResponse(status_code=429, content={'detail': 'Another file transfer is in progress. Try again when it finishes.'})
                async with file_slot:
                    try:
                        async with asyncio.timeout(180):
                            response = await call_next(request)
                    except TimeoutError:
                        return JSONResponse(status_code=408, content={'detail': 'File transfer exceeded its deadline.'})
            else:
                response = await call_next(request)
        response.headers.update({'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'", 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()'})
        return response

    def actor(request):
        return request.state.session['user_id']

    def admin(request):
        if request.state.session['role'] not in ('captain', 'lead_scribe'):
            raise HTTPException(403, 'This action requires the team host role.')

    def record(id, kind=None):
        value = store.get(id)
        if not value or kind and value['kind'] != kind:
            raise HTTPException(404, 'The record was not found.')
        return value

    def evidence_artifact(value):
        data = value['data']
        try:
            path = store.root / 'artifacts' / data['artifact_id']
            private(path)
            if not path.is_file() or path.stat().st_size != data['size']:
                raise ValueError('Evidence size changed')
            checksum = hashlib.sha256()
            with path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    checksum.update(chunk)
            if checksum.hexdigest() != data['sha256']:
                raise ValueError('Evidence checksum changed')
            return path
        except Exception as error:
            store.blocked = 'Evidence integrity failed. Preserve the workspace and inspect the original artifact.'
            raise RuntimeError(store.blocked) from error

    def exposed_data(kind, data):
        # The exact reviewed Ghostwriter body is retained for integrity checks and
        # dispatch. Browser status views receive only its hash and state metadata.
        if APP_NAME == 'Merlin' and kind == 'delivery':
            return {key: value for key, value in data.items() if key != 'payload'}
        return data

    def exposed_record(value):
        return {**value, 'data': exposed_data(value['kind'], value['data'])}

    def edit_allowed(request, value):
        role = request.state.session['role']
        if value['kind'] not in ('finding', 'draft') or value['data'].get('source_instance'):
            raise HTTPException(403, 'This technical record is read-only.')
        if APP_NAME == 'Harbinger' and value['kind'] != 'finding' or APP_NAME == 'Merlin' and value['kind'] != 'draft':
            raise HTTPException(403, 'This application does not own this record.')
        if role not in ('captain', 'lead_scribe') and value['data'].get('owner_id') not in ('', actor(request)):
            raise HTTPException(403, 'Ask the current owner to transfer this assignment.')

    def validate_data(kind, data):
        cls = models.Finding if kind == 'finding' else models.Draft
        try:
            value = cls.model_validate(data).model_dump()
        except ValidationError:
            raise HTTPException(422, 'Review the required fields and their limits.')
        for field, expected_kind in (('asset_ids', 'asset'), ('evidence_ids', 'upload')):
            for id in value.get(field, []):
                evidence = record(id, expected_kind)
                if field == 'evidence_ids' and evidence['data'].get('quarantined'):
                    raise HTTPException(403, 'Quarantined evidence cannot be attached.')
        if value.get('owner_id'):
            with store.connect() as c:
                if not c.execute('SELECT 1 FROM users WHERE id=?', (value['owner_id'],)).fetchone():
                    raise HTTPException(422, 'Choose an existing owner.')
        return value

    def session_response(s):
        return {'user': {'id': s['user_id'], 'name': s['name'], 'role': s['role']} if s else None,
                'csrf': s['csrf'] if s else None, 'app': APP_NAME, 'engagement': config['engagement'] if s else {'id': '', 'name': 'Sign in'}, 'mode': config['mode']}

    @app.get('/api/session')
    def get_session(request: Request):
        return session_response(request.state.session)

    @app.post('/api/login')
    def sign_in(body: models.Login, request: Request):
        token, result = auth.login(store, body.name, body.password, request.client.host)
        if token is None:
            raise HTTPException(429 if result == 'throttled' else 401, 'Wait five minutes before another attempt.' if result == 'throttled' else 'The name or password did not match.')
        response = JSONResponse(session_response(auth.session(store, token)))
        response.set_cookie(cookie, token, secure=origin.startswith('https:'), httponly=True, samesite='strict', max_age=28800)
        return response

    @app.post('/api/logout')
    def sign_out(request: Request):
        with store.tx() as c:
            c.execute('DELETE FROM sessions WHERE token=?', (request.state.session['token'],))
            store.event(c, actor(request), 'logout', [])
        response = JSONResponse({'status': 'signed_out'})
        response.delete_cookie(cookie)
        return response

    @app.get('/api/users')
    def users():
        with store.connect() as c:
            return {'items': [dict(r) for r in c.execute('SELECT id,name,role FROM users ORDER BY name')]}

    @app.get('/api/records')
    def records(request: Request, kind: str = None, limit: int = 500, offset: int = 0):
        if limit < 1 or limit > 1000 or offset < 0:
            raise HTTPException(422, 'Invalid page limits.')
        restricted = request.state.session['role'] not in ('captain', 'lead_scribe')
        if restricted and kind == 'transfer_conflict':
            raise HTTPException(403, 'Transfer conflict details require the team host role.')
        with store.connect() as c:
            predicate = '(? IS NULL OR kind=?) AND (?=0 OR kind!=\'transfer_conflict\')'
            params = (kind, kind, int(restricted))
            total = c.execute('SELECT COUNT(*) FROM records WHERE ' + predicate, params).fetchone()[0]
            rows = c.execute('SELECT * FROM records WHERE ' + predicate + ' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', (*params, limit, offset))
            return {'items': [exposed_record(store.decode(r)) for r in rows], 'total': total}

    @app.get('/api/records/{id}')
    def get_record(id: str, request: Request):
        value = record(id)
        if value['kind'] == 'transfer_conflict':
            admin(request)
        return exposed_record(value)

    @app.get('/api/records/{id}/revisions')
    def revisions(id: str, request: Request):
        r = record(id)
        if r['kind'] == 'transfer_conflict':
            admin(request)
        with store.connect() as c:
            return {'items': [dict(id=id, kind=r['kind'], revision_id=row['id'], data=exposed_data(r['kind'], json.loads(row['data'])), updated_at=row['created_at'], actor=row['actor']) for row in c.execute('SELECT * FROM revisions WHERE record_id=? ORDER BY created_at DESC', (id,))]}

    @app.post('/api/records')
    def new_record(body: models.NewRecord, request: Request):
        allowed = 'finding' if APP_NAME == 'Harbinger' else 'draft'
        if body.kind != allowed:
            raise HTTPException(403, 'Use the dedicated operation for this record type.')
        data = validate_data(body.kind, body.data)
        data['owner_id'] = data.get('owner_id') or actor(request)
        if data['owner_id'] != actor(request):
            admin(request)
        if APP_NAME == 'Harbinger' and data.get('evidence_state') == 'confirmed':
            admin(request)
        if APP_NAME == 'Harbinger' and data.get('writing_state') == 'submitted':
            raise HTTPException(403, 'Only the dedicated submission operation can mark a finding submitted.')
        if APP_NAME == 'Merlin' and data.get('lead_id'):
            raise HTTPException(403, 'Create lead-backed drafts from the received lead.')
        with store.tx() as c:
            r = store.put(c, body.kind, data, actor(request))
            store.event(c, actor(request), 'record.created', [r['id']], revision_id=r['revision_id'], content_hash=digest(data))
        return r

    @app.put('/api/records/{id}')
    def edit_record(id: str, body: models.EditRecord, request: Request):
        current = record(id)
        edit_allowed(request, current)
        data = validate_data(current['kind'], body.data)
        if data.get('owner_id') != current['data'].get('owner_id'):
            admin(request)
        if APP_NAME == 'Harbinger':
            if current['data'].get('evidence_state') == 'confirmed' or data.get('evidence_state') == 'confirmed':
                admin(request)
            if data.get('writing_state') == 'submitted':
                if current['data'].get('writing_state') != 'submitted':
                    raise HTTPException(403, 'Only the dedicated submission operation can mark a finding submitted.')
                data['writing_state'] = 'ready'
        if APP_NAME == 'Merlin' and data.get('lead_id') != current['data'].get('lead_id'):
            raise HTTPException(403, 'The technical lead reference is immutable.')
        with store.tx() as c:
            locked = store.get(id, c)
            if locked['revision_id'] != body.base_revision_id:
                raise Conflict(locked)
            invalidated = []
            if APP_NAME == 'Merlin' and current['kind'] == 'draft':
                related = [store.decode(row) for row in c.execute("SELECT * FROM records WHERE kind='delivery'") if json.loads(row['data']).get('draft_id') == id]
                blocked = [item for item in related if item['data'].get('status') in ('sending', 'uncertain', 'reconciling', 'delivered')]
                if blocked:
                    raise HTTPException(409, 'Resolve the active, uncertain, or delivered Ghostwriter record before editing this draft.')
                for item in related:
                    if item['data'].get('status') == 'reviewed':
                        changed = store.put(c, 'delivery', {**item['data'], 'status': 'rejected', 'reason': 'draft_changed'}, actor(request), item['id'], item['revision_id'])
                        invalidated.append(changed['id'])
            r = store.put(c, current['kind'], data, actor(request), id, locked['revision_id'])
            if invalidated:
                store.event(c, actor(request), 'delivery.invalidated', invalidated + [id], reason='draft_changed')
            store.event(c, actor(request), 'record.updated', [id], revision_id=r['revision_id'], content_hash=digest(data))
        return r

    @app.get('/api/assets')
    def assets(q: str = '', track: str = '', offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 500 or len(q) > 200:
            raise HTTPException(422, 'Invalid search limits.')
        predicate = "kind='asset' AND (?='' OR json_extract(data,'$.track')=?) AND (?='' OR json_extract(data,'$.label') LIKE ? OR id=?)"
        params = (track, track, q, '%' + q + '%', q)
        with store.connect() as c:
            total = c.execute('SELECT COUNT(*) FROM records WHERE ' + predicate, params).fetchone()[0]
            rows = c.execute('SELECT * FROM records WHERE ' + predicate + " ORDER BY json_extract(data,'$.label'),id LIMIT ? OFFSET ?", (*params, limit, offset))
            items = []
            for row in rows:
                r = store.decode(row)
                items.append({**r, 'label': r['data']['label'], 'kind': r['data']['kind'], 'track': r['data']['track']})
            return {'items': items, 'total': total}

    @app.get('/api/assets/{id}')
    def asset(id: str):
        r = record(id, 'asset')
        return {**r, 'label': r['data']['label'], 'kind': r['data']['kind'], 'track': r['data']['track']}

    @app.get('/api/graph')
    def graph(q: str = '', track: str = ''):
        page = assets(q, track, limit=500)
        nodes = [{k: r[k] for k in ('id', 'label', 'kind', 'track')} for r in page['items']]
        ids = {n['id'] for n in nodes}
        edges, total = [], 0
        with store.connect() as c:
            for row in c.execute("SELECT id,data FROM records WHERE kind='relationship'"):
                data = json.loads(row['data'])
                if data['source'] in ids and data['target'] in ids:
                    total += 1
                    if len(edges) < 1000:
                        edges.append({'id': row['id'], **data})
        return {'nodes': nodes, 'edges': edges, 'total_nodes': page['total'], 'total_edges': total}

    @app.get('/api/uploads')
    @app.get('/api/evidence')
    def uploads(request: Request, limit: int = 100, offset: int = 0):
        page = records(request, 'upload', limit, offset)
        evidence_to_findings = {}
        with store.connect() as c:
            for row in c.execute("SELECT id,kind,data FROM records WHERE kind IN ('finding','lead')"):
                data = json.loads(row['data'])
                finding_id = row['id'] if row['kind'] == 'finding' else data.get('finding_id')
                if not finding_id:
                    continue
                for evidence_id in data.get('evidence_ids', []):
                    evidence_to_findings.setdefault(evidence_id, set()).add(finding_id)
        page['items'] = [
            {**item, 'data': {**item['data'], 'finding_ids': sorted(evidence_to_findings.get(item['id'], set()))}}
            for item in page['items']
        ]
        return page

    @app.post('/api/uploads')
    async def upload(request: Request, file: UploadFile = File(...), format: str = Form(...)):
        if APP_NAME != 'Harbinger' or format not in FORMATS:
            raise HTTPException(422, 'Choose an admitted Harbinger import format.')
        id = str(uuid.uuid4())
        path = store.root / 'artifacts' / id
        h, size = hashlib.sha256(), 0
        with store.tx() as c:
            store.event(c, actor(request), 'upload.started', [id], parser=format)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as target:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 256 * 1024**2 or shutil.disk_usage(store.root).free < 256 * 1024**2:
                        raise HTTPException(413, 'The upload or available storage reached its limit.')
                    target.write(chunk); h.update(chunk)
                target.flush(); os.fsync(target.fileno())
            key = f'{format}:{h.hexdigest()}'
            with store.tx() as c:
                previous = c.execute('SELECT record_id FROM imports WHERE key=?', (key,)).fetchone()
                if previous:
                    r = record(previous[0])
                    store.event(c, actor(request), 'upload.duplicate', [r['id']])
                else:
                    data = dict(filename=Path(file.filename or 'upload').name[:200], format=format, status='uploaded', sha256=h.hexdigest(), size=size, artifact_id=id, quarantined=False, limitations=[])
                    r = store.put(c, 'upload', data, actor(request), id)
                    c.execute('INSERT INTO imports VALUES(?,?)', (key, id))
                    store.event(c, actor(request), 'upload.received', [id], size=size, sha256=h.hexdigest())
            if previous:
                path.unlink()
            return r
        finally:
            await file.close()

    @app.post('/api/uploads/{id}/parse')
    async def parse_upload(id: str, request: Request):
        r = record(id, 'upload')
        if r['data']['status'] in ('merged', 'parsing'):
            raise HTTPException(409, 'This upload is already merged or being parsed.')
        with store.tx() as c:
            # Re-read under the writer lock to prevent two parser dispatches.
            current = store.get(id, c)
            if current['revision_id'] != r['revision_id']:
                raise Conflict(current)
            r = store.put(c, 'upload', {**r['data'], 'status': 'parsing'}, actor(request), id, r['revision_id'])
            store.event(c, actor(request), 'parser.started', [id], parser=r['data']['format'])
        try:
            path = store.root / 'artifacts' / r['data']['artifact_id']
            private(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != r['data']['sha256']:
                raise RuntimeError('The source checksum changed. Preserve the workspace for review.')
            preview = await asyncio.to_thread(run_parser, path, r['data']['format'])
            data = {**r['data'], 'status': 'quarantined' if preview['quarantined'] else 'preview', 'quarantined': preview['quarantined'], 'limitations': preview['limitations']}
            with store.tx() as c:
                store.put(c, 'preview', preview, actor(request), id + '-preview', (store.get(id + '-preview', c) or {}).get('revision_id'))
                r = store.put(c, 'upload', data, actor(request), id, r['revision_id'])
                store.event(c, actor(request), 'parser.completed', [id], outcome=data['status'], complete=preview['complete'])
            return r
        except Exception:
            with store.tx() as c:
                current = store.get(id, c)
                if current['revision_id'] == r['revision_id'] and current['data'].get('status') == 'parsing':
                    store.put(c, 'upload', {**current['data'], 'status': 'failed', 'limitations': ['Parsing failed. The original is retained. No records were merged.']}, actor(request), id, current['revision_id'])
                    store.event(c, actor(request), 'parser.failed', [id], outcome='incomplete')
            raise

    @app.get('/api/uploads/{id}/preview')
    def preview(id: str):
        record(id, 'upload')
        return record(id + '-preview', 'preview')['data']

    @app.post('/api/uploads/{id}/merge')
    def merge(id: str, request: Request):
        r = record(id, 'upload')
        if r['data']['status'] == 'merged':
            return r
        if r['data']['status'] != 'preview' or r['data'].get('quarantined'):
            raise HTTPException(409, 'Review a successful, non-quarantined preview first.')
        evidence_artifact(r)
        p = preview(id)
        with store.tx() as c:
            current = store.get(id, c)
            if current['revision_id'] != r['revision_id']:
                raise Conflict(current)
            mapping = {a['id']: str(uuid.uuid5(uuid.UUID(id), a['id'])) for a in p['assets']}
            for a in p['assets']:
                data = {**a, 'source_artifact': id, 'source_locator': a['id']}
                store.put(c, 'asset', data, actor(request), mapping[a['id']])
            for edge in p['relationships']:
                if edge['source'] not in mapping or edge['target'] not in mapping:
                    raise ValueError('Relationship has an unknown endpoint')
                store.put(c, 'relationship', {**edge, 'source': mapping[edge['source']], 'target': mapping[edge['target']], 'source_artifact': id}, actor(request))
            for o in p['observations']:
                store.put(c, 'observation', {**o, 'subject': mapping[o['subject']], 'source_artifact': id}, actor(request))
            r = store.put(c, 'upload', {**r['data'], 'status': 'merged'}, actor(request), id, r['revision_id'])
            store.event(c, actor(request), 'upload.merged', [id], assets=len(p['assets']), relationships=len(p['relationships']), observations=len(p['observations']))
        return r

    @app.get('/api/evidence/{id}/preview')
    def evidence_preview(id: str):
        r = record(id, 'upload')
        path = evidence_artifact(r)
        if r['data'].get('quarantined'):
            return {'text': 'This evidence is quarantined. Review the restricted original.', 'quarantined': True, 'base_revision_id': r['revision_id'], 'artifact_sha256': r['data']['sha256']}
        with path.open('rb') as f:
            raw = f.read(65537)
        if secret_bearing(raw) or b'\0' in raw or raw.startswith(b'PK'):
            return {'text': 'A safe text preview is unavailable. Use the reviewed import view.', 'quarantined': True, 'base_revision_id': r['revision_id'], 'artifact_sha256': r['data']['sha256']}
        return {'text': safe_text(raw) + ('\n[Preview limit reached]' if len(raw) > 65536 else ''), 'quarantined': False, 'base_revision_id': r['revision_id'], 'artifact_sha256': r['data']['sha256']}

    @app.get('/api/evidence/{id}/download')
    def download(id: str, request: Request):
        r = record(id, 'upload')
        admin(request)
        path = evidence_artifact(r)
        with store.tx() as c:
            store.event(c, actor(request), 'evidence.downloaded', [id], sha256=r['data']['sha256'])
        return FileResponse(path, media_type='application/octet-stream', filename='evidence-' + id + '.bin')

    @app.get('/api/evidence/{id}/render')
    def render_evidence_image(id: str, request: Request):
        r = record(id, 'upload')
        if r['data'].get('quarantined') or not r['data'].get('reviewed_for_export'):
            raise HTTPException(409, 'A host reviewer must approve this evidence before inline display.')
        path = evidence_artifact(r)
        try:
            from .evidence_images import identify
            media_type, extension = identify(path)
        except ValueError as error:
            raise HTTPException(415, str(error)) from error
        except OSError as error:
            store.blocked = 'Evidence image access failed. Preserve the workspace and inspect the artifact.'
            raise RuntimeError(store.blocked) from error
        with store.tx() as c:
            store.event(c, actor(request), 'evidence.rendered', [id], sha256=r['data']['sha256'], media_type=media_type)
        return FileResponse(
            path,
            media_type=media_type,
            filename='evidence-' + id + '.' + extension,
            content_disposition_type='inline',
        )

    @app.post('/api/findings/{id}/submit')
    def submit(id: str, request: Request):
        r = record(id, 'finding'); edit_allowed(request, r)
        d = r['data']
        if d.get('writing_state') == 'submitted':
            with store.connect() as c:
                existing = c.execute("SELECT id FROM records WHERE kind='lead' AND json_extract(data,'$.finding_id')=? AND json_extract(data,'$.finding_revision_id')=? ORDER BY updated_at DESC LIMIT 1", (id, r['revision_id'])).fetchone()
            if existing:
                return store.get(existing[0])
            raise HTTPException(409, 'This submitted revision has no lead receipt. Review the audit before retrying.')
        if d.get('writing_state') != 'ready':
            raise HTTPException(422, 'Mark the finding Ready for scribe before submission.')
        if not d['asset_ids'] or not d['evidence_ids'] and not d['evidence_needed'].strip():
            raise HTTPException(422, 'Choose an affected asset. Attach evidence or explain what is still needed.')
        with store.tx() as c:
            submitted = c.execute("SELECT id FROM records WHERE kind='lead' AND json_extract(data,'$.finding_id')=? AND json_extract(data,'$.finding_revision_id')=? ORDER BY updated_at DESC LIMIT 1", (id, r['revision_id'])).fetchone()
            if submitted:
                return store.get(submitted[0], c)
            key = 'lead:' + r['revision_id']
            prior = c.execute('SELECT record_id FROM imports WHERE key=?', (key,)).fetchone()
            if prior:
                return store.get(prior[0], c)
            current = store.get(id, c)
            if current['revision_id'] != r['revision_id']:
                raise Conflict(current)
            submitted = store.put(c, 'finding', {**d, 'writing_state': 'submitted'}, actor(request), id, r['revision_id'])
            lead = store.put(c, 'lead', {**submitted['data'], 'finding_id': id, 'finding_revision_id': submitted['revision_id'], 'tester_id': actor(request)}, actor(request))
            c.execute('INSERT INTO imports VALUES(?,?)', (key, lead['id']))
            store.event(c, actor(request), 'lead.submitted', [lead['id'], id], finding_revision_id=submitted['revision_id'])
        return lead

    @app.post('/api/leads/{id}/draft')
    def draft_from_lead(id: str, request: Request):
        if APP_NAME != 'Merlin':
            raise HTTPException(403, 'Lead drafting belongs to Merlin.')
        lead = record(id, 'lead')
        data = lead['data']
        with store.tx() as c:
            key = 'draft-from-lead:' + id
            prior = c.execute('SELECT record_id FROM imports WHERE key=?', (key,)).fetchone()
            if prior:
                return store.get(prior[0], c)
            draft = store.put(c, 'draft', {
                'title': data.get('title', 'Untitled finding'),
                'description': data.get('observation', ''),
                'impact': data.get('impact', ''),
                'remediation': '',
                'references': '',
                'evidence_ids': data.get('evidence_ids', []),
                'owner_id': actor(request),
                'lead_id': id,
            }, actor(request))
            c.execute('INSERT INTO imports VALUES(?,?)', (key, draft['id']))
            store.event(c, actor(request), 'draft.created_from_lead', [draft['id'], id])
        return draft

    @app.post('/api/questions')
    def question(body: models.Question, request: Request):
        if APP_NAME != 'Merlin':
            raise HTTPException(403, 'Evidence questions belong to Merlin.')
        record(body.finding_id, 'finding')
        with store.tx() as c:
            r = store.put(c, 'question', body.model_dump(), actor(request))
            store.event(c, actor(request), 'question.created', [r['id'], body.finding_id])
        return r

    @app.get('/api/connections')
    def connections():
        peers = store.setting('peers', [])
        ghostwriter = store.setting('ghostwriter', {})
        peer_rows = [{**{k: p.get(k, '') for k in ('id', 'name', 'origin', 'recipient')}, 'status': 'enrolled'} for p in peers]
        from .ghostwriter_adapter import ADAPTER
        ghostwriter_status = 'verified' if ghostwriter.get('schema_hash') and ghostwriter.get('adapter_id') == ADAPTER else 'configured_unverified' if ghostwriter else 'not_configured'
        return {'peers': peer_rows, 'ghostwriter': {'status': ghostwriter_status, 'origin': ghostwriter.get('origin', ''), 'report_id': ghostwriter.get('report_id', '')}}

    @app.get('/api/events')
    async def events(request: Request):
        try:
            start = int(request.headers.get('last-event-id', '0'))
        except ValueError:
            raise HTTPException(422, 'Invalid event cursor.')
        async def stream():
            last = start
            while not await request.is_disconnected():
                if not auth.session(store, request.cookies.get(cookie)):
                    break
                with store.connect() as c:
                    head = c.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
                    if head - last > 1000 or last > head:
                        last = head
                        yield f'id: {last}\nevent: reset\ndata: {{}}\n\n'
                    else:
                        for row in c.execute('SELECT seq,body FROM events WHERE seq>? ORDER BY seq LIMIT 1000', (last,)):
                            last = row['seq']
                            yield f'id: {last}\nevent: change\ndata: {canonical({"ids": json.loads(row["body"])["ids"], "sequence": last})}\n\n'
                yield ': connected\n\n'
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    from .transfer import routes as transfer_routes
    from .ghostwriter_adapter import routes as delivery_routes
    transfer_routes(app, store, actor, admin, record)
    delivery_routes(app, store, actor, admin, record)
    @app.api_route('/api/{missing:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    def missing_api(missing: str):
        raise HTTPException(404, 'This operation is not available.')
    frontend = Path(__file__).parent.parent / 'frontend' / 'dist'
    if frontend.is_dir():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app
