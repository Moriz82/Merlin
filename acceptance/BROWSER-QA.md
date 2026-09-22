# Current browser acceptance

The self-contained local runner is `acceptance/browser_acceptance.py`. Its tested tool versions, license notes, and manual gates are recorded below.

```sh
python3 acceptance/browser_acceptance.py --app Merlin --origin http://127.0.0.1:8711 --output acceptance/browser-current
```

`browser-current/RESULTS.json` and its PNG manifest are the current reproducible evidence. The run uses installed Chromium **152.0.7977.75** and Python Playwright **1.62.0** (**Apache-2.0**); no dependencies were installed. Real built HTML/JS/CSS comes from 8711. The live unauthenticated login screen is checked without credentials. All later APIs and login data are synthetic loopback fixtures; no real workspace data, transfer, Ghostwriter delivery, or backend mutation is used.

The final 2026-09-22 run passed all three viewport cases with zero browser, network or HTTP errors. All nine current Merlin captures were visually reviewed, and their hashes and dimensions were verified. The served JS/CSS hashes match the local `frontend/dist` bytes. Some editor captures are scrolled to the edited field; they show the viewport at that interaction point.

The run checks loaded Inbox, Drafts, Evidence and Transfer routes, the Markdown editor and offline state at **1366 x 768** and **1920 x 1080**, with no page-wide horizontal overflow. It also checks visible skip-link focus, main focus, keyboard navigation, modal focus containment/Escape/restoration, named controls in the Chromium accessibility tree, reduced-motion behavior and browser/network errors.

Markdown checks verify that raw HTML tags are omitted, a script-protocol link and an external link are omitted, a remote embedded image is omitted without a request, and a fragment link remains allowed. The fixture uses only benign synthetic text. Closing the native fixture EventSource stream verifies truthful connection-loss text, disabled writes/sign-out, local text retention and no attempted offline save.

**Manual gates remain:** actual browser zoom at 200% and screen-reader speech/usability. The **683 x 384** capture is only a compact reflow proxy. Accessibility-tree names/roles do not prove screen-reader announcements, reading order, or WCAG conformance. Synthetic login does not prove live authentication or persistence. See the result's exact scope and limitations before citing it.
