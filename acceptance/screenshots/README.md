# Synthetic screenshot capture manifest

Capture only synthetic workspace data. Do not capture client records, source artifacts, keys, tokens, or browser session values.

For each capture, create a manifest entry with:

```text
capture_id:
application: Harbinger or Merlin
route:
viewport:
fixture: synthetic fixture name and revision
operator:
captured_at_utc:
file: relative PNG path
sha256:
notes: visible state and any limitation
```

Use a repeatable viewport and fixture. Record the route, role, and state that the capture proves. Store PNG files in this directory only after review.

The checked-in set uses synthetic data. It covers the lead inbox and Markdown editor at 1366 x 768 and 1920 x 1080. Verify each file against `MANIFEST.json` before release.
