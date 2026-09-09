import React, { useEffect, useRef, useState } from "react";
import { ApiError, api, downloadJson, RecordItem, Session } from "../api";
import { ErrorMessage, Empty, UnsavedDialog } from "../components/common";
function Evidence({ refreshKey, online = true }: { refreshKey: number; online?: boolean }) {
  const [items, setItems] = useState<RecordItem[]>([]);
  const [selected, setSelected] = useState<RecordItem | null>(null);
  const [preview, setPreview] = useState<{
    text: string;
    quarantined: boolean;
  } | null>(null);
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<unknown>(null);
  const request = useRef(0);
  const [loading, setLoading] = useState(true), [previewLoading, setPreviewLoading] = useState(false), [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false; setLoading(true); setError(null);
    void api
      .get<{ items: RecordItem[] }>("/api/evidence")
      .then((r) => { if (!cancelled) setItems(r.items); })
      .catch(err => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; request.current++; };
  }, [refreshKey, retry]);
  async function choose(item: RecordItem) {
    const generation = ++request.current;
    setSelected(item);
    setPreviewLoading(true);
    setPreview(null);
    setQuestion("");
    setError(null);
    try {
      const result = await api.get<{ text: string; quarantined: boolean }>(`/api/evidence/${item.id}/preview`);
      if (generation === request.current) setPreview(result);
    } catch (err) {
      if (generation === request.current) setError(err);
    } finally { if (generation === request.current) setPreviewLoading(false); }
  }
  const findingId = selected
    ? String(
        selected.data.finding_id ??
          (Array.isArray(selected.data.finding_ids)
            ? (selected.data.finding_ids[0] ?? "")
            : ""),
      )
    : "";
  async function ask() {
    if (!online || !findingId || !question.trim()) return;
    try {
      await api.post("/api/questions", {
        finding_id: findingId,
        text: question,
      });
      setQuestion("");
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Source review</p>
          <h1>Evidence</h1>
          <p className="muted">
            Keep quoted proof and open questions beside the prose they support.
          </p>
        </div>
      </div>
      <ErrorMessage error={error} />{Boolean(error) && <button onClick={() => selected ? void choose(selected) : setRetry(value => value + 1)}>Retry evidence</button>}{loading && <p role="status">Loading evidence…</p>}
      <small>Showing up to 100 evidence records from the current host.</small>
      <div className="evidence-grid">
        <aside className="panel">
          {items.length ? (
            items.map((item) => (
              <button
                key={item.id}
                className={
                  selected?.id === item.id ? "queue-row selected" : "queue-row"
                }
                onClick={() => void choose(item)}
              >
                <span>
                  <strong>
                    {String(item.data.filename || item.data.title || item.id)}
                  </strong>
                  <small>{item.updated_at}</small>
                </span>
              </button>
            ))
          ) : loading || error ? null : (
            <Empty
              title="No evidence records"
              body="Evidence is added by the Harbinger merge."
            />
          )}
        </aside>
        <div className="panel evidence-view">
          {selected && preview ? (
            <>
              <h2>
                {String(
                  selected.data.filename || selected.data.title || selected.id,
                )}
              </h2>
              <p className="uncertainty">
                {preview.quarantined
                  ? "Quarantined text · treat as untrusted input."
                  : "Reviewed evidence."}
              </p>
              <pre>{preview.text}</pre>
              <div className="question">
                <label>
                  Evidence question
                <input
                  placeholder={
                    findingId
                      ? "Record a question for the finding owner"
                      : "Link this evidence to a finding to ask a question"
                  }
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  disabled={!findingId}
                />
                </label>
                <button
                  onClick={() => void ask()}
                  disabled={!online || !findingId || !question.trim()}
                >
                  Ask
                </button>
              </div>
            </>
          ) : previewLoading ? <p role="status">Loading evidence preview…</p> : selected ? <p className="muted">Evidence preview is unavailable. Retry this selection.</p> : (
            <Empty
              title="Select evidence"
              body="Choose a record to inspect proof and ask a question."
            />
          )}
        </div>
      </div>
    </section>
  );
}
export { Evidence };
