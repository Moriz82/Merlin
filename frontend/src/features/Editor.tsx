import React, { useEffect, useRef, useState } from "react";
import { ApiError, api, downloadJson, RecordItem, Session } from "../api";
import { ErrorMessage, Empty, UnsavedDialog } from "../components/common";
import { RecordDiff } from "../components/RecordDiff";
import { DraftQuestions } from "./editor/DraftQuestions";
import { Markdown } from "./Markdown";
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
  const [questionDirty, setQuestionDirty] = useState(false);
  const [view, setView] = useState<"write" | "split" | "preview">("split");
  const [error, setError] = useState<unknown>(null);
  const [conflict, setConflict] = useState<{
    message: string;
    current?: RecordItem;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [evidence, setEvidence] = useState<RecordItem[]>([]);
  const [evidenceError, setEvidenceError] = useState<unknown>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [hasMoreEvidence, setHasMoreEvidence] = useState(false);
  const evidenceRequest = useRef(0);
  const evidenceBusy = useRef(false);
  const evidenceOffset = useRef(0);
  const savingRef = useRef(false);
  const blocked = useRef(false);
  const saveRequested = useRef(false);
  const timer = useRef<number | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const selectedEvidenceIds = Array.isArray(draft.evidence_ids) ? draft.evidence_ids.filter((id): id is string => typeof id === "string") : [];
  async function loadEvidencePage(reset = false) {
    if (reset) {
      evidenceRequest.current++;
      evidenceBusy.current = false;
      evidenceOffset.current = 0;
      setEvidence([]);
      setHasMoreEvidence(false);
    }
    if (evidenceBusy.current) return;
    const generation = evidenceRequest.current;
    const offset = evidenceOffset.current;
    evidenceBusy.current = true;
    setEvidenceLoading(true);
    setEvidenceError(null);
    try {
      const result = await api.get<{ items?: RecordItem[]; total?: number }>(offset ? `/api/evidence?limit=100&offset=${offset}` : "/api/evidence");
      if (generation !== evidenceRequest.current) return;
      const page = Array.isArray(result.items) ? result.items : [];
      evidenceOffset.current = offset + page.length;
      setEvidence(current => {
        const ids = new Set(current.map(item => item.id));
        return [...current, ...page.filter(item => {
          if (ids.has(item.id)) return false;
          ids.add(item.id);
          return true;
        })];
      });
      setHasMoreEvidence(typeof result.total === "number" ? evidenceOffset.current < result.total && page.length > 0 : page.length === 100);
    } catch (err) {
      if (generation === evidenceRequest.current) setEvidenceError(err);
    } finally {
      if (generation === evidenceRequest.current) { evidenceBusy.current = false; setEvidenceLoading(false); }
    }
  }
  useEffect(() => {
    void loadEvidencePage(true);
    return () => { evidenceRequest.current++; };
  }, [selected?.id]);
  useEffect(() => {
    if (selected?.id && selected.id === recordRef.current?.id) {
      if (selected.revision_id === recordRef.current.revision_id) return;
      if (dirty) {
        blocked.current = true;
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
      blocked.current = false; setConflict(null);
      return;
    }
    const next = selected?.data ?? emptyDraft;
    recordRef.current = selected?.id ? selected : null;
    draftRef.current = next;
    setDraft(next);
    setDirty(!selected?.id);
    blocked.current = false; setConflict(null);
  }, [selected?.id, selected?.revision_id]);
  useEffect(() => {
    onDirtyChange(dirty || questionDirty);
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty || questionDirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  }, [dirty, questionDirty, onDirtyChange]);
  const description = String(draft.description ?? "");
  function change(key: string, value: unknown) {
    const next = { ...draftRef.current, [key]: value };
    draftRef.current = next;
    setDraft(next);
    setDirty(true);
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
    if (lockedReason || !online || blocked.current) return;
    if (savingRef.current) {
      saveRequested.current = true;
      return;
    }
    savingRef.current = true;
    saveRequested.current = false;
    setSaving(true);
    setError(null);
    const payload = draftRef.current;
    let succeeded = false;
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
      succeeded = true;
      recordRef.current = item;
      onSaved(item);
      if (JSON.stringify(draftRef.current) === JSON.stringify(payload)) {
        setDirty(false);
      } else {
        setDirty(true);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        blocked.current = true;
        setConflict({
          message: err.message,
          current: (err.detail as { current?: RecordItem })?.current,
        });
      } else setError(err);
    } finally {
      savingRef.current = false;
      setSaving(false);
      if (
        succeeded && (saveRequested.current ||
        JSON.stringify(draftRef.current) !== JSON.stringify(payload))
      )
        window.setTimeout(() => void save(), 0);
    }
  }
  useEffect(() => {
    if (!dirty || lockedReason || !online || conflict || error) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(), 1000);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [draft, lockedReason, online, conflict, error]);
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
            disabled={saving || !dirty || Boolean(lockedReason) || !online || Boolean(conflict)}
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
          {conflict.current && <RecordDiff local={draft} server={conflict.current.data} fields={['title', 'description', 'impact', 'remediation', 'references', 'evidence_ids', 'owner_id', 'lead_id']} />}

          <button
            onClick={() => {
              if (conflict.current) {
                recordRef.current = conflict.current;
                draftRef.current = conflict.current.data;
                setDraft(conflict.current.data);
                setDirty(false);
                onSaved(conflict.current);
              }
              blocked.current = false; setConflict(null);
            }}
          >
            Use current version
          </button>
          {conflict.current && <button onClick={() => {
            recordRef.current = conflict.current ?? recordRef.current;
            onSaved(conflict.current as RecordItem);
            blocked.current = false; setConflict(null);
            setDirty(true);
            void save();
          }}>Keep my text on current revision</button>}
        </div>
      )}
      <div className="editor-body"><div className="editor-narrative"><label>
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
      </div><aside className="draft-context"><fieldset>
        <legend>Reviewed evidence</legend>
        <small>Showing {evidence.length} loaded evidence records. Selection does not upload files to Ghostwriter.</small>
        {Boolean(evidenceError) && <ErrorMessage error={evidenceError} />}
        {evidence.length ? evidence.map((item) => <label key={item.id}><input type="checkbox" disabled={Boolean(lockedReason)} checked={selectedEvidenceIds.includes(item.id)} onChange={() => toggleEvidence(item.id)} />{String(item.data.filename ?? item.id)}</label>) : !evidenceLoading && !evidenceError ? <small>No reviewed evidence records are available.</small> : null}
        {evidenceLoading && <small role="status">Loading evidence…</small>}
        {(hasMoreEvidence || Boolean(evidenceError)) && <button type="button" disabled={evidenceLoading} onClick={() => void loadEvidencePage()}>Load more evidence</button>}
      </fieldset>
      <DraftQuestions leadId={text("lead_id")} online={online} onDirtyChange={setQuestionDirty} /></aside></div>
      <p className="save-state" role="status" aria-label="Draft save status" aria-live="polite" aria-atomic="true">
        {lockedReason ? dirty ? `Not saved · draft locked by ${lockedReason}. Newer local changes remain in this editor; save a draft file.` : `Saved revision is current. Editing locked: ${lockedReason}` : !online ? dirty ? "Not saved · connection lost. Keep this page open or save a draft file." : "Saved revision retained. Connection unavailable." : saving ? "Saving…" : conflict ? "Conflict · choose a version before saving." : dirty ? "Autosaving in one second · unsaved changes stay local until saved." : "Saved revision is current."}
      </p>
    </div>
  );
}
export { DraftEditor };
