# Build ledger

Requested: approved Harbinger/Merlin plan. Runtime model calls: zero.
Main owns contracts, Python core, integration, release evidence, and publication.
Current user authorization: Astra for planning and design; Terra or Sol where each fits. Use source and synthetic fixtures only.
Ruling: preserve main as requested; do not create task branches.
Ruling: use separate internal copies of the small application core, not a shared runtime dependency.
Documentation and synthetic-performance package, 2026-09-08:

- Planner brief: supplied by the main task handoff.
- Implementation: Terra agent (`/root/terra_ui_corrections`).
- Runtime model calls: zero.
- Live acceptance claim: none.

Private release acceptance, 2026-09-09:

- Integration and security hardening: main agent with read-only Luna reviews.
- Source: repository code and synthetic fixtures only.
- Added exact origin and port enforcement, signed pre-body peer admission, one-use request nonces, read-only backup source access, atomic backup and restore staging, and failure preservation.
- Live failure tests added immutable SQLite identity reads, sealed backup databases, full staged-file rehashing, and descriptor-leak checks. An independent final review found no release blocker in these areas.
- Dell evidence: Kali VMs 1110 and 1111, private TLS pairing, LAN and encrypted-file exchange, backup and restore, and performance fixtures.
- Local Ghostwriter v7.2.6 API delivery and uncertain-state reconciliation passed. A separate Ghostwriter evidence upload, DOCX export, package check, text check, and rendered-page review also passed.
- Runtime model calls: zero.
