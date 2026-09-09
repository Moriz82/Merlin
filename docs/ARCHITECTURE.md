# Architecture

Each application is a private, local workspace. They keep separate SQLite state and separate code. They do not share a runtime dependency.

```text
Browser UI -> same-origin FastAPI service -> SQLite state and private artifacts
                               |-> contained, offline parser work (Harbinger)
                               |-> reviewed Ghostwriter adapter (Merlin only)
                               |-> signed, age-encrypted transfer bundle
```

Harbinger stores assets, relationships, uploads, evidence, and technical findings. Merlin receives leads and creates report drafts. A transfer sends selected records in a signed encrypted bundle. It never imports a SQLite database.

Each LAN transfer also has a signed HTTP request envelope. The receiving host verifies the enrolled source, exact recipient, timestamp, one-use nonce, declared size, and body checksum before it reserves the file-transfer slot or reads the body. It rejects stale, replayed, oversized, or changed requests. After decryption, it independently verifies the bundle signature, schema, and record provenance.

The parser accepts only admitted source formats. It runs without a network target. The API records revisions and audit events. The UI keeps a dirty draft local until the user saves or discards it. A reviewed local image route admits only bounded PNG, GIF, and JPEG bytes.

Ghostwriter is a Merlin-only boundary. A lead scribe previews an exact destination and rendered payload, then reviews its fixed proposal hash. Send and reconcile require the current delivery revision. An uncertain result needs reconciliation. Evidence files with a manual attachment state are not uploaded by the adapter.

See [Data handling](DATA-HANDLING.md), [Operations](OPERATIONS.md), and [Readiness](READINESS.md).
