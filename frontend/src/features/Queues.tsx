import React, { useEffect, useRef, useState } from "react";
import { ApiError, api, downloadJson, RecordItem, Session } from "../api";
import { ErrorMessage, Empty, UnsavedDialog } from "../components/common";
function Inbox({ onOpen, online = true, refreshKey = 0 }: { onOpen: (item: RecordItem) => void; online?: boolean; refreshKey?: number }) {
  const [items, setItems] = useState<RecordItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [opening, setOpening] = useState("");
  const limit = 100;
  const [loading, setLoading] = useState(true), [loaded, setLoaded] = useState(false), [retry, setRetry] = useState(0);
  const [filter, setFilter] = useState("");
  const visible = items.filter(item => String(item.data.title ?? "").toLowerCase().includes(filter.toLowerCase()));
  useEffect(() => {
    let cancelled = false; setLoading(true); setError(null);
    void api
      .get<{ items: RecordItem[]; total?: number }>(`/api/records?kind=lead&limit=${limit}&offset=${page * limit}`)
      .then((r) => { if (!cancelled) { setItems(r.items); setTotal(r.total ?? r.items.length); setLoaded(true); } })
      .catch(err => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [page, refreshKey, retry]);
  async function openLead(item: RecordItem) {
    if (!online || opening) return;
    setOpening(item.id);
    setError(null);
    try {
      onOpen(await api.post<RecordItem>(`/api/leads/${item.id}/draft`));
    } catch (err) {
      setError(err);
    } finally {
      setOpening("");
    }
  }
  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Lead queue</p>
          <h1>Inbox</h1>
          <p className="muted">
            Technical findings arrive here for evidence review and report
            drafting.
          </p>
        </div>
      </div>
      <ErrorMessage error={error} />{Boolean(error) && <button onClick={() => setRetry(value => value + 1)}>Retry leads</button>}{loading && <p role="status">{loaded ? "Refreshing leads…" : "Loading leads…"}</p>}
      <div className="queue-filter"><input aria-label="Filter leads on this page" placeholder="Filter titles on this page" value={filter} onChange={event => setFilter(event.target.value)} /><small>Filters apply to this page. Use pagination to inspect the full queue.</small></div>
      <p className="count" aria-live="polite">Showing {items.length} of {total} leads</p>
      {visible.length ? (
        <div className="queue">
          {visible.map((item) => (
            <button
              key={item.id}
              className="queue-row"
              disabled={!online || Boolean(opening)}
              onClick={() => void openLead(item)}
            >
              <span>
                <strong>{String(item.data.title || "Untitled finding")}</strong>
                <small>
                  {String(item.data.evidence_state || "evidence state unknown")}{" "}
                  · {String(item.data.writing_state || "writing state unknown")}
                </small>
              </span>
              <span className="arrow">{opening === item.id ? "Opening…" : "Open"}</span>
            </button>
          ))}
        </div>
      ) : loading || error || !loaded ? null : filter ? <Empty title="No matches on this page" body="Clear the filter or move to another page." /> : (
        <Empty
          title="Inbox is clear"
          body="Submitted Harbinger findings will appear here."
        />
      )}
      <div className="actions" aria-label="Inbox pages"><button onClick={() => setPage(page - 1)} disabled={page === 0}>Previous</button><span className="count">Page {page + 1}</span><button onClick={() => setPage(page + 1)} disabled={(page + 1) * limit >= total}>Next</button></div>
    </section>
  );
}

function Drafts({ onOpen, refreshKey = 0 }: { onOpen: (item: RecordItem | null) => void; refreshKey?: number }) {
  const [items, setItems] = useState<RecordItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const limit = 100;
  const [loading, setLoading] = useState(true), [loaded, setLoaded] = useState(false), [retry, setRetry] = useState(0);
  const [filter, setFilter] = useState("");
  const visible = items.filter(item => String(item.data.title ?? "").toLowerCase().includes(filter.toLowerCase()));
  useEffect(() => {
    let cancelled = false; setLoading(true); setError(null);
    void api
      .get<{ items: RecordItem[]; total?: number }>(`/api/records?kind=draft&limit=${limit}&offset=${page * limit}`)
      .then((r) => { if (!cancelled) { setItems(r.items); setTotal(r.total ?? r.items.length); setLoaded(true); } })
      .catch(err => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [page, refreshKey, retry]);
  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Writing desk</p>
          <h1>Drafts</h1>
          <p className="muted">
            Write in Markdown, keep the uncertainty visible, and save a revision
            before review.
          </p>
        </div>
        <button className="primary" onClick={() => onOpen(null)}>
          New draft
        </button>
      </div>
      <ErrorMessage error={error} />{Boolean(error) && <button onClick={() => setRetry(value => value + 1)}>Retry drafts</button>}{loading && <p role="status">{loaded ? "Refreshing drafts…" : "Loading drafts…"}</p>}
      <div className="queue-filter"><input aria-label="Filter drafts on this page" placeholder="Filter titles on this page" value={filter} onChange={event => setFilter(event.target.value)} /><small>Filters apply to this page. Use pagination to inspect the full queue.</small></div>
      <p className="count" aria-live="polite">Showing {items.length} of {total} drafts</p>
      {visible.length ? (
        <div className="queue">
          {visible.map((item) => (
            <button
              key={item.id}
              className="queue-row"
              onClick={() => onOpen(item)}
            >
              <span>
                <strong>{String(item.data.title || "Untitled draft")}</strong>
                <small>
                  {item.updated_at} · revision {item.revision_id.slice(0, 8)}
                </small>
              </span>
              <span className="arrow">Edit</span>
            </button>
          ))}
        </div>
      ) : loading || error || !loaded ? null : filter ? <Empty title="No matches on this page" body="Clear the filter or move to another page." /> : (
        <Empty
          title="No drafts yet"
          body="Create a draft when a finding is ready for report language."
        />
      )}
      <div className="actions" aria-label="Draft pages"><button onClick={() => setPage(page - 1)} disabled={page === 0}>Previous</button><span className="count">Page {page + 1}</span><button onClick={() => setPage(page + 1)} disabled={(page + 1) * limit >= total}>Next</button></div>
    </section>
  );
}
export { Inbox, Drafts };
