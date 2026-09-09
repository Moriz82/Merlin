# BloodHound importer

Supports BloodHound v5 JSON object arrays and safe ZIP archives containing only
unencrypted JSON. The importer preserves stable object identifiers, membership
relationships, and collection method metadata when present.

Archive path traversal, symlink, duplicate, nested archive/database, encrypted,
and non-JSON entries are rejected. Object inventory and membership are partial
evidence for manual review; no directory query or collection tool runs here.
