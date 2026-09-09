import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../src/api'

describe('Merlin API client', () => {
  it('sends same-origin credentials and CSRF on mutations', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'content-type': 'application/json' } }))
    const client = new ApiClient(); client.csrf = 'csrf'
    await client.post('/api/records', { kind: 'draft' })
    expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', method: 'POST' })
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get('x-csrf-token')).toBe('csrf')
    fetcher.mockRestore()
  })
})
