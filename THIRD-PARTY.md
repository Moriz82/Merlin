# Third-party inventory

Checked 2026-09-08 against `pyproject.toml`, `requirements.lock`, `frontend/package-lock.json`, and installed frontend package metadata. This lists direct dependencies. The lock files contain the complete transitive dependency graph.

## Python

| Package | Exact version | License metadata | Purpose |
| --- | --- | --- | --- |
| fastapi | 0.141.1 | MIT | API framework |
| uvicorn | 0.52.4 | BSD-3-Clause | ASGI server |
| python-multipart | 0.0.32 | Apache-2.0 | Multipart input |
| defusedxml | 0.7.1 | PSFL | Safe XML input |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | Cryptography primitives |
| httpx | 0.28.1 | BSD-3-Clause | Scoped HTTPS client |
| jsonschema | 4.26.0 | MIT | Schema validation |
| markdown-it-py | 4.2.0 | MIT; installed wheel includes project and upstream markdown-it license files | Markdown handling |
| pytest | 9.1.1 | MIT | Test runner (dev) |
| pytest-asyncio | 1.4.0 | Apache-2.0 | Async tests (dev) |

## Frontend

| Package | Exact version | License | Purpose |
| --- | --- | --- | --- |
| react | 18.3.1 | MIT | UI runtime |
| react-dom | 18.3.1 | MIT | Browser rendering |
| @testing-library/jest-dom | 6.6.3 | MIT | DOM assertions |
| @testing-library/react | 16.1.0 | MIT | UI tests |
| @types/node | 22.13.14 | MIT | Node type declarations |
| @types/react | 18.3.18 | MIT | React type declarations |
| @types/react-dom | 18.3.5 | MIT | React DOM type declarations |
| @vitejs/plugin-react | 5.2.0 | MIT | Vite JSX transform |
| esbuild | 0.28.1 | MIT | Build transform |
| jsdom | 25.0.1 | MIT | Browser-like test environment |
| typescript | 5.6.3 | Apache-2.0 | Type checking |
| vite | 7.3.6 | MIT | Development and build |
| vitest | 4.1.11 | MIT | Tests |
| react-markdown | 9.0.3 | MIT | Safe Markdown rendering |
| remark-gfm | 4.0.0 | MIT | GitHub Markdown extensions |

## Runtime tools

| Tool | Exact version and revision | License | Purpose |
| --- | --- | --- | --- |
| age and age-keygen | v1.3.2 source revision `b74dce4cdbe35b5e5f66c06d9612b72f89028758`, Go module sum `h1:r6RSZLFSMm6rzKepZ7ZAYkKCu14f3/Me8c7uKYh7C8c=`, license blob `0080870bc29b23d7605816ac0a535a4eef9d1553` | BSD-3-Clause | Encrypted team bundles and recipient keys. The Docker builder requires this immutable revision and module checksum with `sum.golang.org`, then includes the upstream notice at `/usr/share/doc/age/copyright`. |

## Design references

| Reference | Revision | License | Use |
| --- | --- | --- | --- |
| Hawkeye | main `162b02fb721d852e628113430ea3562d49478fe4` | MIT | Design reference only; no copied code. |
| Gossamer | main `9f706defe10b24e567682b6dd60bae06650ee792` | MIT | Design reference only; no copied code. |

## Acceptance reference

| Reference | Revision | License | Use |
| --- | --- | --- | --- |
| Ghostwriter | tag `v7.2.6`, commit `cdc225787653ee6a848f4cafe8f90adfdc1987c9` | BSD-3-Clause | External local Docker acceptance service; no source or image is redistributed by Merlin. |

See [frontend inventory](frontend/THIRD-PARTY.md) for the browser dependency scope.
