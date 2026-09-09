# Nmap importer

Supports Nmap XML, ordinary text, and grepable (`-oG`) text. XML preserves
addresses, hostnames, open services, and completion status; text formats are
lossy and report that limitation. Nmap output is parsed offline with no scanner
execution or network access.

Service endpoints are limited to valid TCP, UDP, or SCTP ports. Invalid or
missing host addresses are skipped with bounded limitations. The dispatcher
tests all three formats and the lossy/partial behavior.
