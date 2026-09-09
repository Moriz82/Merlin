# Development

Use Python 3.11 or later and the pinned Python lock file. Use the frontend lock file for browser dependencies. Work on source or synthetic fixtures only.

```sh
uv sync --frozen
pytest
cd frontend
npm ci
npm test
npm run typecheck
npm run build
```

Harbinger parser adapters are in `workspace/parsers`. Add a format only with bounded parsing, offline behavior, clear limitations, and tests. Merlin Ghostwriter work is in `workspace/ghostwriter_adapter`; keep review, revision, and uncertain-delivery gates intact.

Run the optional synthetic performance tool separately. It is not a pytest test and it does not contact a target:

```sh
python tests/performance/runner.py
```

Use [THIRD-PARTY.md](../THIRD-PARTY.md) and the frontend inventory before adding a dependency. See [Readiness](READINESS.md) for checks that still need live evidence.

The Docker age builder installs both tools from the immutable age revision `b74dce4cdbe35b5e5f66c06d9612b72f89028758`. It requires module checksum `h1:r6RSZLFSMm6rzKepZ7ZAYkKCu14f3/Me8c7uKYh7C8c=` with the Go checksum database enabled. Check that isolated build with:

```sh
docker build --target age_builder .
```
