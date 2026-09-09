import React, { useEffect, useRef, useState } from "react";
import { ApiError, api, downloadJson, RecordItem, Session } from "../api";
import { ErrorMessage, Empty, UnsavedDialog } from "../components/common";
type TransferReceipt = { status: "imported" | "conflict"; imported: number; duplicates: number; conflicts: string[]; deferred: string[]; bundle_id: string; manifest_hash: string };
type TransferConflict = RecordItem & { data: Record<string, unknown> & { bundle_id?: string; manifest_hash?: string; state?: string; conflict_ids?: string[]; duplicate_ids?: string[]; deferred_ids?: string[]; incoming?: RecordItem[] }; local?: RecordItem[] }
type TransferReview = { review_hash: string; selected_record_ids: string[]; recipient: { id: string; name: string; origin: string }; records: (RecordItem & { selected?: boolean })[]; files: { id: string; size: number; sha256: string }[] };

function TransferRecordDiff({ id, incoming, local }: { id: string; incoming?: RecordItem; local?: RecordItem }) {
  const incomingData = incoming?.data ?? {};
  const localData = local?.data ?? {};
  const fields = Array.from(new Set([...Object.keys(incomingData), ...Object.keys(localData)])).filter((key) => JSON.stringify(incomingData[key]) !== JSON.stringify(localData[key])).sort();
  const value = (item: unknown) => { const text = typeof item === "string" ? item : JSON.stringify(item, null, 2) ?? "undefined"; return text.length > 360 ? <details><summary>{text.slice(0, 360)}…</summary><pre>{text}</pre></details> : <pre>{text}</pre>; };
  const metadata = (record?: RecordItem) => <dl><dt>Kind</dt><dd>{record?.kind ?? "missing local record"}</dd><dt>Record ID</dt><dd className="mono">{record?.id ?? id}</dd><dt>Revision</dt><dd className="mono">{record?.revision_id ?? "not returned"}</dd><dt>Source instance</dt><dd className="mono">{String(record?.data.source_instance ?? "not recorded")}</dd><dt>Source revision</dt><dd className="mono">{String(record?.data.source_revision_id ?? "not recorded")}</dd></dl>;
  return <details className="record-diff" open><summary>Conflicting record {id}: inspect all differing fields before accepting</summary><div className="diff-metadata"><div><strong>Incoming owner revision</strong>{metadata(incoming)}</div><div><strong>Current local revision</strong>{metadata(local)}</div></div>{fields.length ? <div className="diff-fields">{fields.map((field) => <div className="diff-field" key={field}><strong>{field}</strong><div><span>Incoming</span>{value(incomingData[field])}</div><div><span>Local</span>{value(localData[field])}</div></div>)}</div> : <p>No data fields differ; inspect identity, revision, and provenance above.</p>}</details>;
}

function TransferConflictReview({ canReview }: { canReview: boolean }) {
  const [items, setItems] = useState<TransferConflict[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [resolution, setResolution] = useState<{ decision: "keep_local" | "accept_incoming"; imported: number; duplicates: number; bundle_id: string; manifest_hash: string } | null>(null);
  async function load() {
    if (!canReview) return;
    setError(null);
    try {
      const result = await api.get<{ items?: TransferConflict[] }>("/api/transfers/conflicts");
      setItems(result.items ?? []);
      setLoaded(true);
    } catch (err) {
      setError(err);
    }
  }
  useEffect(() => { void load(); }, [canReview]);
  async function resolve(item: TransferConflict, decision: "keep_local" | "accept_incoming") {
    if (!canReview) return;
    setBusy(item.id);
    setError(null);
    try {
      const local = Array.isArray(item.local) ? item.local : [];
      const result = await api.post<TransferConflict>(`/api/transfers/conflicts/${item.id}/resolve`, {
        decision,
        conflict_revision_id: item.revision_id,
        manifest_hash: String(item.data.manifest_hash ?? ""),
        local_revisions: Object.fromEntries(local.map((record) => [record.id, record.revision_id])),
      });
      const data = result.data;
      const conflicts = Array.isArray(data.conflict_ids) ? data.conflict_ids : [];
      const deferred = Array.isArray(data.deferred_ids) ? data.deferred_ids : [];
      const duplicates = Array.isArray(data.duplicate_ids) ? data.duplicate_ids : [];
      setResolution({ decision, imported: decision === "accept_incoming" ? conflicts.length + deferred.length : 0, duplicates: duplicates.length, bundle_id: String(data.bundle_id ?? ""), manifest_hash: String(data.manifest_hash ?? "") });
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) await load();
      setError(err);
    } finally {
      setBusy("");
    }
  }
  if (!canReview) return null;
  return <section className="conflict-review" aria-labelledby="transfer-conflicts"><h2 id="transfer-conflicts">Transfer conflict review</h2><p className="muted">Keeping local records resolves the review while the encrypted bundle remains retained.</p>{resolution && <div className="delivery-status" role="status"><strong>{resolution.decision === "accept_incoming" ? "Incoming acceptance receipt" : "Local-resolution receipt"}</strong><span>{resolution.decision === "accept_incoming" ? `${resolution.imported} imported · ${resolution.duplicates} exact duplicates.` : "Local records kept; encrypted bundle retained."}</span><span className="mono">bundle {resolution.bundle_id} · manifest {resolution.manifest_hash}</span></div>}<ErrorMessage error={error} />{loaded && !items.length && <Empty title="No transfer conflicts" body="No encrypted bundles are waiting for host review." />}{items.map((item) => { const data = item.data; const conflicts = Array.isArray(data.conflict_ids) ? data.conflict_ids : []; const duplicates = Array.isArray(data.duplicate_ids) ? data.duplicate_ids : []; const deferred = Array.isArray(data.deferred_ids) ? data.deferred_ids : []; const incoming = Array.isArray(data.incoming) ? data.incoming : []; const local = Array.isArray(item.local) ? item.local : []; const reviewReady = typeof data.manifest_hash === "string" && local.length === conflicts.length && local.every((record) => Boolean(record.revision_id)); return <article className="panel" key={item.id}><div className="detail-head"><div><strong>{String(data.state ?? "needs_review")}</strong><p className="mono">bundle {String(data.bundle_id ?? "")}</p></div><span className="count">{conflicts.length} conflicts · {duplicates.length} duplicates · {deferred.length} deferred</span></div><p>Keeping local records retains the encrypted bundle for audit and review. Accepting incoming accepts the preserved owner revision and deferred evidence only when the server confirms this conflict is eligible.</p>{conflicts.map((id) => <TransferRecordDiff key={id} id={id} incoming={incoming.find((record) => record.id === id)} local={local.find((record) => record.id === id)} />)}<div className="actions"><button className="primary" disabled={data.state !== "needs_review" || Boolean(busy) || !reviewReady} onClick={() => void resolve(item, "keep_local")}>{busy === item.id ? "Resolving…" : "Keep local records"}</button><button disabled={data.state !== "needs_review" || Boolean(busy) || !reviewReady} onClick={() => void resolve(item, "accept_incoming")}>Accept incoming revision and deferred evidence</button></div></article>; })}</section>;
}

function Transfer({ online = true, role = "lead_scribe" }: { online?: boolean; role?: string }) {
  const [connections, setConnections] = useState<{
    peers: { id: string; name: string; recipient: string; status?: "enrolled" }[];
  }>({ peers: [] });
  const [recipient, setRecipient] = useState("");
  const [recordIds, setRecordIds] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState("");
  const [receipt, setReceipt] = useState<TransferReceipt | null>(null);
  const [review, setReview] = useState<TransferReview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const canTransfer = role === "lead_scribe";
  useEffect(() => {
    void api
      .get<typeof connections>("/api/connections")
      .then(setConnections)
      .catch(setError);
  }, []);
  const ids = () =>
    recordIds
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
  async function reviewTransfer() {
    if (!canTransfer) return;
    setError(null);
    setReview(null);
    try {
      const result = await api.post<TransferReview>("/api/transfers/preview", {
        record_ids: ids(),
        recipient_id: recipient,
      });
      setReview(result);
      setMessage("Transfer selection reviewed. Send or save this exact encrypted bundle.");
    } catch (err) {
      setError(err);
    }
  }
  async function sendPeer() {
    if (!canTransfer || !review) return;
    try {
      const result = await api.post<TransferReceipt>("/api/transfers/send", {
        record_ids: ids(),
        recipient_id: recipient,
        review_hash: review.review_hash,
      });
      setReceipt(result);
      setMessage(result.status === "conflict" ? `conflict: no records imported; bundle ${result.bundle_id} is held for review.` : `imported: receipt recorded for bundle ${result.bundle_id}.`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setReview(null);
      setError(err);
    }
  }
  async function exportRecords() {
    if (!review) return;
    try {
      const response = await fetch("/api/transfers/export", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": api.csrf ?? "",
        },
        body: JSON.stringify({ record_ids: ids(), recipient_id: recipient, review_hash: review.review_hash }),
      });
      if (!response.ok) {
        if (response.status === 409) setReview(null);
        throw new Error(`Export failed (${response.status})`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `merlin-transfer-${Date.now()}.age`;
      link.click();
      URL.revokeObjectURL(url);
      setMessage("Encrypted transfer exported.");
    } catch (err) {
      setError(err);
    }
  }
  async function importRecords() {
    if (!file) return;
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api.post<TransferReceipt>("/api/transfers/import", form);
      setReceipt(result);
      setMessage(result.status === "conflict" ? `conflict: no records imported; bundle ${result.bundle_id} is held for review.` : `imported: receipt recorded for bundle ${result.bundle_id}.`);
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Private handoff</p>
          <h1>Transfer</h1>
          <p className="muted">
            Move reviewed draft records through enrolled peer configurations or encrypted files.
          </p>
        </div>
      </div>
      <ErrorMessage error={error} />
      {!canTransfer && <div className="uncertainty" role="status">Lead-scribe access is required to start handoffs, export bundles, import transfer receipts, or review transfer conflicts.</div>}
      {message && <div className="notice">{message}</div>}
      {receipt && <div className={`delivery-status ${receipt.status}`} role="status"><strong>{receipt.status}</strong><span>{receipt.status === "conflict" ? "No records imported while this bundle is held." : `${receipt.imported} imported.`} {receipt.duplicates} exact duplicates · {receipt.conflicts.length} conflicts · {receipt.deferred.length} deferred</span><span className="mono">bundle {receipt.bundle_id} · manifest {receipt.manifest_hash}</span>{receipt.conflicts.length > 0 && <ul aria-label="Conflicting record IDs">{receipt.conflicts.map((id) => <li className="mono" key={id}>{id}</li>)}</ul>}{receipt.deferred.length > 0 && <ul aria-label="Deferred record IDs">{receipt.deferred.map((id) => <li className="mono" key={id}>{id}</li>)}</ul>}</div>}
      <div className="transfer-grid">
        <div className="panel">
          <h2>Peer delivery</h2>
          <label>
            Peer recipient
            <select
              value={recipient}
              onChange={(e) => { setRecipient(e.target.value); setReview(null); }}
            >
              <option value="">Choose enrolled peer</option>
              {connections.peers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · enrolled · {p.recipient}
                </option>
              ))}
            </select>
          </label>
          <label>
            Record IDs
            <input
              value={recordIds}
              onChange={(e) => { setRecordIds(e.target.value); setReview(null); }}
              placeholder="draft-id-1, draft-id-2"
            />
          </label>
          <button
            disabled={!canTransfer || !online || !recipient || !recordIds.trim()}
            onClick={() => void reviewTransfer()}
          >
            Review transfer
          </button>
          {review && <div className="delivery-status" role="status"><strong>Reviewed transfer selection</strong><span>{review.selected_record_ids.length} selected · {review.records.length} total records · {review.files.length} evidence files</span><span>Recipient: {review.recipient.name} at {review.recipient.origin}</span><ul aria-label="Reviewed transfer records">{review.records.map((item) => <li key={item.id}><strong>{item.selected ? "Selected" : "Dependency"}:</strong> {String(item.data.title ?? item.data.label ?? item.data.filename ?? item.id)} <span className="mono">{item.kind} · {item.revision_id}</span></li>)}</ul></div>}
          <div className="actions">
            <button
              className="primary"
              disabled={!canTransfer || !online || !review}
              onClick={() => void sendPeer()}
            >
              Start peer handoff
            </button>
            <button
              disabled={!canTransfer || !online || !review}
              onClick={() => void exportRecords()}
            >
              Save encrypted file
            </button>
          </div>
        </div>
        <div className="panel">
          <h2>File fallback</h2>
          <input
            aria-label="File fallback"
            type="file"
            accept=".age,application/octet-stream"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button
            className="primary"
            disabled={!canTransfer || !online || !file}
            onClick={() => void importRecords()}
          >
            Import and reconcile
          </button>
          <p className="hint">
            Conflicts remain visible for review. An uncertain delivery needs
            reconciliation before any new send.
          </p>
        </div>
      </div>
      <TransferConflictReview canReview={canTransfer} />
    </section>
  );
}
export { Transfer };
