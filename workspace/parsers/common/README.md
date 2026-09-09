# Common parser core

`ParseContext` owns the bounded normalized asset, relationship, and observation
shapes shared by all importers. `archive`, `secret_bearing`, and `safe_text`
implement the existing archive, quarantine, and display limits. The core does
not collect, execute tools, or access the network.

Inputs are already bounded raw bytes. Source provenance is retained through the
normalized locations and quarantine flag; possible secret-bearing content is
kept out of normal views. Archive entries are limited, path checked, JSON-only,
and reject nested archives/databases. Shared behavior is covered by the parser
and backend tests.
