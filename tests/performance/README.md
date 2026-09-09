# Synthetic performance runner

Run this tool only when you want a local synthetic measurement. It creates a temporary private workspace, loads fake records, and measures the real FastAPI and SQLite paths with concurrent authenticated sessions. It does not call scanners or contact network targets.

```sh
python tests/performance/runner.py
python tests/performance/runner.py --assets 10 --relationships 20 --sessions 2
```

The default run makes 10,000 assets, 100,000 relationships, and 12 synthetic sessions. It measures the initial asset and graph API view plus concurrent search and save operations. It writes one JSON result to standard output and exits nonzero when a timing threshold fails. The small command is a smoke check. `pytest` does not invoke the full run.

Do not use client records as input. This runner does not measure browser rendering. Record browser and designated-VM results separately in [Readiness](../../docs/READINESS.md).
