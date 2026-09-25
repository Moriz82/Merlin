# Merlin frontend

Merlin is the local writing desk. It uses same-origin API calls and the approved Harbinger/Merlin contract. It has no model transport, scanner execution, fixture fallback, or raw HTML rendering.

```sh
npm ci
npm run dev
npm test
npm run typecheck
npm run build
```

Inbox shows incoming findings. Drafts provide a native textarea with a small Markdown toolbar and a safe `react-markdown` + `remark-gfm` preview. Edits autosave after one second. A stale revision keeps the local text in the editor and shows the server version for reconciliation. Ghostwriter delivery requires an explicit review step; `uncertain` remains visible and is not retried automatically. Transfer uses enrolled peers and encrypted `.age` files.

## Dependency inventory

See [the exact frontend inventory](THIRD-PARTY.md).
