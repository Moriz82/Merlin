# Client info stripper

This module makes a minimized local export for human review. It accepts record data and a private engagement key of at least 32 bytes. It makes stable HMAC aliases for asset identifiers.

The output keeps only admitted asset kind, track, valid port, valid protocol, and relationships between included assets. It labels the result `client-confidential-minimized`.

It removes names, addresses, URLs, prose, raw evidence, credentials, and source paths. Its receipt sets `human_review_required` and `disclosure_authorized` to false. The module does not use a network connection and does not authorize release of any data.

See [Data handling](../../docs/DATA-HANDLING.md).
