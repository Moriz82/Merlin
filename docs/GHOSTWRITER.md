# Ghostwriter delivery

Ghostwriter delivery is Merlin-only and lead-scribe controlled. It is an optional reviewed report path, not proof that a remote finding exists.

1. Configure an approved test report on this host.
2. Save the current draft.
3. Select **Preview delivery**. Check the exact destination, report, rendered title and text, and evidence manifest.
4. Select **Mark reviewed**. This approves only the displayed proposal hash.
5. Send the reviewed proposal once.
6. If the result is uncertain, reconcile it with the remote finding ID. Do not retry the mutation blindly.

The adapter checks the installed GraphQL shape and uses an exact HTTPS origin, except for a synthetic local container. Ghostwriter v7.2.6 sets `addedAsBlank` on the server. Merlin does not send that field. The preview binds the proposal ID, draft revision, destination, report, rendered payload, and evidence manifest. A draft, evidence, or connection change invalidates the preview. Send and reconcile also require the current displayed delivery revision.

When `attachment_state` is `manual_required`, the finding text and evidence manifest are delivered. The selected evidence files are **not** uploaded. Attach them in Ghostwriter after the reviewed proposal exists.

The local Docker acceptance check uses synthetic data. It covers delivery, a separate Ghostwriter evidence upload, DOCX export, package checks, text extraction, and visual review. This separate upload does not add evidence upload capability to Merlin. See [Readiness](READINESS.md), the [local lab procedure](../acceptance/GHOSTWRITER-LAB.md), and the [adapter guide](../workspace/ghostwriter_adapter/README.md).
