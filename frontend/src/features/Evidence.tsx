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
  const selectedRef = useRef<RecordItem | null>(null);
  const pageRequest = useRef(0);
  const pageBusy = useRef(false);
  const pageOffset = useRef(0);
  const [hasMore, setHasMore] = useState(false);
  const [pageError, setPageError] = useState(false);
  const [loading, setLoading] = useState(true), [previewLoading, setPreviewLoading] = useState(false), [retry, setRetry] = useState(0);
  async function loadPage(reset = false) {
    if (reset) {
      pageRequest.current++;
      request.current++;
      pageBusy.current = false;
      pageOffset.current = 0;
      setItems([]);
      setHasMore(false);
      setPreview(null);
      setPreviewLoading(Boolean(selectedRef.current));
    }
    if (pageBusy.current) return;
    const generation = pageRequest.current;
    const offset = pageOffset.current;
    pageBusy.current = true;
    setLoading(true);
    setPageError(false);
    setError(null);
    try {
      const result = await api.get<{ items: RecordItem[]; total?: number }>(offset ? `/api/evidence?limit=100&offset=${offset}` : "/api/evidence");
      if (generation !== pageRequest.current) return;
      const page = Array.isArray(result.items) ? result.items : [];
      pageOffset.current = offset + page.length;
      setItems(current => {
        const ids = new Set(current.map(item => item.id));
        return [...current, ...page.filter(item => {
          if (ids.has(item.id)) return false;
          ids.add(item.id);
          return true;
        })];
      });
      setHasMore(typeof result.total === "number" ? pageOffset.current < result.total && page.length > 0 : page.length === 100);
      if (reset && selectedRef.current) {
        const active = selectedRef.current;
        const validation = request.current;
        try {
          const current = page.find(item => item.id === active.id) ?? await api.get<RecordItem>(`/api/records/${active.id}`);
          if (generation !== pageRequest.current || validation !== request.current || selectedRef.current?.id !== active.id) return;
          if (current.kind !== 'upload') throw new Error('Selected evidence is no longer an upload.');
          selectedRef.current = current;
          setSelected(current);
          if (!page.some(item => item.id === current.id)) setItems(rows => [...rows, current]);
          await loadPreview(current);
        } catch (err) {
          if (generation === pageRequest.current && validation === request.current) { setError(err); setPreviewLoading(false); }
        }
      }
    } catch (err) {
      if (generation === pageRequest.current) { setError(err); setPageError(true); }
    } finally {
      if (generation === pageRequest.current) { pageBusy.current = false; setLoading(false); }
    }
  }
  useEffect(() => {
    void loadPage(true);
    return () => { pageRequest.current++; request.current++; };
  }, [refreshKey, retry]);
  async function loadPreview(item: RecordItem) {
    const generation = ++request.current;
    setPreviewLoading(true);
    setPreview(null);
    setError(null);
    try {
      const result = await api.get<{ text: string; quarantined: boolean; base_revision_id?: string; artifact_sha256?: string }>(`/api/evidence/${item.id}/preview`);
      if (generation === request.current) {
        if (result.base_revision_id && result.base_revision_id !== item.revision_id || result.artifact_sha256 && result.artifact_sha256 !== item.data.sha256) throw new Error('Evidence changed while its preview loaded. Refresh the selection.');
        setPreview(result);
      }
    } catch (err) {
      if (generation === request.current) setError(err);
    } finally { if (generation === request.current) setPreviewLoading(false); }
  }
  async function choose(item: RecordItem) {
    selectedRef.current = item;
    setSelected(item);
    setQuestion("");
    await loadPreview(item);
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
    if (!online || !preview || previewLoading || !findingId || !question.trim()) return;
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
      <ErrorMessage error={error} />{Boolean(error) && <button onClick={() => pageError && items.length ? void loadPage() : setRetry(value => value + 1)}>Retry evidence</button>}{loading && <p role="status">Loading evidence…</p>}
      <small>Showing {items.length} evidence records from the current host.</small>
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
                  disabled={!online || !findingId || !question.trim() || previewLoading}
                >
                  Ask
                </button>
              </div>
            </>
          ) : previewLoading ? <><p role="status">Loading evidence preview…</p>{question && <label>Evidence question<input value={question} disabled readOnly /></label>}</> : selected ? <><p className="muted">Evidence preview is unavailable. Retry this selection.</p>{question && <label>Evidence question<input value={question} disabled readOnly /></label>}</> : (
            <Empty
              title="Select evidence"
              body="Choose a record to inspect proof and ask a question."
            />
          )}
        </div>
      </div>
      {hasMore && <button type="button" disabled={loading} onClick={() => void loadPage()}>Load more evidence</button>}
    </section>
  );
}
export { Evidence };
