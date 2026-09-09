# Synthetic rehearsal

Run date: 2026-09-09 UTC.

The Docker rehearsals used synthetic records only.

1. Merlin imported four encrypted records selected in Harbinger.
2. A scribe created and revised a Markdown finding draft.
3. The lead scribe previewed and approved the exact Ghostwriter payload.
4. The first network dispatch created the remote finding, but a local response-handling defect left the delivery uncertain.
5. Exact reconciliation found that remote finding and recorded delivery without another send.
6. The response handling was repaired. Regression tests now use a real `httpx.Response` and inject a post-dispatch persistence failure.
7. A scribe question returned to the Harbinger owner.
8. The encrypted file fallback imported once. A repeat used the original receipt and made no second merge.
9. Kali VM 1110 used its enrolled identity and private CA to send a five-record closure and two evidence files to Kali VM 1111 over HTTPS.
10. Merlin imported three new records, recognized two exact duplicates, and returned the same receipt for the repeated send.
11. Merlin was stopped. The next LAN send returned 503 without a false success state. The 5,164-byte encrypted file then crossed hosts and returned the original receipt.
12. Merlin returned one new question and four exact duplicate dependencies.
13. Harbinger created an 18-file v6 backup. Merlin created a 17-file v6 backup. Restore retained peer enrollment, evidence, findings, leads, questions, and the Merlin draft.
14. Both four-core, 8 GiB Kali VMs passed the 10,000-asset, 100,000-relationship, 12-session performance thresholds.
15. Merlin delivered another reviewed synthetic finding with adapter `ghostwriter-reportedFinding-v7.2.6-3`. The live schema check accepted the server-owned `addedAsBlank` field behavior.
16. A separate Ghostwriter operation uploaded one synthetic text evidence file and added its evidence block to the delivered finding.
17. Ghostwriter's exporter produced a 70,740-byte DOCX. Package checks and text extraction found the finding title, evidence body, and numbered caption. LibreOffice rendered 17 pages. Page 10 passed visual review.

The local Ghostwriter version was v7.2.6. Evidence files stayed `manual_required`; the adapter did not upload them. The separate Ghostwriter evidence and document checks passed. The sample template left its affected-entity and severity display slots blank, although the stored Ghostwriter record had severity `Critical`. Original pre-restore state folders remain preserved on both VMs.
