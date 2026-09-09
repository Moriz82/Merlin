import { useEffect, useState } from 'react'
import { api, RecordItem } from '../../api'

export function DraftQuestions({ leadId, online, onDirtyChange }: { leadId: string; online: boolean; onDirtyChange?: (dirty: boolean) => void }) {
  const [findingId, setFindingId] = useState(''), [text, setText] = useState(''), [error, setError] = useState(''), [receipt, setReceipt] = useState('')
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false)
  useEffect(() => {
    let cancelled = false; setFindingId(''); setText(''); setError(''); setReceipt('')
    if (!leadId) return
    setLoading(true)
    void api.get<RecordItem>(`/api/records/${encodeURIComponent(leadId)}`).then(lead => {
      if (!cancelled) setFindingId(typeof lead.data?.finding_id === 'string' ? lead.data.finding_id : '')
    }).catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Source lead could not load.') }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [leadId])
  useEffect(() => { onDirtyChange?.(Boolean(text.trim())) }, [text, onDirtyChange])
  async function record() {
    if (!online || !findingId || !text.trim() || busy) return
    setBusy(true); setError(''); setReceipt('')
    try { await api.post('/api/questions', { finding_id: findingId, text: text.trim() }); setText(''); setReceipt('Question recorded. Return it through Transfer when ready.') }
    catch (err) { setError(err instanceof Error ? err.message : 'Question could not be recorded.') }
    finally { setBusy(false) }
  }
  return <fieldset><legend>Scribe questions</legend><p className="muted">Keep the missing detail close to the report text.</p>{loading && <p role="status">Loading the source lead…</p>}{error && <p role="alert">{error}</p>}{receipt && <p role="status">{receipt}</p>}
    <label>Question for the finding owner<textarea value={text} maxLength={10000} disabled={!findingId || busy} onChange={event => setText(event.target.value)} placeholder={leadId ? 'Describe the evidence or detail you need' : 'Open a received lead to link questions to a finding'} /></label>
    <button disabled={!online || !findingId || !text.trim() || busy} onClick={() => void record()}>{busy ? 'Recording question…' : 'Record question'}</button>
    {text.trim() && <small role="status">{online ? 'Question not recorded.' : 'Question not recorded. Connection unavailable; keep this page open.'}</small>}
    <small>{findingId ? 'Questions are saved separately from the draft.' : 'A source finding is required to record a question.'}</small>
  </fieldset>
}
