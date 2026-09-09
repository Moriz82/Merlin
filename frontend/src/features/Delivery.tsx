import React, { useEffect, useRef, useState } from "react";
import { ApiError, api, downloadJson, RecordItem, Session } from "../api";
import { ErrorMessage, Empty, UnsavedDialog } from "../components/common";
import { DraftEditor } from "./Editor";
import { deliveryMessage, attachmentMessage } from "./delivery/presentation";
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
    <section className="draft-flow">
      <button className="back" onClick={onBack}>
        Back to drafts
      </button>
      <nav className="draft-jumps" aria-label="Draft sections"><a href="#draft-writing" onClick={event => { event.preventDefault(); document.getElementById("draft-writing")?.scrollIntoView(); }}>Writing</a><a href="#delivery-review" onClick={event => { event.preventDefault(); document.getElementById("delivery-review")?.scrollIntoView(); }}>Review and delivery</a></nav>
      <div id="draft-writing"><DraftEditor
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
      </div><ErrorMessage error={error} />
      <div className="review-panel" id="delivery-review" aria-label="Review and delivery">
        <div>
          <p className="review-caption">Saved revision review</p>
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
            Delivery: <strong>{String(delivery.data.status)}</strong><span>{deliveryMessage(deliveryStatus)}</span>
            {delivery.data.payload_hash
              ? ` · hash ${String(delivery.data.payload_hash)}`
              : ""}
          </p>{String(delivery.data.attachment_state ?? "") === "manual_required" && <p className="uncertainty" role="status">{attachmentMessage(deliveryStatus)}</p>}</>
        )}
        {deliveryStatus === 'uncertain' && <div className="reconcile"><label>Remote Ghostwriter finding ID<input inputMode="numeric" value={remoteId} onChange={(event) => setRemoteId(event.target.value)} /></label><button disabled={!canLead || !online || busy || refreshing || reconciling || !delivery?.data.payload_hash || !/^\d+$/.test(remoteId)} onClick={() => void reconcile()}>{reconciling ? 'Reconciling receipt…' : 'Reconcile receipt'}</button></div>}
      </div>
    </section>
  );
}
export { DraftFlow };
