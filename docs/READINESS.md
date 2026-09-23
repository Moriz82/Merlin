# Readiness

**Current checkout, 2026-09-23 UTC:** Local synthetic checks passed after evidence pagination, off-page evidence refresh, session-expiry, writable-storage health, and Ghostwriter-ID changes: 218 backend tests and 54 frontend tests, with five role-specific backend skips; TypeScript and the production build passed. The current built browser UI passed the synthetic fixture at three viewports; see the local result at `/home/moriz/.local/state/harbinger-merlin-e2e/browser-current-r2/merlin/RESULTS.json`. The Dell host is offline, so the changed Docker image, two-VM workflow, Ghostwriter report template, and CPTC11 handoff are **not yet revalidated**. The table below records the earlier checkpoint and must not be used as a current release claim.

This is a private release-candidate checkpoint. It records synthetic checks on the development host and both designated Kali VMs.

Last checked: 2026-09-22 UTC.

| Evidence | Status | Current evidence | Remaining gate |
| --- | --- | --- | --- |
| Backend and frontend suites | Pass | The current source passed 191 backend tests and 47 frontend tests. The five skips are Harbinger-owned collection cases. TypeScript checks and the production build passed. | None for Merlin-owned automated paths. |
| Current-host Docker service | Pass | The rebuilt read-only container was healthy. The populated workspace passed backup, restore, integrity verification, and restart with all records and revisions retained. | None for this host. |
| Same-host Harbinger-to-Merlin exchange | Pass | Merlin imported four linked records and evidence from Harbinger. Exact replay and encrypted-file import returned the original receipt. A real stale draft save returned 409 and preserved the accepted revision. Live SSE replay and reset passed. | None. |
| HTTPS LAN with individual accounts | Pass | Current-source Kali deployments used fresh private-CA certificates and strict CA verification. The direct signed transfer, dependency deduplication, lead read, and draft creation passed. | Re-enroll if either host identity, key, or address changes. |
| Encrypted file fallback | Pass | Merlin imported the prior unconfirmed encrypted bundle after the compatibility repair. A one-bit change was rejected with HTTP 422. | None. |
| Ghostwriter API delivery | Pass on this host | Merlin verified local Ghostwriter v7.2.6 with adapter `ghostwriter-reportedFinding-v7.2.6-3`, rejected a real GraphQL application error, and delivered one reviewed synthetic text finding. Timeout and no-auto-retry behavior remained uncertain until explicit reconciliation. | Evidence-file delivery remains manual by design. |
| Ghostwriter document and evidence attachment | Pass on this host | A separate Ghostwriter operation attached reviewed synthetic text evidence. Ghostwriter exported a 70,740-byte DOCX. Package and text checks found the title, evidence body, and numbered caption. LibreOffice rendered the 17-page document, and page 10 passed visual review. Merlin still reports `manual_required` and does not upload evidence. | Repeat for a changed Ghostwriter release or report template. |
| Synthetic browser review | Partial, current | Nine current captures cover Inbox, Drafts, Evidence, Transfer, Markdown safety, and offline state at 1366 x 768, 1920 x 1080, and compact 683 x 384. Keyboard focus, named controls, reduced motion, retained unsaved text, disabled offline writes, and zero browser/network errors passed. | Complete actual 200 percent browser zoom and screen-reader speech. The compact view is only a reflow proxy. |
| Synthetic performance run | Pass on this host | The 10,000-asset and 100,000-relationship fixture passed with 12 sessions. Initial view was 0.198 seconds, search p95 was 0.124 seconds, and save p95 was 0.030 seconds. | None. |
| Kali VM 1110 | Pass | Current Harbinger source, exact guest identity, existing snapshots, private CA, signed transfer, backup, restore, and restart passed. The older deployment remained healthy. | Keep the accepted snapshots and host-key record with the private release record. |
| Kali VM 1111 | Pass | Current Merlin source, exact guest identity, existing snapshots, strict HTTPS, transfer, draft persistence, backup, restore, and restart passed. The older deployment remained healthy. | Keep the accepted snapshots and host-key record with the private release record. |

All listed checks used synthetic data. See [the rehearsal record](../acceptance/REHEARSAL.md) and [current browser method](../acceptance/BROWSER-QA.md).
