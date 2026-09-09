import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App, DraftEditor, DraftFlow, Evidence, Inbox, Transfer } from '../src/main'

const session = { user: { id: 'u1', name: 'Casey', role: 'lead_scribe' }, csrf: 'csrf-1', app: 'merlin', engagement: { id: 'e1', name: 'Northwind' }, mode: 'lan' }
const draft = { id: 'draft-1', kind: 'draft', revision_id: 'rev-1', updated_at: '2026-09-08T19:00:00Z', data: { title: 'Draft', description: 'Server text', impact: '', remediation: '', references: '', evidence_ids: [], owner_id: '', lead_id: '' } }
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const eventSources: MockEventSource[] = []
class MockEventSource {
  onopen?: () => void
  onerror?: () => void
  private listeners = new Map<string, (() => void)[]>()
  constructor() { eventSources.push(this) }
  addEventListener(type: string, listener: () => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]) }
  emit(type: string) { for (const listener of this.listeners.get(type) ?? []) listener() }
  close() {}
}
let fetchMock: ReturnType<typeof vi.fn>
async function settledRender(ui: Parameters<typeof render>[0]) { let result!: ReturnType<typeof render>; await act(async () => { result = render(ui); }); return result; }


function baseFetch(path: string) {
  if (path === '/api/session') return response(session)
  if (path === '/api/events') return response({})
  if (path.startsWith('/api/records?kind=lead')) return response({ items: [], total: 0 })
  if (path.startsWith('/api/records?kind=draft')) return response({ items: [draft], total: 1 })
  if (path === '/api/connections') return response({ peers: [], ghostwriter: { status: 'configured', report_id: 'report-1' } })
  return response({ items: [] })
}

beforeEach(() => { history.replaceState(null, '', '#/inbox'); eventSources.length = 0; vi.stubGlobal('EventSource', MockEventSource); fetchMock = vi.fn((input: RequestInfo | URL) => baseFetch(String(input))); vi.stubGlobal('fetch', fetchMock) })
afterEach(async () => { await act(async () => {}); cleanup(); vi.restoreAllMocks(); vi.useRealTimers() })

describe('Merlin UI contract', () => {
  it('logs in through the session contract', async () => {
    fetchMock.mockReset().mockImplementationOnce(() => response({ detail: 'unauthenticated' }, 401)).mockImplementationOnce(() => response(session)).mockImplementation(baseFetch)
    render(<App />)
    await screen.findByRole('heading', { name: 'Make the prose accountable.' })
    fireEvent.change(screen.getByLabelText('Account name'), { target: { value: 'Casey' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Inbox' })).toBeInTheDocument()
  })

  it('reports connection loss and keeps server writes disabled', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: 'Inbox' })
    act(() => eventSources[0]?.onopen?.())
    expect(screen.getByText(/connected/)).toBeInTheDocument()
    act(() => eventSources[0]?.onerror?.())
    expect(screen.getByText(/Connection lost/)).toBeInTheDocument()
  })

  it('uses dispatchable SSE events for sync state without claiming an unsaved write', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: 'Inbox' })
    expect(screen.getByText(/Connection not confirmed/)).toBeInTheDocument()
    act(() => eventSources[0]?.onopen?.())
    expect(screen.getByText(/connected/)).toBeInTheDocument()
    await act(async () => eventSources[0]?.emit('change'))
    await act(async () => eventSources[0]?.emit('reset'))
    expect(screen.getByText(/connected/)).toBeInTheDocument()
    act(() => eventSources[0]?.onerror?.())
    expect(screen.getByText(/Connection lost/)).toBeInTheDocument()
  })

  it('serializes autosaves and keeps edits made during an in-flight save', async () => {
    let resolveFirst!: (value: Response) => void
    const firstSave = new Promise<Response>(resolve => { resolveFirst = resolve })
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/records/draft-1' && init?.method === 'PUT') {
        const count = fetchMock.mock.calls.filter(call => String(call[0]) === '/api/records/draft-1').length
        return count === 1 ? firstSave : Promise.resolve(response({ ...draft, revision_id: 'rev-3', data: { ...draft.data, description: 'Local two' } }))
      }
      return baseFetch(String(input))
    })
    render(<DraftEditor selected={draft} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    const editor = screen.getByLabelText('Draft description in Markdown')
    fireEvent.change(editor, { target: { value: 'Local one' } })
    await waitFor(() => expect(fetchMock.mock.calls.some(call => String(call[0]) === '/api/records/draft-1')).toBe(true), { timeout: 1500 })
    fireEvent.change(editor, { target: { value: 'Local two' } })
    resolveFirst(response({ ...draft, revision_id: 'rev-2', data: { ...draft.data, description: 'Local one' } }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(call => String(call[0]) === '/api/records/draft-1').length).toBeGreaterThanOrEqual(2), { timeout: 1500 })
    const saves = fetchMock.mock.calls.filter(call => String(call[0]) === '/api/records/draft-1')
    expect(JSON.parse(String(saves.at(-1)?.[1]?.body)).data.description).toBe('Local two')
    expect(JSON.parse(String(saves.at(-1)?.[1]?.body)).base_revision_id).toBe('rev-2')
  }, 5000)

  it('turns a first POST into revisioned PUTs without duplicate drafts', async () => {
    let resolveFirst!: (value: Response) => void
    const firstSave = new Promise<Response>(resolve => { resolveFirst = resolve })
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/records' && init?.method === 'POST') return firstSave
      if (String(input) === '/api/records/new-draft' && init?.method === 'PUT') return response({ ...draft, id: 'new-draft', revision_id: 'new-rev-2', data: { ...draft.data, description: 'Second edit' } })
      return baseFetch(String(input))
    })
    render(<DraftEditor selected={null} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    const editor = screen.getByLabelText('Draft description in Markdown')
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New draft' } })
    fireEvent.change(editor, { target: { value: 'First edit' } })
    await waitFor(() => expect(fetchMock.mock.calls.some(call => String(call[0]) === '/api/records')).toBe(true), { timeout: 1500 })
    fireEvent.change(editor, { target: { value: 'Second edit' } })
    resolveFirst(response({ ...draft, id: 'new-draft', revision_id: 'new-rev-1', data: { ...draft.data, title: 'New draft', description: 'First edit' } }))
    await waitFor(() => expect(fetchMock.mock.calls.some(call => String(call[0]) === '/api/records/new-draft')).toBe(true), { timeout: 1500 })
    expect(fetchMock.mock.calls.filter(call => String(call[0]) === '/api/records')).toHaveLength(1)
    const update = fetchMock.mock.calls.find(call => String(call[0]) === '/api/records/new-draft')
    expect(JSON.parse(String(update?.[1]?.body)).base_revision_id).toBe('new-rev-1')
  }, 5000)

  it('rebases preserved conflict text and retries the current server revision', async () => {
    let attempts = 0
    const server = { ...draft, revision_id: 'rev-current', data: { ...draft.data, description: 'Server revision' } }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/records/draft-1' && init?.method === 'PUT') {
        attempts++
        if (attempts === 1) return response({ detail: { current: server } }, 409)
        return response({ ...server, revision_id: 'rev-next', data: { ...server.data, description: 'Preserved local text' } })
      }
      return baseFetch(String(input))
    })
    render(<DraftEditor selected={draft} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    fireEvent.change(screen.getByLabelText('Draft description in Markdown'), { target: { value: 'Preserved local text' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save now' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Keep my text on current revision' }))
    await waitFor(() => expect(attempts).toBe(2))
    const saves = fetchMock.mock.calls.filter(call => String(call[0]) === '/api/records/draft-1')
    expect(JSON.parse(String(saves[1][1]?.body)).base_revision_id).toBe('rev-current')
    expect(JSON.parse(String(saves[1][1]?.body)).data.description).toBe('Preserved local text')
  })

  it('reconciles an open clean draft to the server revision after refresh', async () => {
    const current = { ...draft, revision_id: 'rev-r2', data: { ...draft.data, description: 'Remote revision R2' } }
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/connections') return response({ peers: [], ghostwriter: { status: 'verified', report_id: 'report-1' } })
      if (path === '/api/deliveries') return response({ items: [] })
      if (path === '/api/records/draft-1') return response(current)
      return baseFetch(path)
    })
    const flow = render(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={0} />)
    expect(await screen.findByLabelText('Draft description in Markdown')).toHaveValue('Server text')
    flow.rerender(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={1} />)
    await waitFor(() => expect(screen.getByLabelText('Draft description in Markdown')).toHaveValue('Remote revision R2'))
    expect(screen.getByText('Saved revision is current.')).toBeInTheDocument()
  })

  it('clears a delivery preview when SSE refresh returns a new draft revision', async () => {
    const current = { ...draft, revision_id: 'rev-r2', data: { ...draft.data, description: 'Remote revision R2' } }
    const proposal = { proposal_id: 'proposal-1', proposal_hash: 'proposal-hash', adapter_id: 'ghostwriter', draft_id: 'draft-1', draft_revision_id: 'rev-1', destination_origin: 'https://ghostwriter.test', report_id: 'report-1', payload_hash: 'hash-1', payload: { title: 'Draft' }, evidence_manifest: [], attachment_state: 'manual_required' }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/connections') return response({ peers: [], ghostwriter: { status: 'verified', report_id: 'report-1' } })
      if (path === '/api/deliveries') return response({ items: [] })
      if (path === '/api/deliveries/preview' && init?.method === 'POST') return response(proposal)
      if (path === '/api/records/draft-1') return response(current)
      return baseFetch(path)
    })
    const flow = render(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={0} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Preview delivery' }))
    expect(await screen.findByLabelText('Delivery preview')).toBeInTheDocument()
    flow.rerender(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={1} />)
    await waitFor(() => expect(screen.getByLabelText('Draft description in Markdown')).toHaveValue('Remote revision R2'))
    expect(screen.queryByLabelText('Delivery preview')).toBeNull()
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeDisabled()
  })

  it('keeps local draft text and exposes the server conflict after refresh', async () => {
    const current = { ...draft, revision_id: 'rev-r2', data: { ...draft.data, description: 'Remote revision R2' } }
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/connections') return response({ peers: [], ghostwriter: { status: 'verified', report_id: 'report-1' } })
      if (path === '/api/deliveries') return response({ items: [] })
      if (path === '/api/records/draft-1') return response(current)
      return baseFetch(path)
    })
    const flow = render(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={0} />)
    const editor = await screen.findByLabelText('Draft description in Markdown')
    fireEvent.change(editor, { target: { value: 'Local revision R1' } })
    flow.rerender(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={1} />)
    expect(await screen.findByText('Another revision was saved.')).toBeInTheDocument()
    expect(editor).toHaveValue('Local revision R1')
    expect(screen.getByText(/The server changed this draft/)).toBeInTheDocument()
    expect(screen.getByText('Conflict · choose a version before saving.')).toBeInTheDocument()
    expect(screen.queryByText('Saved revision is current.')).toBeNull()
  })

  it('locks local draft text when refresh reports an uncertain remote delivery', async () => {
    const current = { ...draft, revision_id: 'rev-r2', data: { ...draft.data, description: 'Remote revision R2' } }
    const uncertain = { id: 'delivery-1', kind: 'delivery', revision_id: 'delivery-r2', updated_at: '2026-09-08T20:00:00Z', data: { status: 'uncertain', draft_id: draft.id, report_id: 'report-1', payload_hash: 'hash-1' } }
    let refreshed = false
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/connections') return response({ peers: [], ghostwriter: { status: 'verified', report_id: 'report-1' } })
      if (path === '/api/deliveries') return response({ items: refreshed ? [uncertain] : [] })
      if (path === '/api/records/draft-1') return response(current)
      return baseFetch(path)
    })
    const flow = render(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={0} />)
    const editor = await screen.findByLabelText('Draft description in Markdown')
    fireEvent.change(editor, { target: { value: 'Local text before uncertain delivery' } })
    refreshed = true
    flow.rerender(<DraftFlow selected={draft} onBack={() => undefined} refreshKey={1} />)
    expect(await screen.findByText('uncertain')).toBeInTheDocument()
    expect(editor).toHaveValue('Local text before uncertain delivery')
    expect(editor).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save draft file' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send to Ghostwriter' })).toBeDisabled()
    expect(screen.getByText(/Not saved · draft locked by Ghostwriter receipt is uncertain/)).toBeInTheDocument()
  })

  it('refreshes a newly created draft by its saved ID and locks a reconciling delivery', async () => {
    const saved = { ...draft, id: 'draft-created', revision_id: 'created-r1', data: { ...draft.data, title: 'Created draft', description: 'Created text' } }
    const reconciling = { id: 'delivery-created', kind: 'delivery', revision_id: 'delivery-r1', updated_at: 'now', data: { status: 'reconciling', draft_id: 'draft-created', payload_hash: 'hash-created' } }
    let refreshed = false
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/connections') return response({ peers: [], ghostwriter: { status: 'verified', report_id: 'report-1' } })
      if (path === '/api/evidence') return response({ items: [] })
      if (path === '/api/records' && init?.method === 'POST') return response(saved)
      if (path === '/api/records/draft-created') return response(saved)
      if (path === '/api/deliveries') return response({ items: refreshed ? [reconciling] : [] })
      return baseFetch(path)
    })
    const flow = render(<DraftFlow selected={null} onBack={() => undefined} refreshKey={0} />)
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Created draft' } })
    fireEvent.change(screen.getByLabelText('Draft description in Markdown'), { target: { value: 'Created text' } })
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === '/api/records' && init?.method === 'POST')).toBe(true), { timeout: 1500 })
    refreshed = true
    flow.rerender(<DraftFlow selected={null} onBack={() => undefined} refreshKey={1} />)
    expect(await screen.findByText('reconciling')).toBeInTheDocument()
    expect(screen.getByLabelText('Draft description in Markdown')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Preview delivery' })).toBeDisabled()
  }, 5000)

  it('creates a draft through the idempotent lead endpoint', async () => {
    const lead = { id: 'lead-1', kind: 'lead', revision_id: 'lead-rev', updated_at: 'now', data: { title: 'Lead', observation: 'Observed' } }
    const created = { ...draft, data: { ...draft.data, lead_id: 'lead-1' } }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith('/api/records?kind=lead')) return response({ items: [lead], total: 1 })
      if (String(input) === '/api/leads/lead-1/draft' && init?.method === 'POST') return response(created)
      return baseFetch(String(input))
    })
    const opened = vi.fn()
    render(<Inbox onOpen={opened} />)
    fireEvent.click(await screen.findByRole('button', { name: /Lead/ }))
    await waitFor(() => expect(opened).toHaveBeenCalledWith(created))
  })

  it('provides real Write, Split, and Preview views', async () => {
    await settledRender(<DraftEditor selected={draft} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    expect(screen.getByLabelText('Draft description in Markdown')).toBeInTheDocument()
    expect(screen.getByLabelText('Markdown preview')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Write' }))
    expect(screen.queryByLabelText('Markdown preview')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    expect(screen.queryByLabelText('Draft description in Markdown')).toBeNull()
    expect(screen.getByLabelText('Markdown preview')).toBeInTheDocument()
  })

  it('renders only selected local evidence and fragment Markdown links', async () => {
    const evidenceId = '11111111-1111-4111-8111-111111111111'
    const selected = { ...draft, data: { ...draft.data, evidence_ids: [evidenceId], description: `[http](https://outside.invalid) [mail](mailto:outside@example.invalid) [data](data:text/plain,unsafe) [script](javascript:alert(1)) [bad](/api/evidence/not-a-uuid/render) [safe](/api/evidence/${evidenceId}/render) [part](#evidence)\n![local](/api/evidence/${evidenceId}/render) ![remote](https://outside.invalid/pixel)` } }
    await settledRender(<DraftEditor selected={selected} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    expect(screen.getByRole('link', { name: 'safe' })).toHaveAttribute('href', `/api/evidence/${evidenceId}/render`)
    expect(screen.getByRole('link', { name: 'part' })).toHaveAttribute('href', '#evidence')
    expect(screen.queryByRole('link', { name: 'http' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'mail' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'data' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'script' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'bad' })).toBeNull()
    expect(document.querySelector(`img[src="/api/evidence/${evidenceId}/render"]`)).not.toBeNull()
    expect(screen.getByText('Embedded image omitted. Attach reviewed evidence instead.')).toBeInTheDocument()
  })

  it('stores selected evidence IDs and inserts only the authenticated render path', async () => {
    const evidenceId = '22222222-2222-4222-8222-222222222222'
    const evidence = { id: evidenceId, kind: 'evidence', revision_id: 'e1', updated_at: 'now', data: { filename: 'sanitized.png', reviewed_for_export: true } }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/evidence') return response({ items: [evidence] })
      if (String(input) === '/api/records/draft-1' && init?.method === 'PUT') return response({ ...draft, revision_id: 'rev-2', data: { ...draft.data, evidence_ids: [evidenceId] } })
      return baseFetch(String(input))
    })
    render(<DraftEditor selected={draft} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    fireEvent.click(await screen.findByLabelText('sanitized.png'))
    fireEvent.click(screen.getByRole('button', { name: 'Insert evidence' }))
    expect(screen.getByLabelText('Draft description in Markdown')).toHaveValue(`![Reviewed evidence](/api/evidence/${evidenceId}/render)Server text`)
    fireEvent.click(screen.getByRole('button', { name: 'Save now' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === '/api/records/draft-1' && init?.method === 'PUT')).toBe(true))
    const save = fetchMock.mock.calls.find(([input, init]) => String(input) === '/api/records/draft-1' && init?.method === 'PUT')
    expect(JSON.parse(String(save?.[1]?.body)).data.evidence_ids).toEqual([evidenceId])
  })

  it('shows quarantined evidence without turning unsafe HTML into DOM', async () => {
    const evidence = { id: 'e1', kind: 'evidence', revision_id: 'r1', updated_at: 'now', data: { filename: 'raw.txt', finding_id: 'finding-1' } }
    fetchMock.mockImplementation((input: RequestInfo | URL) => { const path = String(input); if (path === '/api/evidence') return response({ items: [evidence], total: 1 }); if (path === '/api/evidence/e1/preview') return response({ text: '<img src="https://evil.example/x" onerror="alert(1)">unsafe', quarantined: true }); return baseFetch(path) })
    render(<Evidence refreshKey={0} />)
    fireEvent.click(await screen.findByRole('button', { name: /raw.txt/ }))
    expect(await screen.findByText('Quarantined text · treat as untrusted input.')).toBeInTheDocument()
    expect(screen.getByText(/unsafe/)).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('[onerror]')).toBeNull()
  })

  it('does not render a stale evidence preview under a later selection', async () => {
    const first = { id: 'e1', kind: 'upload', revision_id: 'r1', updated_at: 'now', data: { filename: 'first.txt' } }
    const second = { id: 'e2', kind: 'upload', revision_id: 'r2', updated_at: 'now', data: { filename: 'second.txt' } }
    let resolveFirst!: (value: Response) => void
    let resolveSecond!: (value: Response) => void
    const firstPreview = new Promise<Response>(resolve => { resolveFirst = resolve })
    const secondPreview = new Promise<Response>(resolve => { resolveSecond = resolve })
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/evidence') return response({ items: [first, second], total: 2 })
      if (path === '/api/evidence/e1/preview') return firstPreview
      if (path === '/api/evidence/e2/preview') return secondPreview
      return baseFetch(path)
    })
    render(<Evidence refreshKey={0} />)
    fireEvent.click(await screen.findByRole('button', { name: /first.txt/ }))
    fireEvent.click(screen.getByRole('button', { name: /second.txt/ }))
    resolveSecond(response({ text: 'Second proof', quarantined: false }))
    expect(await screen.findByText('Second proof')).toBeInTheDocument()
    resolveFirst(response({ text: 'Stale first proof', quarantined: false }))
    await waitFor(() => expect(screen.queryByText('Stale first proof')).toBeNull())
  })

  it('reviews, sends, and reloads an uncertain Ghostwriter delivery status', async () => {
    const uncertain = { id: 'delivery-1', kind: 'delivery', revision_id: 'dr-2', updated_at: '2026-09-08T19:01:00Z', data: { status: 'uncertain', draft_id: 'draft-1', report_id: 'report-1', payload_hash: 'hash-1' } }
    const delivered = { ...uncertain, revision_id: 'dr-3', data: { ...uncertain.data, status: 'delivered', remote_id: 44 } }
    const proposal = { proposal_id: 'proposal-1', proposal_hash: 'proposal-hash', adapter_id: 'ghostwriter', draft_id: 'draft-1', draft_revision_id: 'rev-1', destination_origin: 'https://ghostwriter.test', report_id: 'report-1', payload_hash: 'hash-1', payload: { title: 'Draft', description: '<strong>Exact description</strong>', impact: 'Exact impact', mitigation: 'Exact mitigation', references: 'Exact references', reportId: 7, severityId: 4, findingTypeId: 2, complete: false, extraFields: { merlin_evidence_attachment: 'manual_required' } }, evidence_manifest: [{ id: 'e1' }, { id: 'e2' }], attachment_state: 'manual_required' }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => { const path = String(input); if (path === '/api/connections') return baseFetch(path); if (path === '/api/deliveries') return response({ items: [] }); if (path === '/api/deliveries/preview' && init?.method === 'POST') return response(proposal); if (path === '/api/deliveries/review' && init?.method === 'POST') return response({ id: 'delivery-1', kind: 'delivery', revision_id: 'dr-1', updated_at: '2026-09-08T19:00:00Z', data: { status: 'reviewed', draft_id: 'draft-1', report_id: 'report-1', payload_hash: 'hash-1' } }); if (path === '/api/deliveries/delivery-1/send' && init?.method === 'POST') return response(uncertain); if (path === '/api/deliveries/delivery-1/reconcile' && init?.method === 'POST') return response(delivered); return response({}) })
    const first = render(<DraftFlow selected={draft} onBack={() => undefined} />)
    expect(await screen.findByRole('button', { name: 'Mark reviewed' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Preview delivery' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input, init]) => String(input) === '/api/deliveries/preview' && init?.method === 'POST')
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ draft_id: 'draft-1', draft_revision_id: 'rev-1', report_id: 'report-1' })
    })
    expect(screen.getByLabelText('Delivery preview')).toHaveTextContent('Destination: https://ghostwriter.test')
    expect(screen.getByLabelText('Delivery preview')).toHaveTextContent('Report: report-1')
    expect(screen.getByLabelText('Delivery preview')).toHaveTextContent('Title: Draft')
    expect(screen.getByLabelText('Delivery preview')).toHaveTextContent('Evidence: 2')
    const exact = screen.getByLabelText('Exact Ghostwriter payload')
    expect(exact).toHaveTextContent('TitleDraft')
    expect(exact).toHaveTextContent('<strong>Exact description</strong>')
    expect(exact).toHaveTextContent('Exact impact')
    expect(exact).toHaveTextContent('Exact mitigation')
    expect(exact).toHaveTextContent('Exact references')
    expect(exact).toHaveTextContent('Report ID7')
    expect(exact).toHaveTextContent('Severity ID4')
    expect(exact).toHaveTextContent('Finding type ID2')
    expect(exact).toHaveTextContent('Completefalse')
    expect(exact).not.toHaveTextContent('Added as blank')
    expect(exact).toHaveTextContent('Evidence attachment statemanual_required')
    expect(exact).toHaveTextContent('merlin_evidence_attachment')
    expect(exact).toHaveTextContent('proposal-hash')
    expect(exact).toHaveTextContent('hash-1')
    expect(exact.querySelector('pre strong')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Mark reviewed' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input, init]) => String(input) === '/api/deliveries/review' && init?.method === 'POST')
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ draft_id: 'draft-1', draft_revision_id: 'rev-1', report_id: 'report-1', proposal_id: 'proposal-1', proposal_hash: 'proposal-hash' })
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Send to Ghostwriter' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input, init]) => String(input) === '/api/deliveries/delivery-1/send' && init?.method === 'POST')
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ delivery_revision_id: 'dr-1', payload_hash: 'hash-1' })
    })
    expect(await screen.findByText('uncertain')).toBeInTheDocument()
    expect(screen.getByLabelText('Draft description in Markdown')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send to Ghostwriter' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Remote Ghostwriter finding ID'), { target: { value: '44' } })
    fireEvent.click(screen.getByRole('button', { name: 'Reconcile receipt' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input, init]) => String(input) === '/api/deliveries/delivery-1/reconcile' && init?.method === 'POST')
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ delivery_revision_id: 'dr-2', payload_hash: 'hash-1', remote_id: 44 })
    })
    expect(await screen.findByText('delivered')).toBeInTheDocument()
    first.unmount()
    fetchMock.mockImplementation((input: RequestInfo | URL) => String(input) === '/api/deliveries' ? response({ items: [uncertain] }) : response({}))
    render(<DraftFlow selected={draft} onBack={() => undefined} />)
    expect(await screen.findByText('uncertain')).toBeInTheDocument()
  })

  it('keeps unsaved text mounted when navigation is requested', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: 'Inbox' })
    fireEvent.click(screen.getByRole('link', { name: 'Drafts' }))
    fireEvent.click(await screen.findByRole('button', { name: 'New draft' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Unsaved title' } })
    fireEvent.click(screen.getByRole('link', { name: 'Inbox' }))
    expect(await screen.findByRole('dialog', { name: 'Work is not saved' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('Unsaved title')
  })

  it('replaces remote and authenticated local Markdown images without creating requests', async () => {
    const withImages = {
      ...draft,
      data: {
        ...draft.data,
        description: '![remote](https://outside.invalid/pixel)\n![local](/api/evidence/e1/download)',
      },
    }
    await settledRender(<DraftEditor selected={withImages} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    expect(screen.getAllByText('Embedded image omitted. Attach reviewed evidence instead.')).toHaveLength(2)
    expect(document.querySelector('img')).toBeNull()
    expect(fetchMock.mock.calls.map(call => String(call[0]))).toEqual(['/api/evidence'])
  })

  it('refreshes an already-open inbox and keeps its queue paged', async () => {
    const first = { id: 'lead-1', kind: 'lead', revision_id: 'r1', updated_at: '', data: { title: 'First lead' } }
    const second = { id: 'lead-2', kind: 'lead', revision_id: 'r2', updated_at: '', data: { title: 'Second lead' } }
    fetchMock.mockImplementation((input: RequestInfo | URL) => String(input).includes('offset=0') ? response({ items: [first], total: 101 }) : response({ items: [second], total: 101 }))
    const opened = vi.fn()
    const rendered = render(<Inbox refreshKey={0} onOpen={opened} />)
    expect(await screen.findByText('First lead')).toBeInTheDocument()
    rendered.rerender(<Inbox refreshKey={1} onOpen={opened} />)
    await waitFor(() => expect(fetchMock.mock.calls.filter(call => String(call[0]).includes('kind=lead')).length).toBeGreaterThan(1))
    expect(screen.getByText('Showing 1 of 101 leads')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Second lead')).toBeInTheDocument()
  })

  it('does not call a locked draft autosaving and keeps local text available for file save', async () => {
    const rendered = await settledRender(<DraftEditor selected={draft} onSaved={() => undefined} onDirtyChange={() => undefined} />)
    fireEvent.change(screen.getByLabelText('Draft description in Markdown'), { target: { value: 'Local text before delivery' } })
    rendered.rerender(<DraftEditor selected={draft} lockedReason="uncertain delivery" onSaved={() => undefined} onDirtyChange={() => undefined} />)
    expect(screen.getByLabelText('Draft description in Markdown')).toHaveValue('Local text before delivery')
    expect(screen.getByText(/Not saved · draft locked by uncertain delivery/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save draft file' })).toBeEnabled()
    expect(screen.queryByText(/Autosaving in one second/)).toBeNull()
  })

  it('renders imported and conflict transfer receipts without claiming conflict imports', async () => {
    const receipts = [
      { status: 'imported', imported: 2, duplicates: 1, conflicts: [], deferred: [], bundle_id: 'bundle-ok', manifest_hash: 'hash-ok' },
      { status: 'conflict', imported: 0, duplicates: 1, conflicts: ['draft-7'], deferred: ['draft-8'], bundle_id: 'bundle-conflict', manifest_hash: 'hash-conflict' },
    ]
    let next = 0
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/connections') return response({ peers: [], ghostwriter: {} })
      if (String(input) === '/api/transfers/import' && init?.method === 'POST') return response(receipts[next++])
      return baseFetch(String(input))
    })
    render(<Transfer />)
    const input = screen.getByLabelText('File fallback')
    fireEvent.change(input, { target: { files: [new File(['bundle'], 'review.age')] } })
    fireEvent.click(screen.getByRole('button', { name: 'Import and reconcile' }))
    expect(await screen.findByText(/2 imported/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Import and reconcile' }))
    expect(await screen.findByText(/No records imported while this bundle is held/)).toBeInTheDocument()
    expect(screen.getByLabelText('Conflicting record IDs')).toHaveTextContent('draft-7')
    expect(screen.getByLabelText('Deferred record IDs')).toHaveTextContent('draft-8')
  })

  it('renders imported and conflict receipts returned by direct peer handoff', async () => {
    const receipts = [
      { status: 'imported', imported: 1, duplicates: 0, conflicts: [], deferred: [], bundle_id: 'peer-ok', manifest_hash: 'manifest-ok' },
      { status: 'conflict', imported: 0, duplicates: 1, conflicts: ['draft-7'], deferred: ['draft-8'], bundle_id: 'peer-held', manifest_hash: 'manifest-held' },
    ]
    let next = 0
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/connections') return response({ peers: [{ id: 'peer-1', name: 'Harbinger', recipient: 'age1peer', status: 'enrolled' }], ghostwriter: {} })
      if (path === '/api/transfers/preview' && init?.method === 'POST') return response({ review_hash: 'a'.repeat(64), selected_record_ids: ['draft-1'], recipient: { id: 'peer-1', name: 'Harbinger', origin: 'https://harbinger.test' }, records: [{ ...draft, selected: true }], files: [] })
      if (path === '/api/transfers/send' && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ record_ids: ['draft-1'], recipient_id: 'peer-1', review_hash: 'a'.repeat(64) })
        return response(receipts[next++])
      }
      return baseFetch(path)
    })
    render(<Transfer role="lead_scribe" />)
    fireEvent.change(await screen.findByLabelText('Peer recipient'), { target: { value: 'peer-1' } })
    fireEvent.change(screen.getByLabelText('Record IDs'), { target: { value: 'draft-1' } })
    expect(screen.getByRole('button', { name: 'Start peer handoff' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Review transfer' }))
    expect(await screen.findByText(/1 selected · 1 total records/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start peer handoff' }))
    await screen.findAllByText(/bundle peer-ok/)
    expect(screen.getAllByText(/bundle peer-ok/)).toHaveLength(2)
    expect(screen.getByText(/1 imported/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start peer handoff' }))
    expect(await screen.findByText(/No records imported while this bundle is held/)).toBeInTheDocument()
    expect(screen.getByLabelText('Conflicting record IDs')).toHaveTextContent('draft-7')
    expect(screen.getByLabelText('Deferred record IDs')).toHaveTextContent('draft-8')
  })

  it('clears transfer review when the selected records change', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/connections') return response({ peers: [{ id: 'peer-1', name: 'Harbinger', recipient: 'age1peer', status: 'enrolled' }], ghostwriter: {} })
      if (path === '/api/transfers/preview' && init?.method === 'POST') return response({ review_hash: 'b'.repeat(64), selected_record_ids: ['draft-1'], recipient: { id: 'peer-1', name: 'Harbinger', origin: 'https://harbinger.test' }, records: [{ ...draft, selected: true }], files: [] })
      return baseFetch(path)
    })
    render(<Transfer role="lead_scribe" />)
    fireEvent.change(await screen.findByLabelText('Peer recipient'), { target: { value: 'peer-1' } })
    const records = screen.getByLabelText('Record IDs')
    fireEvent.change(records, { target: { value: 'draft-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review transfer' }))
    expect(await screen.findByText(/1 selected · 1 total records/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start peer handoff' })).toBeEnabled()
    fireEvent.change(records, { target: { value: 'draft-2' } })
    expect(screen.queryByText(/1 selected · 1 total records/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Start peer handoff' })).toBeDisabled()
  })

  it('requires lead-scribe access for Ghostwriter and transfer actions', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      if (String(input) === '/api/connections') return response({ peers: [], ghostwriter: { report_id: 'report-1', status: 'configured_unverified' } })
      if (String(input) === '/api/deliveries') return response({ items: [] })
      return baseFetch(String(input))
    })
    const flow = render(<DraftFlow selected={draft} onBack={() => undefined} role="scribe" />)
    expect(await screen.findByText(/Lead-scribe access is required to review/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeDisabled()
    expect(screen.getByText('Ghostwriter status: configured unverified.')).toBeInTheDocument()
    flow.unmount()
    render(<Transfer role="scribe" />)
    expect(await screen.findByText(/Lead-scribe access is required to start handoffs/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start peer handoff' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save encrypted file' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Import and reconcile' })).toBeDisabled()
    expect(fetchMock.mock.calls.some(call => String(call[0]) === '/api/transfers/conflicts')).toBe(false)
  })

  it('reviews host transfer conflicts and reloads the retained local version', async () => {
    let resolved = false
    const incoming = { id: 'draft-7', kind: 'draft', revision_id: 'remote-r', updated_at: '', data: { title: 'Incoming draft', description: 'Incoming summary' } }
    const local = { id: 'draft-7', kind: 'draft', revision_id: 'local-r', updated_at: '', data: { title: 'Local draft', description: 'Local summary' } }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/transfers/conflicts') return response({ items: [{ id: 'conflict-1', kind: 'transfer_conflict', revision_id: 'conflict-r', updated_at: '', data: { state: resolved ? 'resolved' : 'needs_review', bundle_id: 'bundle-held', manifest_hash: 'a'.repeat(64), conflict_ids: ['draft-7'], duplicate_ids: ['lead-1'], deferred_ids: ['evidence-1'], incoming: [incoming] }, local: [local] }], total: 1 })
      if (path === '/api/transfers/conflicts/conflict-1/resolve' && init?.method === 'POST') { expect(JSON.parse(String(init.body))).toEqual({ decision: 'keep_local', conflict_revision_id: 'conflict-r', manifest_hash: 'a'.repeat(64), local_revisions: { 'draft-7': 'local-r' } }); resolved = true; return response({ id: 'conflict-1', kind: 'transfer_conflict', revision_id: 'conflict-r2', updated_at: '', data: { state: 'resolved', bundle_id: 'bundle-held', manifest_hash: 'a'.repeat(64), conflict_ids: ['draft-7'], duplicate_ids: ['lead-1'], deferred_ids: ['evidence-1'] } }) }
      return baseFetch(path)
    })
    render(<Transfer role="lead_scribe" />)
    expect(await screen.findByText('Incoming draft')).toBeInTheDocument()
    expect(screen.getByText('Local draft')).toBeInTheDocument()
    expect(screen.getByText(/1 conflicts · 1 duplicates · 1 deferred/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Keep local records' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(call => String(call[0]) === '/api/transfers/conflicts').length).toBeGreaterThan(1))
    expect(screen.getByText('resolved')).toBeInTheDocument()
  })

  it('accepts an eligible incoming revision and shows its resolution receipt', async () => {
    let resolved = false
    let resolveAccept!: (value: Response) => void
    const accepted = new Promise<Response>(resolve => { resolveAccept = resolve })
    const conflict = { id: 'conflict-accept', kind: 'transfer_conflict', revision_id: 'conflict-r', updated_at: '', data: { state: 'needs_review', bundle_id: 'bundle-accept', manifest_hash: 'b'.repeat(64), conflict_ids: ['draft-7'], duplicate_ids: ['lead-1'], deferred_ids: ['evidence-1'], incoming: [{ id: 'draft-7', kind: 'draft', revision_id: 'remote-r', updated_at: '', data: { title: 'Incoming draft', description: 'Incoming detail', relationships: ['asset-remote'], evidence_question: 'Incoming question', source_instance: 'owner-a', source_revision_id: 'source-r' } }] }, local: [{ id: 'draft-7', kind: 'draft', revision_id: 'local-r', updated_at: '', data: { title: 'Local draft', description: 'Local detail', relationships: ['asset-local'], evidence_question: 'Local question', source_instance: 'owner-a', source_revision_id: 'source-local' } }] }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => { const path = String(input); if (path === '/api/transfers/conflicts') return response({ items: [{ ...conflict, data: { ...conflict.data, state: resolved ? 'resolved' : 'needs_review' } }], total: 1 }); if (path === '/api/transfers/conflicts/conflict-accept/resolve' && init?.method === 'POST') { expect(JSON.parse(String(init.body))).toEqual({ decision: 'accept_incoming', conflict_revision_id: 'conflict-r', manifest_hash: 'b'.repeat(64), local_revisions: { 'draft-7': 'local-r' } }); return accepted } return baseFetch(path) })
    render(<Transfer role="lead_scribe" />)
    const accept = await screen.findByRole('button', { name: 'Accept incoming revision and deferred evidence' })
    expect(screen.getByText('relationships')).toBeInTheDocument()
    expect(screen.getByText('evidence_question')).toBeInTheDocument()
    expect(screen.getAllByText('Source revision')).toHaveLength(2)
    fireEvent.click(accept)
    expect(accept).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Resolving…' })).toBeDisabled()
    resolved = true
    resolveAccept(response({ ...conflict, revision_id: 'conflict-r2', data: { ...conflict.data, state: 'resolved', resolution: 'accept_incoming' } }))
    expect(await screen.findByText('Incoming acceptance receipt')).toBeInTheDocument()
    expect(screen.getByText(/2 imported · 1 exact duplicates/)).toBeInTheDocument()
    expect(screen.getAllByText(new RegExp(`bundle bundle-accept · manifest ${'b'.repeat(64)}`))).toHaveLength(1)
    expect(await screen.findByText('resolved')).toBeInTheDocument()
  })

  it('shows an accept-incoming rejection while retaining the conflict for review', async () => {
    const conflict = { id: 'conflict-reject', kind: 'transfer_conflict', revision_id: 'conflict-r', updated_at: '', data: { state: 'needs_review', bundle_id: 'bundle-reject', manifest_hash: 'c'.repeat(64), conflict_ids: ['draft-7'], duplicate_ids: [], deferred_ids: [], incoming: [{ id: 'draft-7', kind: 'draft', revision_id: 'remote-r', updated_at: '', data: { title: 'Incoming draft', description: 'Incoming detail' } }] }, local: [{ id: 'draft-7', kind: 'draft', revision_id: 'local-r', updated_at: '', data: { title: 'Local draft', description: 'Local detail' } }] }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => { const path = String(input); if (path === '/api/transfers/conflicts') return response({ items: [conflict], total: 1 }); if (path === '/api/transfers/conflicts/conflict-reject/resolve' && init?.method === 'POST') { expect(JSON.parse(String(init.body))).toEqual({ decision: 'accept_incoming', conflict_revision_id: 'conflict-r', manifest_hash: 'c'.repeat(64), local_revisions: { 'draft-7': 'local-r' } }); return response({ detail: 'Server rejected incoming revision' }, 409) } return baseFetch(path) })
    render(<Transfer role="lead_scribe" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Accept incoming revision and deferred evidence' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Server rejected incoming revision')
    expect(screen.getByText('Incoming draft')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Accept incoming revision and deferred evidence' })).toBeEnabled()
  })

  it('marks Ghostwriter attachments as manual without claiming an upload', async () => {
    const delivery = { id: 'delivery-1', kind: 'delivery', revision_id: 'delivery-r', updated_at: '2026-09-08T19:01:00Z', data: { status: 'reviewed', draft_id: 'draft-1', report_id: 'report-1', attachment_state: 'manual_required' } }
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      if (String(input) === '/api/deliveries') return response({ items: [delivery] })
      return baseFetch(String(input))
    })
    render(<DraftFlow selected={draft} onBack={() => undefined} />)
    expect(await screen.findByText(/Evidence files will need to be attached in Ghostwriter/)).toBeInTheDocument()
    expect(screen.queryByText(/Finding text and evidence manifest are delivered/)).toBeNull()
    expect(screen.queryByText(/Attachments uploaded/i)).toBeNull()
  })
})
