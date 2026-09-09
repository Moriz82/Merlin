const label = (key: string) => key.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase())
const value = (data: unknown) => data === undefined ? 'Not set' : typeof data === 'string' ? data || 'Empty' : JSON.stringify(data, null, 2)

export function RecordDiff({ local, server, fields }: { local: Record<string, unknown>; server: Record<string, unknown>; fields: readonly string[] }) {
  const changed = fields.filter(key => JSON.stringify(local[key]) !== JSON.stringify(server[key]))
  return <div className="record-diff" aria-label="Editable field comparison"><p>Compare all changed fields. Keeping the local version replaces these editable fields on the displayed server revision.</p>{changed.map(key => <div className="diff-field" key={key}><strong>{label(key)}</strong><div><span>Your version</span><pre>{value(local[key])}</pre></div><div><span>On the server</span><pre>{value(server[key])}</pre></div></div>)}{!changed.length && <p>Editable fields match. The server revision or source metadata changed.</p>}</div>
}
