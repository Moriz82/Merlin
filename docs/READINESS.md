# Readiness

This is a private release-candidate checkpoint. It records synthetic checks on the development host and both designated Kali VMs.

Last checked: 2026-09-09 UTC.

| Evidence | Status | Current evidence | Remaining gate |
| --- | --- | --- | --- |
| Backend and frontend suites | Pass | 184 backend tests and 31 frontend tests passed after the Ghostwriter v7.2.6 schema correction. Five environment-dependent tests were skipped. Typecheck, the production build, and the high-severity dependency audit passed. | Repeat after a source change. |
| Current-host Docker service | Pass | The rebuilt app passed its health check on loopback. A networkless backup container read the source through a read-only mount and produced a verified 10-file backup. | None for this host. |
| Same-host Harbinger-to-Merlin exchange | Pass | Merlin imported four selected synthetic records and returned a scribe question. | None. |
| HTTPS LAN with individual accounts | Pass | Kali VM 1110 and VM 1111 used enrolled identities and the private CA. Merlin received a five-record closure and two evidence files. It imported three new records, recognized two exact duplicates, and returned one new question with four duplicate dependencies. Repeated sends returned the original receipts. | Re-enroll if either host identity, key, or address changes. |
| Encrypted file fallback | Pass | Merlin was stopped before a repeated LAN send. Harbinger returned 503. The 5,164-byte age bundle then crossed hosts and imported with the original receipt and no second merge. | None. |
| Ghostwriter API delivery | Pass on this host | Merlin delivered a reviewed synthetic finding to local Ghostwriter v7.2.6. The live schema check confirmed that `addedAsBlank` is server-owned. Reconciliation found the exact remote record after an uncertain local result and did not send a duplicate. | None for the API path on this host. |
| Ghostwriter document and evidence attachment | Pass on this host | A separate Ghostwriter operation attached reviewed synthetic text evidence. Ghostwriter exported a 70,740-byte DOCX. Package and text checks found the title, evidence body, and numbered caption. LibreOffice rendered the 17-page document, and page 10 passed visual review. Merlin still reports `manual_required` and does not upload evidence. | Repeat for a changed Ghostwriter release or report template. |
| Synthetic browser review | Pass | Reviewed screenshots cover the inbox and Markdown editor at 1366 x 768 and 1920 x 1080. Hashes are in the screenshot manifest. | None. |
| Synthetic performance run | Pass on Kali VM 1111 | The 10,000-asset and 100,000-relationship fixture loaded in 4.313 seconds. Initial view was 0.391 seconds. Search p95 was 0.532 seconds. Save p95 was 0.053 seconds with 12 sessions. | None. |
| Kali VM 1110 | Pass | Verified `kali-base-test`, four cores, 8 GiB RAM, wired `vmbr0`, snapshot chain, Kali 2026.1, source trust, repeat setup, Harbinger deployment, HTTPS pairing, transfer, backup, restore, and restart. | Keep the accepted snapshot and host-key record with the private release record. |
| Kali VM 1111 | Pass | Verified `kali-cptc-test`, four cores, 8 GiB RAM, wired `vmbr0`, snapshot chain, Kali 2026.1, source trust, repeat setup, Docker deployment, HTTPS pairing, transfer, a 17-file v6 backup, restore, retained six required records, and restart. | Keep the accepted snapshot and host-key record with the private release record. |

All listed checks used synthetic data. See [the rehearsal record](../acceptance/REHEARSAL.md).
