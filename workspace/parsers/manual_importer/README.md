# Manual importer

Supports manual observation schema version 1 with declared assets,
observations, and relationships. Asset tracks are restricted to the existing
network, web, Linux, Windows/AD, and database-service set; references must
resolve within the imported asset set.

The importer performs no interpretation beyond shape and ownership checks.
Unknown fields, invalid tracks, unresolved subjects, and malformed schema are
rejected. Tests cover valid and invalid manual records.
