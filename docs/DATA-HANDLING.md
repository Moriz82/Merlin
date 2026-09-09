# Data handling

Treat source artifacts, evidence, records, transfer keys, and Ghostwriter tokens as confidential. Keep them in the private workspace. Do not put them in screenshots, issue text, or public repositories.

Harbinger accepts saved tool output. It does not run scanners or exploits. The parser is format scoped and networkless. It records partial or unsupported results instead of making an unsupported conclusion.

The client-info stripper creates an aliased, minimized output. It removes names, addresses, URLs, prose, raw evidence, credentials, and source paths. Its receipt requires human review and does not authorize disclosure. See [its module guide](../workspace/client_info_stripper/README.md).

Transfers use signed, recipient-encrypted bundles. They carry selected records, not database files. A duplicate is not an import. A conflict preserves the encrypted bundle for host review.

Merlin renders untrusted Markdown without raw HTML or external links. External images stay blocked. An authenticated local evidence image can render only after host review and only when it passes the bounded PNG, GIF, or JPEG check. The Ghostwriter adapter sends reviewed prose and an evidence manifest. When the attachment state is `manual_required`, attach the selected evidence files in Ghostwriter yourself.

Use synthetic data for development and acceptance tooling. Do not use client data in local examples or performance runs.
