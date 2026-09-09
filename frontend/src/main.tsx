import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, api, downloadJson, RecordItem, Session } from "./api";
import "./styles.css";

const nav = [
  ["inbox", "Inbox"],
  ["drafts", "Drafts"],
  ["evidence", "Evidence"],
  ["transfer", "Transfer"],
] as const;
const emptyDraft = {
  title: "",
  description: "",
  impact: "",
  remediation: "",
  references: "",
  evidence_ids: [] as string[],
  owner_id: "",
  lead_id: "",
};

function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <strong>Could not complete that request.</strong>
      <span>
        {error instanceof Error
          ? error.message
          : "The service returned an unreadable error."}
      </span>
    </div>
  );
}
function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}
function UnsavedDialog({ label, onStay, onDiscard }: { label: string; onStay: () => void; onDiscard: () => void }) {
  return <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="unsaved-title"><h2 id="unsaved-title">Draft is not saved</h2><p>Save in the editor before {label}, or discard the local edits.</p><button autoFocus className="primary" onClick={onStay}>Stay and edit</button><button onClick={onDiscard}>Discard and continue</button></div></div>;
}
function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<Session>("/api/login", { name, password });
      api.csrf = result.csrf;
      onLogin(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <div className="login-card">
        <p className="eyebrow">Merlin / report writing desk</p>
        <h1>Make the prose accountable.</h1>
        <p className="muted">
          A private lead queue for evidence-backed drafts and reviewed
          Ghostwriter delivery.
        </p>
        <form onSubmit={submit}>
          <label>
            Account name
            <input
              required
              autoComplete="username"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            Password
            <input
              required
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <ErrorMessage error={error} />
          <button className="primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <small>LAN mode · drafts stay in the engagement</small>
      </div>
    </main>
  );
}
function useRoute() {
  const [route, setRoute] = useState(
    () => location.hash.replace(/^#\/?/, "") || "inbox",
  );
  useEffect(() => {
    const f = () => setRoute(location.hash.replace(/^#\/?/, "") || "inbox");
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  return route;
}
const evidencePath = /^\/api\/evidence\/([0-9a-f]{8}-[0-9a-f-]{27,})\/render$/i;
function safeEvidencePath(value: string, allowed: Set<string>) {
  const match = evidencePath.exec(value);
  return Boolean(match && allowed.has(match[1]));
}
function Markdown({ value, evidenceIds = [] }: { value: string; evidenceIds?: string[] }) {
  const allowed = new Set(evidenceIds);
  return (
    <div className="markdown">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => href && (href.startsWith('#') || safeEvidencePath(href, allowed)) ? <a href={href}>{children}</a> : <span className="unsafe-link" role="note">Unsafe link omitted.</span>,
          img: ({ src, alt }) => src && safeEvidencePath(src, allowed) ? <img src={src} alt={alt ?? 'Reviewed evidence'} onError={(event) => { event.currentTarget.replaceWith(Object.assign(document.createElement('span'), { className: 'omitted-image', textContent: 'Evidence image could not load. Keep the evidence selection and ask the host to enable its render endpoint.' })); }} /> : <span className="omitted-image" role="note">Embedded image omitted. Attach reviewed evidence instead.</span>,
        }}
      >
        {value || "*Nothing written yet.*"}
      </ReactMarkdown>
    </div>
  );
}
function Toolbar({
  onInsert,
  onEvidence,
  hasSelectedEvidence = false,
  disabled = false,
}: {
  onInsert: (before: string, after?: string) => void;
  onEvidence?: () => void;
  hasSelectedEvidence?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="toolbar" aria-label="Markdown toolbar">
      <button type="button" aria-label="Bold selected text" disabled={disabled} onClick={() => onInsert("**", "**")}>
        <strong>B</strong>
      </button>
      <button type="button" aria-label="Italicize selected text" disabled={disabled} onClick={() => onInsert("*", "*")}>
        <em>I</em>
      </button>
      <button type="button" disabled={disabled} onClick={() => onInsert("## ")}>
        Heading
      </button>
      <button type="button" disabled={disabled} onClick={() => onInsert("- ")}>
        List
      </button>
      <button type="button" disabled={disabled} onClick={() => onInsert("> ")}>
        Quote
      </button>
      {onEvidence && <button type="button" disabled={disabled || !hasSelectedEvidence} onClick={onEvidence}>Insert evidence</button>}
      <span className="toolbar-note">Markdown</span>
    </div>
  );
}
function DraftEditor({
  selected,
  onSaved,
  onDirtyChange,
  lockedReason = "",
  online = true,
}: {
  selected: RecordItem | null;
  onSaved: (item: RecordItem) => void;
  onDirtyChange: (dirty: boolean) => void;
  lockedReason?: string;
  online?: boolean;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>(
    selected?.data ?? emptyDraft,
  );
  const draftRef = useRef(draft);
  const recordRef = useRef<RecordItem | null>(selected?.id ? selected : null);
  const [dirty, setDirty] = useState(!selected?.id);
  const [view, setView] = useState<"write" | "split" | "preview">("split");
  const [error, setError] = useState<unknown>(null);
  const [conflict, setConflict] = useState<{
    message: string;
    current?: RecordItem;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [evidence, setEvidence] = useState<RecordItem[]>([]);
  const [evidenceError, setEvidenceError] = useState<unknown>(null);
  const savingRef = useRef(false);
  const saveRequested = useRef(false);
  const timer = useRef<number | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const selectedEvidenceIds = Array.isArray(draft.evidence_ids) ? draft.evidence_ids.filter((id): id is string => typeof id === "string") : [];
  useEffect(() => {
    void api.get<{ items?: RecordItem[] }>("/api/evidence").then((result) => setEvidence(Array.isArray(result.items) ? result.items : [])).catch(setEvidenceError);
  }, [selected?.id]);
  useEffect(() => {
    if (selected?.id && selected.id === recordRef.current?.id) {
      if (selected.revision_id === recordRef.current.revision_id) return;
      if (dirty) {
        setConflict({
          message: "The server changed this draft. Your text remains local until you compare and save it.",
          current: selected,
        });
        return;
      }
      recordRef.current = selected;
      draftRef.current = selected.data;
      setDraft(selected.data);
      setDirty(false);
      setConflict(null);
      return;
    }
    const next = selected?.data ?? emptyDraft;
    recordRef.current = selected?.id ? selected : null;
    draftRef.current = next;
    setDraft(next);
    setDirty(!selected?.id);
    setConflict(null);
  }, [selected?.id, selected?.revision_id]);
  useEffect(() => {
    onDirtyChange(dirty);
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  }, [dirty, onDirtyChange]);
  const description = String(draft.description ?? "");
  function change(key: string, value: unknown) {
    const next = { ...draftRef.current, [key]: value };
    draftRef.current = next;
    setDraft(next);
    setDirty(true);
    setConflict(null);
  }
  function insert(before: string, after = "") {
    const element = textarea.current;
    if (!element) return;
    const start = element.selectionStart;
    const end = element.selectionEnd;
    const value =
      description.slice(0, start) +
      before +
      description.slice(start, end) +
      after +
      description.slice(end);
    change("description", value);
    requestAnimationFrame(() => {
      element.focus();
      element.setSelectionRange(start + before.length, end + before.length);
    });
  }
  function toggleEvidence(id: string) {
    change("evidence_ids", selectedEvidenceIds.includes(id) ? selectedEvidenceIds.filter((value) => value !== id) : [...selectedEvidenceIds, id]);
  }
  function insertEvidence() {
    const id = selectedEvidenceIds[0];
    if (!id) return;
    insert(`![Reviewed evidence](/api/evidence/${id}/render)`);
  }
  async function save() {
    if (lockedReason || !online) return;
    if (savingRef.current) {
      saveRequested.current = true;
      return;
    }
    savingRef.current = true;
    saveRequested.current = false;
    setSaving(true);
    setError(null);
    const payload = draftRef.current;
    try {
      const persisted = recordRef.current;
      const item = persisted?.id
        ? await api.put<RecordItem>(`/api/records/${persisted.id}`, {
            base_revision_id: persisted.revision_id,
            data: payload,
          })
        : await api.post<RecordItem>("/api/records", {
            kind: "draft",
            data: payload,
          });
      recordRef.current = item;
      onSaved(item);
      if (JSON.stringify(draftRef.current) === JSON.stringify(payload)) {
        setDirty(false);
      } else {
        setDirty(true);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409)
        setConflict({
          message: err.message,
          current: (err.detail as { current?: RecordItem })?.current,
        });
      else setError(err);
    } finally {
      savingRef.current = false;
      setSaving(false);
      if (
        saveRequested.current ||
        JSON.stringify(draftRef.current) !== JSON.stringify(payload)
      )
        window.setTimeout(() => void save(), 0);
    }
  }
  useEffect(() => {
    if (!dirty || lockedReason || !online) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(), 1000);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [draft, lockedReason, online]);
  function text(key: string) {
    return String(draft[key] ?? "");
  }
  return (
    <div className="desk-editor">
      <div className="editor-head">
        <div>
          <p className="eyebrow">
            {selected
              ? `Revision ${selected.revision_id.slice(0, 8)}`
              : "Unsaved draft"}
          </p>
          <h2>{text("title") || "Untitled report section"}</h2>
        </div>
        <div className="actions">
          <button
            onClick={() =>
              downloadJson("merlin-draft.json", { kind: "draft", data: draft })
            }
          >
            Save draft file
          </button>
          <button
            className="primary"
            onClick={() => void save()}
            disabled={saving || !dirty || Boolean(lockedReason) || !online}
          >
            {saving ? "Saving…" : "Save now"}
          </button>
        </div>
      </div>
      <ErrorMessage error={error} />
      {lockedReason && <div className="uncertainty" role="status"><strong>Draft locked</strong><span>{lockedReason}</span></div>}
      {conflict && (
        <div className="conflict">
          <strong>Another revision was saved.</strong>
          <span>
            {conflict.message} Your text remains in this editor. Review the
            current version below before choosing what to keep.
          </span>
          {conflict.current && (
            <details>
              <summary>
                Current server version {conflict.current.revision_id}
              </summary>
              <Markdown value={String(conflict.current.data.description ?? "")} evidenceIds={Array.isArray(conflict.current.data.evidence_ids) ? conflict.current.data.evidence_ids.filter((id): id is string => typeof id === "string") : []} />
            </details>
          )}
          <button
            onClick={() => {
              if (conflict.current) {
                recordRef.current = conflict.current;
                draftRef.current = conflict.current.data;
                setDraft(conflict.current.data);
                setDirty(false);
                onSaved(conflict.current);
              }
              setConflict(null);
            }}
          >
            Use current version
          </button>
          {conflict.current && <button onClick={() => {
            recordRef.current = conflict.current ?? recordRef.current;
            onSaved(conflict.current as RecordItem);
            setConflict(null);
            setDirty(true);
            void save();
          }}>Keep my text on current revision</button>}
        </div>
      )}
      <label>
        Title
        <input
          disabled={Boolean(lockedReason)}
          value={text("title")}
          onChange={(e) => change("title", e.target.value)}
        />
      </label>
      <div className="preview-tabs view-switch" aria-label="Editor view">
        {(["write", "split", "preview"] as const).map((mode) => <button key={mode} aria-pressed={view === mode} onClick={() => setView(mode)}>{mode[0].toUpperCase() + mode.slice(1)}</button>)}
      </div>
      <div className={`writing-grid mode-${view}`}>
        {view !== "preview" && <div className="editor-pane">
          <Toolbar onInsert={insert} onEvidence={insertEvidence} hasSelectedEvidence={selectedEvidenceIds.length > 0} disabled={Boolean(lockedReason)} />
          <textarea
            ref={textarea}
            disabled={Boolean(lockedReason)}
            rows={18}
            value={description}
            onChange={(e) => change("description", e.target.value)}
            aria-label="Draft description in Markdown"
          />
        </div>}
        {view !== "write" && <div className="preview-pane" aria-label="Markdown preview"><Markdown value={description} evidenceIds={selectedEvidenceIds} /></div>}
      </div>
      <div className="form-grid">
        <label>
          Impact
          <textarea
            disabled={Boolean(lockedReason)}
            rows={4}
            value={text("impact")}
            onChange={(e) => change("impact", e.target.value)}
          />
        </label>
        <label>
          Remediation
          <textarea
            disabled={Boolean(lockedReason)}
            rows={4}
            value={text("remediation")}
            onChange={(e) => change("remediation", e.target.value)}
          />
        </label>
        <label>
          References
          <textarea
            disabled={Boolean(lockedReason)}
            rows={3}
            value={text("references")}
            onChange={(e) => change("references", e.target.value)}
          />
        </label>
        <label>
          Lead ID
          <input
            readOnly
            value={text("lead_id")}
          />
        </label>
      </div>
      <fieldset>
        <legend>Reviewed evidence</legend>
        {Boolean(evidenceError) && <ErrorMessage error={evidenceError} />}
        {evidence.length ? evidence.map((item) => <label key={item.id}><input type="checkbox" disabled={Boolean(lockedReason)} checked={selectedEvidenceIds.includes(item.id)} onChange={() => toggleEvidence(item.id)} />{String(item.data.filename ?? item.id)}</label>) : <small>No reviewed evidence records are available.</small>}
      </fieldset>
      <p className="save-state">
        {lockedReason ? `Not saved · draft locked by ${lockedReason}. Local text remains only in this editor; save a draft file.` : !online ? "Not saved · connection lost. Keep this page open or save a draft file." : saving ? "Saving…" : conflict ? "Conflict · choose a version before saving." : dirty ? "Autosaving in one second · unsaved changes stay local until saved." : "Saved revision is current."}
      </p>
    </div>
  );
}

function Inbox({ onOpen, online = true, refreshKey = 0 }: { onOpen: (item: RecordItem) => void; online?: boolean; refreshKey?: number }) {
  const [items, setItems] = useState<RecordItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [opening, setOpening] = useState("");
  const limit = 100;
  useEffect(() => {
    void api
      .get<{ items: RecordItem[]; total?: number }>(`/api/records?kind=lead&limit=${limit}&offset=${page * limit}`)
      .then((r) => { setItems(r.items); setTotal(r.total ?? r.items.length); })
      .catch(setError);
  }, [page, refreshKey]);
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
      <ErrorMessage error={error} />
      <p className="count" aria-live="polite">Showing {items.length} of {total} leads</p>
      {items.length ? (
        <div className="queue">
          {items.map((item) => (
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
      ) : (
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
  useEffect(() => {
    void api
      .get<{ items: RecordItem[]; total?: number }>(`/api/records?kind=draft&limit=${limit}&offset=${page * limit}`)
      .then((r) => { setItems(r.items); setTotal(r.total ?? r.items.length); })
      .catch(setError);
  }, [page, refreshKey]);
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
      <ErrorMessage error={error} />
      <p className="count" aria-live="polite">Showing {items.length} of {total} drafts</p>
      {items.length ? (
        <div className="queue">
          {items.map((item) => (
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
      ) : (
        <Empty
          title="No drafts yet"
          body="Create a draft when a finding is ready for report language."
        />
      )}
      <div className="actions" aria-label="Draft pages"><button onClick={() => setPage(page - 1)} disabled={page === 0}>Previous</button><span className="count">Page {page + 1}</span><button onClick={() => setPage(page + 1)} disabled={(page + 1) * limit >= total}>Next</button></div>
    </section>
  );
}

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
  useEffect(() => {
    void api
      .get<{ items: RecordItem[] }>("/api/evidence")
      .then((r) => setItems(r.items))
      .catch(setError);
  }, [refreshKey]);
  async function choose(item: RecordItem) {
    const generation = ++request.current;
    setSelected(item);
    setPreview(null);
    setQuestion("");
    setError(null);
    try {
      const result = await api.get<{ text: string; quarantined: boolean }>(`/api/evidence/${item.id}/preview`);
      if (generation === request.current) setPreview(result);
    } catch (err) {
      if (generation === request.current) setError(err);
    }
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
      <ErrorMessage error={error} />
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
          ) : (
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
          ) : (
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

type DeliveryProposal = {
  proposal_id: string;
  proposal_hash: string;
  adapter_id: string;
  draft_id: string;
  draft_revision_id: string;
  destination_origin: string;
  report_id: string;
  payload_hash: string;
  payload: Record<string, unknown>;
  evidence_manifest: unknown[];
  attachment_state: string;
};

function DraftFlow({
  selected,
  onBack,
  onDirtyChange,
  online = true,
  role = "lead_scribe",
  refreshKey = 0,
}: {
  selected: RecordItem | null;
  onBack: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  online?: boolean;
  role?: string;
  refreshKey?: number;
}) {
  const [item, setItem] = useState(selected);
  const [dirty, setDirty] = useState(!selected?.id);
  const [reportId, setReportId] = useState("");
  const [ghostwriterStatus, setGhostwriterStatus] = useState<"verified" | "configured_unverified" | "not_configured">("not_configured");
  const [delivery, setDelivery] = useState<RecordItem | null>(null);
  const [proposal, setProposal] = useState<DeliveryProposal | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [remoteId, setRemoteId] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const canLead = role === "lead_scribe";
  useEffect(() => {
    void api.get<{ ghostwriter: { report_id?: string; status?: "verified" | "configured_unverified" | "not_configured" } }>("/api/connections")
      .then((result) => {
        const nextReportId = String(result.ghostwriter.report_id ?? "");
        setReportId(nextReportId);
        setProposal((current) => current?.report_id === nextReportId ? current : null);
        setGhostwriterStatus(result.ghostwriter.status ?? "not_configured");
      })
      .catch(setError);
  }, [refreshKey]);
  useEffect(() => {
    const draftId = item?.id;
    if (!draftId) return;
    let cancelled = false;
    setProposal(null);
    const forDraft = (records: RecordItem[]) => records
      .filter((candidate) => String(candidate.data.draft_id ?? '') === draftId)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0] ?? null;
    if (!refreshKey) {
      void api.get<{ items: RecordItem[] }>('/api/deliveries').then((result) => {
        if (!cancelled) setDelivery(forDraft(result.items));
      }).catch((err) => { if (!cancelled) setError(err); });
      return () => { cancelled = true; };
    }
    setRefreshing(true);
    void Promise.all([
      api.get<RecordItem>(`/api/records/${draftId}`),
      api.get<{ items: RecordItem[] }>('/api/deliveries'),
    ]).then(([current, deliveries]) => {
      if (cancelled) return;
      setItem(current);
      setDelivery(forDraft(deliveries.items));
    }).catch((err) => { if (!cancelled) setError(err); }).finally(() => { if (!cancelled) setRefreshing(false); });
    return () => { cancelled = true; };
  }, [item?.id, refreshKey]);
  async function preview() {
    if (!canLead || !online || !item?.id || dirty || !reportId.trim() || busy || refreshing || reconciling || delivery && ['reviewed', 'sending', 'uncertain', 'reconciling', 'delivered'].includes(String(delivery.data.status))) return;
    setBusy(true);
    setError(null);
    try {
      setProposal(await api.post<DeliveryProposal>("/api/deliveries/preview", {
        draft_id: item.id,
        draft_revision_id: item.revision_id,
        report_id: reportId.trim(),
      }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setProposal(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  async function review() {
    if (!canLead || !online || !item || !item.id || dirty || !proposal || proposal.draft_id !== item.id || proposal.draft_revision_id !== item.revision_id || proposal.report_id !== reportId.trim() || refreshing || reconciling || delivery && ['reviewed', 'sending', 'uncertain', 'reconciling', 'delivered'].includes(String(delivery.data.status))) return;
    setBusy(true);
    try {
      const result = await api.post<RecordItem>("/api/deliveries/review", {
        draft_id: item.id,
        draft_revision_id: item.revision_id,
        report_id: reportId.trim(),
        proposal_id: proposal.proposal_id,
        proposal_hash: proposal.proposal_hash,
      });
      setDelivery(result);
      setProposal(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setProposal(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  async function send() {
    const payloadHash = String(delivery?.data.payload_hash ?? "");
    if (!canLead || !online || !delivery || dirty || refreshing || reconciling || delivery.data.status !== "reviewed" || !payloadHash) return;
    setBusy(true);
    try {
      setDelivery(
        await api.post<RecordItem>(`/api/deliveries/${delivery.id}/send`, {
          delivery_revision_id: delivery.revision_id,
          payload_hash: payloadHash,
        }),
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setProposal(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  async function reconcile() {
    const payloadHash = String(delivery?.data.payload_hash ?? "");
    if (!canLead || !online || !delivery || refreshing || reconciling || delivery.data.status !== "uncertain" || !payloadHash || !/^\d+$/.test(remoteId)) return;
    setBusy(true);
    setReconciling(true);
    setError(null);
    try {
      setDelivery(await api.post<RecordItem>(`/api/deliveries/${delivery.id}/reconcile`, {
        delivery_revision_id: delivery.revision_id,
        payload_hash: payloadHash,
        remote_id: Number(remoteId),
      }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setProposal(null);
      setError(err);
    } finally {
      setBusy(false);
      setReconciling(false);
    }
  }
  const deliveryStatus = String(delivery?.data.status ?? "");
  const proposalPayload = proposal?.payload ?? {};
  const lockedReason = refreshing
    ? 'Checking the current server revision and delivery state.'
    : reconciling
    ? 'Ghostwriter receipt reconciliation is in progress.'
    : ['sending', 'uncertain', 'reconciling', 'delivered'].includes(deliveryStatus)
    ? deliveryStatus === 'uncertain'
      ? 'Ghostwriter receipt is uncertain. Reconcile the remote finding before you edit or send again.'
      : deliveryStatus === 'reconciling'
        ? 'Ghostwriter receipt reconciliation is in progress.'
        : deliveryStatus === 'delivered'
        ? 'This revision was delivered. Create a separate update proposal for later evidence.'
        : 'Ghostwriter delivery is in progress.'
    : '';
  return (
    <section>
      <button className="back" onClick={onBack}>
        Back to drafts
      </button>
      <DraftEditor
        selected={item}
        lockedReason={lockedReason}
        online={online}
        onSaved={(result) => {
          setItem(result);
          setDirty(false);
          setProposal(null);
          if (delivery?.data.status === 'reviewed') setDelivery(null);
        }}
        onDirtyChange={(value) => { setDirty(value); if (value) setProposal(null); onDirtyChange?.(value); }}
      />
      <ErrorMessage error={error} />
      <div className="review-panel">
        <div>
          <p className="eyebrow">Ghostwriter gate</p>
          <h2>Review before delivery</h2>
          <p className="muted">
            The delivery payload is tied to a reviewed draft revision and hash.
            Uncertain status stays visible.
          </p>
          <label className="report-field">
            Ghostwriter report ID
            <input
              value={reportId}
              readOnly
              placeholder="Configure a Ghostwriter report on this host"
            />
          </label>
          <small>Ghostwriter status: {ghostwriterStatus.replace(/_/g, " ")}.</small>
          {!canLead && <p className="uncertainty" role="status">Lead-scribe access is required to review, send, or reconcile Ghostwriter delivery.</p>}
        </div>
        <div className="actions">
          <button
            onClick={() => void preview()}
            disabled={!canLead || !online || !item?.id || dirty || busy || refreshing || reconciling || !reportId.trim() || ['reviewed', 'sending', 'uncertain', 'reconciling', 'delivered'].includes(deliveryStatus)}
          >
            Preview delivery
          </button>
          <button
            onClick={() => void review()}
            disabled={!canLead || !online || !item?.id || dirty || busy || refreshing || reconciling || !proposal || proposal.draft_id !== item?.id || proposal.draft_revision_id !== item?.revision_id || proposal.report_id !== reportId.trim() || ['reviewed', 'sending', 'uncertain', 'reconciling', 'delivered'].includes(deliveryStatus)}
          >
            Mark reviewed
          </button>
          {delivery && (
            <button
              className="primary"
              onClick={() => void send()}
              disabled={!canLead || !online || busy || dirty || refreshing || reconciling || delivery.data.status !== "reviewed" || !delivery.data.payload_hash}
            >
              Send to Ghostwriter
            </button>
          )}
        </div>
        {proposal && <div className="delivery-status" aria-label="Delivery preview"><strong>Previewed delivery</strong><span>Destination: {proposal.destination_origin}</span><span>Report: {proposal.report_id}</span><span>Title: {String(proposalPayload.title ?? item?.data.title ?? 'Untitled draft')}</span><span>Evidence: {proposal.evidence_manifest.length}</span><details className="payload-preview" aria-label="Exact Ghostwriter payload"><summary>Exact Ghostwriter payload</summary><dl><dt>Title</dt><dd>{String(proposalPayload.title ?? '')}</dd><dt>Description</dt><dd><pre>{String(proposalPayload.description ?? '')}</pre></dd><dt>Impact</dt><dd><pre>{String(proposalPayload.impact ?? '')}</pre></dd><dt>Mitigation</dt><dd><pre>{String(proposalPayload.mitigation ?? '')}</pre></dd><dt>References</dt><dd><pre>{String(proposalPayload.references ?? '')}</pre></dd><dt>Report ID</dt><dd>{String(proposalPayload.reportId ?? '')}</dd><dt>Severity ID</dt><dd>{String(proposalPayload.severityId ?? '')}</dd><dt>Finding type ID</dt><dd>{String(proposalPayload.findingTypeId ?? '')}</dd><dt>Complete</dt><dd>{String(proposalPayload.complete ?? '')}</dd><dt>Evidence attachment state</dt><dd>{proposal.attachment_state}</dd><dt>Extra fields</dt><dd><pre>{JSON.stringify(proposalPayload.extraFields ?? {}, null, 2)}</pre></dd><dt>Proposal hash</dt><dd className="mono">{proposal.proposal_hash}</dd><dt>Payload hash</dt><dd className="mono">{proposal.payload_hash}</dd></dl></details></div>}
        {delivery && (
          <><p className={`delivery-status ${String(delivery.data.status)}`}>
            Delivery: <strong>{String(delivery.data.status)}</strong>
            {delivery.data.payload_hash
              ? ` · hash ${String(delivery.data.payload_hash)}`
              : ""}
          </p>{String(delivery.data.attachment_state ?? "") === "manual_required" && <p className="uncertainty" role="status">Finding text and evidence manifest are delivered. Selected evidence files must be attached in Ghostwriter; they were not uploaded.</p>}</>
        )}
        {deliveryStatus === 'uncertain' && <div className="reconcile"><label>Remote Ghostwriter finding ID<input inputMode="numeric" value={remoteId} onChange={(event) => setRemoteId(event.target.value)} /></label><button disabled={!canLead || !online || busy || refreshing || reconciling || !delivery?.data.payload_hash || !/^\d+$/.test(remoteId)} onClick={() => void reconcile()}>{reconciling ? 'Reconciling receipt…' : 'Reconcile receipt'}</button></div>}
      </div>
    </section>
  );
}

function Shell({
  session,
  onLogout,
}: {
  session: Session;
  onLogout: () => void;
}) {
  const route = useRoute();
  const [draft, setDraft] = useState<RecordItem | null | undefined>(undefined);
  const [refresh, setRefresh] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [connection, setConnection] = useState<"connecting" | "connected" | "offline">("connecting");
  const [lastSync, setLastSync] = useState("");
  const [pending, setPending] = useState<{ label: string; run: () => void } | null>(null);
  useEffect(() => {
    const events = new EventSource("/api/events");
    events.onopen = () => { setConnection("connected"); setLastSync(new Date().toISOString()); };
    events.onerror = () => setConnection("offline");
    const f = () => { setConnection("connected"); setLastSync(new Date().toISOString()); setRefresh((v) => v + 1); };
    events.addEventListener("change", f);
    events.addEventListener("reset", f);
    return () => events.close();
  }, []);
  useEffect(() => {
    if (draft !== undefined && dirty && route !== "drafts") {
      const target = route;
      setPending({ label: `opening ${target}`, run: () => { setDraft(undefined); location.hash = `#/${target}`; } });
      location.hash = "#/drafts";
    }
  }, [route, draft, dirty]);
  async function logoutNow() {
    await api.post("/api/logout");
    api.csrf = null;
    onLogout();
  }
  function guarded(label: string, run: () => void) {
    if (dirty) setPending({ label, run });
    else run();
  }
  function navigate(target: string) {
    guarded(`opening ${target}`, () => { setDraft(undefined); location.hash = `#/${target}`; });
  }
  let content: React.ReactNode;
  if (draft !== undefined)
    content = (
      <DraftFlow
        selected={draft}
        online={connection === "connected"}
        role={session.user?.role ?? ""}
        refreshKey={refresh}
        onDirtyChange={setDirty}
        onBack={() => navigate("drafts")}
      />
    );
  else if (route === "inbox")
    content = (
      <Inbox
        online={connection === "connected"}
        refreshKey={refresh}
        onOpen={(item) => {
          setDraft(item);
          location.hash = "#/drafts";
        }}
      />
    );
  else if (route === "evidence") content = <Evidence refreshKey={refresh} online={connection === "connected"} />;
  else if (route === "transfer") content = <Transfer online={connection === "connected"} role={session.user?.role ?? ""} />;
  else content = <Drafts refreshKey={refresh} onOpen={(item) => setDraft(item)} />;
  return (
    <div className="app">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="topbar">
        <a className="brand" href="#/inbox" onClick={(event) => { event.preventDefault(); navigate("inbox"); }}>
          <span className="brand-seal">M</span>
          <span>Merlin</span>
        </a>
        <span className="desk-note">writing desk / private LAN</span>
        <div className="top-actions">
          <span><i className={`status-dot ${connection}`} />{connection}{lastSync ? ` · ${new Date(lastSync).toLocaleTimeString()}` : ""}</span>
          <span>{session.user?.name}</span>
          <button disabled={connection !== "connected"} onClick={() => guarded("signing out", () => void logoutNow())}>Sign out</button>
        </div>
      </header>
      <div className="body">
        <nav className="sidebar" aria-label="Primary navigation">
          <p className="sidebar-title">{session.engagement.name}</p>
          {nav.map(([key, label]) => (
            <a
              className={route === key ? "active" : ""}
              aria-current={route === key ? "page" : undefined}
              href={`#/${key}`}
              key={key}
              onClick={(event) => { event.preventDefault(); navigate(key); }}
            >
              {label}
            </a>
          ))}
          <div className="sidebar-foot">
            <span>{session.user?.role}</span>
            <small>Ghostwriter delivery is review-gated.</small>
          </div>
        </nav>
        <main className="content" id="main-content">{connection !== "connected" && <div className="connection-banner" role="status">{connection === "offline" ? "Connection lost." : "Connection not confirmed."} Unsaved text remains in memory. Server writes are disabled. Last sync: {lastSync || "not yet"}.</div>}{content}</main>
      </div>
      {pending && <UnsavedDialog label={pending.label} onStay={() => setPending(null)} onDiscard={() => { const action = pending.run; setPending(null); setDirty(false); action(); }} />}
    </div>
  );
}
function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    void api
      .get<Session>("/api/session")
      .then((value) => {
        api.csrf = value.csrf;
        setSession(value);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) setSession(null);
        else setError(err);
      })
      .finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="loading">Checking session…</div>;
  if (error)
    return (
      <main className="login">
        <div className="login-card">
          <ErrorMessage error={error} />
          <p className="muted">
            The LAN service is not ready. Check the application status and
            reload.
          </p>
        </div>
      </main>
    );
  return session?.user ? (
    <Shell
      session={session}
      onLogout={() => setSession({ ...session, user: null })}
    />
  ) : (
    <Login onLogin={setSession} />
  );
}
export { App, DraftEditor, DraftFlow, Evidence, Inbox, Login, Shell, Transfer };
