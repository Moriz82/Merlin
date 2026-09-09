import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { App, DraftEditor, DraftFlow, Evidence, Inbox } from '../src/main'

const session = { user: { id: 'u1', name: 'Casey', role: 'lead_scribe' }, csrf: 'csrf', app: 'Merlin', engagement: { id: 'e1', name: 'Synthetic engagement' }, mode: 'lan' }
const draft = { id: 'd1', kind: 'draft', revision_id: 'r1', updated_at: '2026-09-09T12:00:00Z', data: { title: 'Synthetic draft', description: 'Saved report text', impact: 'Saved impact', remediation: '', references: '', evidence_ids: [], owner_id: '', lead_id: '' } }
const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
let fetchMock: ReturnType<typeof vi.fn>
function base(path: string) {
  if (path === '/api/session') return response(session)
  if (path === '/api/connections') return response({ ghostwriter: { status: 'verified', report_id: '42' }, peers: [] })
  if (path.startsWith('/api/records?kind=draft')) return response({ items: [draft], total: 1 })
  return response({ items: [], total: 0 })
}
beforeEach(() => {
  history.replaceState(null, '', '#/inbox')
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  fetchMock = vi.fn((path: string) => base(path)); vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each(['reviewed', 'sending', 'uncertain', 'reconciling'])('does not claim delivery while a manual attachment record is %s', async status => {
  fetchMock.mockImplementation((path: string) => path === '/api/deliveries' ? response({ items: [{ id: 'delivery1', kind: 'delivery', revision_id: 'dr1', updated_at: '', data: { draft_id: 'd1', status, attachment_state: 'manual_required', payload_hash: 'a'.repeat(64) } }] }) : base(path))
  render(<DraftFlow selected={draft} onBack={() => undefined} />)
  await screen.findByText(status)
  expect(screen.queryByText(/Finding text and evidence manifest are delivered/)).not.toBeInTheDocument()
  expect(screen.getByText(/Evidence files will need to be attached in Ghostwriter/)).toBeInTheDocument()
})

it('renders selected evidence visibly and replaces load failures through React', async () => {
  const style = document.createElement('style')
  function css(url: string): string { return readFileSync(url, 'utf8').replace(/@import\s+['"]([^'"]+)['"];?/g, (_all, relative: string) => css(resolve(dirname(url), relative))) }
  style.textContent = css(resolve('src/styles.css')); document.head.append(style)
  const id = '11111111-1111-4111-8111-111111111111'
  const imageDraft = { ...draft, data: { ...draft.data, evidence_ids: [id], description: `![Synthetic evidence](/api/evidence/${id}/render)` } }
  try {
    await act(async () => render(<DraftEditor selected={imageDraft} onSaved={() => undefined} onDirtyChange={() => undefined} />))
    const image = screen.getByAltText('Synthetic evidence'); expect(image).toBeVisible()
    fireEvent.error(image)
    expect(screen.getByText(/Evidence image could not load/)).toBeInTheDocument()
  } finally { style.remove() }
})

it('keeps clean locked draft persistence separate from its delivery lock and announces saving status', async () => {
  await act(async () => render(<DraftEditor selected={draft} lockedReason="This revision was delivered." onSaved={() => undefined} onDirtyChange={() => undefined} />))
  const status = screen.getByRole('status', { name: 'Draft save status' })
  expect(status).toHaveTextContent(/Saved/); expect(status).not.toHaveTextContent(/Not saved|only in this editor/)
})

it('contains unsaved-dialog keyboard focus and keeps the skip link out of routing', async () => {
  render(<App />); await screen.findByRole('heading', { name: 'Inbox' })
  fireEvent.click(screen.getByRole('link', { name: 'Skip to content' }))
  expect(location.hash).toBe('#/inbox'); expect(screen.getByRole('main')).toHaveFocus()
  fireEvent.click(screen.getByRole('link', { name: 'Drafts' })); const create = await screen.findByRole('button', { name: 'New draft' }); await act(async () => fireEvent.click(create))
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Keep this text' } })
  const trigger = screen.getByRole('link', { name: 'Inbox' }); trigger.focus(); fireEvent.click(trigger)
  const dialog = screen.getByRole('dialog'); const buttons = within(dialog).getAllByRole('button')
  expect(within(dialog).getByRole('button', { name: 'Stay and edit' })).toHaveFocus()
  buttons[buttons.length - 1].focus(); fireEvent.keyDown(dialog, { key: 'Tab' }); expect(buttons[0]).toHaveFocus()
  fireEvent.keyDown(dialog, { key: 'Escape' }); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(trigger).toHaveFocus()
  expect(screen.getByLabelText('Title')).toHaveValue('Keep this text')
})

it('returns the viewport to the workspace header when a draft opens', async () => {
  render(<App />); await screen.findByRole('heading', { name: 'Inbox' })
  fireEvent.click(screen.getByRole('link', { name: 'Drafts' }))
  document.documentElement.scrollTop = 300
  fireEvent.click(await screen.findByRole('button', { name: /Synthetic draft/ }))
  await screen.findByLabelText('Draft description in Markdown')
  expect(document.documentElement.scrollTop).toBe(0)
})

it('guards navigation while a scribe question remains unrecorded', async () => {
  const leadDraft = { ...draft, data: { ...draft.data, lead_id: 'lead1' } }
  fetchMock.mockImplementation((path: string) => {
    if (path.startsWith('/api/records?kind=draft')) return response({ items: [leadDraft], total: 1 })
    if (path === '/api/records/lead1') return response({ id: 'lead1', kind: 'lead', revision_id: 'l1', data: { finding_id: 'finding1' } })
    return base(path)
  })
  render(<App />); await screen.findByRole('heading', { name: 'Inbox' })
  fireEvent.click(screen.getByRole('link', { name: 'Drafts' }))
  fireEvent.click(await screen.findByRole('button', { name: /Synthetic draft/ }))
  const question = await screen.findByLabelText('Question for the finding owner')
  await waitFor(() => expect(question).toBeEnabled())
  fireEvent.change(question, { target: { value: 'Which host produced this evidence?' } })
  expect(await screen.findByText('Question not recorded. Connection unavailable; keep this page open.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('link', { name: 'Inbox' }))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(question).toHaveValue('Which host produced this evidence?')
  fireEvent.click(screen.getByRole('button', { name: 'Stay and edit' }))
  expect(question).toHaveValue('Which host produced this evidence?')
  fireEvent.click(screen.getByRole('link', { name: 'Inbox' }))
  fireEvent.click(screen.getByRole('button', { name: 'Discard and continue' }))
  await screen.findByRole('heading', { name: 'Inbox' })
  fireEvent.click(screen.getByRole('link', { name: 'Drafts' }))
  fireEvent.click(await screen.findByRole('button', { name: /Synthetic draft/ }))
  expect(await screen.findByLabelText('Question for the finding owner')).toHaveValue('')
})

it('closes a clean editor when browser history changes the route', async () => {
  render(<App />); await screen.findByRole('heading', { name: 'Inbox' })
  fireEvent.click(screen.getByRole('link', { name: 'Drafts' }))
  fireEvent.click(await screen.findByRole('button', { name: /Synthetic draft/ }))
  await screen.findByLabelText('Draft description in Markdown')
  await act(async () => { location.hash = '#/inbox'; dispatchEvent(new HashChangeEvent('hashchange')) })
  expect(await screen.findByRole('heading', { name: 'Inbox' })).toBeInTheDocument()
  expect(screen.queryByLabelText('Draft description in Markdown')).not.toBeInTheDocument()
})

it('keeps question text mounted when dirty browser history is declined', async () => {
  const leadDraft = { ...draft, data: { ...draft.data, lead_id: 'lead1' } }
  fetchMock.mockImplementation((path: string) => {
    if (path.startsWith('/api/records?kind=draft')) return response({ items: [leadDraft], total: 1 })
    if (path === '/api/records/lead1') return response({ id: 'lead1', kind: 'lead', revision_id: 'l1', data: { finding_id: 'finding1' } })
    return base(path)
  })
  render(<App />); await screen.findByRole('heading', { name: 'Inbox' })
  fireEvent.click(screen.getByRole('link', { name: 'Drafts' }))
  fireEvent.click(await screen.findByRole('button', { name: /Synthetic draft/ }))
  const question = await screen.findByLabelText('Question for the finding owner')
  await waitFor(() => expect(question).toBeEnabled())
  fireEvent.change(question, { target: { value: 'Preserve this exact question.' } })
  await act(async () => { location.hash = '#/inbox'; dispatchEvent(new HashChangeEvent('hashchange')) })
  expect(await screen.findByRole('dialog', { name: 'Work is not saved' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Stay and edit' }))
  expect(screen.getByLabelText('Question for the finding owner')).toHaveValue('Preserve this exact question.')
  expect(location.hash).toBe('#/drafts')
})

it('compares title, impact and evidence changes when a draft revision conflicts', async () => {
  const view = render(<DraftEditor selected={draft} onSaved={() => undefined} onDirtyChange={() => undefined} />)
  fireEvent.change(screen.getByLabelText('Draft description in Markdown'), { target: { value: 'Local report text' } })
  view.rerender(<DraftEditor selected={{ ...draft, revision_id: 'r2', data: { ...draft.data, title: 'Server title', impact: 'Server impact', evidence_ids: ['e2'] } }} onSaved={() => undefined} onDirtyChange={() => undefined} />)
  expect(await screen.findByText('Server impact')).toBeInTheDocument()
  expect(within(screen.getByLabelText('Editable field comparison')).getByText('Server title')).toBeInTheDocument()
})

it('does not call a pending or failed inbox clear and offers a retry', async () => {
  let fail!: (value: Response) => void
  fetchMock.mockImplementation((path: string) => path.includes('kind=lead') ? new Promise<Response>(resolve => { fail = resolve }) : base(path))
  render(<Inbox onOpen={() => undefined} />)
  expect(screen.getByText('Loading leads…')).toBeInTheDocument()
  expect(screen.queryByText('Inbox is clear')).not.toBeInTheDocument()
  await act(async () => fail(new Response(JSON.stringify({ detail: 'Queue unavailable' }), { status: 503, headers: { 'content-type': 'application/json' } })))
  expect(screen.getByRole('button', { name: 'Retry leads' })).toBeInTheDocument()
  expect(screen.queryByText('Inbox is clear')).not.toBeInTheDocument()
})

it('stops autosave retries when a delayed save conflicts with newer local typing', async () => {
  let finish!: (value: Response) => void
  fetchMock.mockImplementation((path: string, init?: RequestInit) => init?.method === 'PUT' ? new Promise<Response>(resolve => { finish = resolve }) : base(path))
  render(<DraftEditor selected={draft} onSaved={() => undefined} onDirtyChange={() => undefined} />)
  fireEvent.change(screen.getByLabelText('Draft description in Markdown'), { target: { value: 'First local edit' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save now' }))
  fireEvent.change(screen.getByLabelText('Draft description in Markdown'), { target: { value: 'Later local typing' } })
  await act(async () => finish(new Response(JSON.stringify({ detail: { current: { ...draft, revision_id: 'r2' } } }), { status: 409, headers: { 'content-type': 'application/json' } })))
  await act(async () => new Promise(resolve => setTimeout(resolve, 30)))
  expect(fetchMock.mock.calls.filter(call => call[1]?.method === 'PUT')).toHaveLength(1)
  expect(screen.getByRole('button', { name: 'Save now' })).toBeDisabled()
  expect(screen.getByLabelText('Draft description in Markdown')).toHaveValue('Later local typing')
})

it('records a scribe question beside a lead-backed draft using the existing question endpoint', async () => {
  fetchMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === '/api/records/lead1') return response({ id: 'lead1', kind: 'lead', revision_id: 'l1', data: { finding_id: 'finding1' } })
    if (path === '/api/questions' && init?.method === 'POST') return response({ id: 'q1', kind: 'question', revision_id: 'q1r', data: { text: 'Which asset was observed?', finding_id: 'finding1' } })
    return base(path)
  })
  render(<DraftEditor selected={{ ...draft, data: { ...draft.data, lead_id: 'lead1' } }} onSaved={() => undefined} onDirtyChange={() => undefined} />)
  const question = await screen.findByLabelText('Question for the finding owner')
  await waitFor(() => expect(question).toBeEnabled())
  fireEvent.change(question, { target: { value: 'Which asset was observed?' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record question' }))
  expect(await screen.findByText('Question recorded. Return it through Transfer when ready.')).toBeInTheDocument()
  const call = fetchMock.mock.calls.find(call => call[0] === '/api/questions')
  expect(JSON.parse(call?.[1].body)).toEqual({ finding_id: 'finding1', text: 'Which asset was observed?' })
})

it('distinguishes an evidence preview in flight from no selection', async () => {
  let finish!: (value: Response) => void
  fetchMock.mockImplementation((path: string) => path === '/api/evidence' ? response({ items: [{ id: 'e1', kind: 'upload', revision_id: 'e1r', data: { filename: 'Synthetic.txt' } }] }) : path === '/api/evidence/e1/preview' ? new Promise<Response>(resolve => { finish = resolve }) : base(path))
  render(<Evidence refreshKey={0} />)
  fireEvent.click(await screen.findByRole('button', { name: /Synthetic.txt/ }))
  expect(screen.getByText('Loading evidence preview…')).toBeInTheDocument()
  expect(screen.queryByText('Select evidence')).not.toBeInTheDocument()
  await act(async () => finish(response({ text: 'Synthetic evidence text', quarantined: false })))
  expect(screen.getByText('Synthetic evidence text')).toBeInTheDocument()
})
