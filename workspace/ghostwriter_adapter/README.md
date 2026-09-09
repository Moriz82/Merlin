# Ghostwriter adapter

This module creates reviewed `reportedFinding` proposals for the installed Ghostwriter schema. It is Merlin-only. It checks the supported GraphQL input fields before review and sends to an exact HTTPS origin, except for a synthetic local container.

Adapter `ghostwriter-reportedFinding-v7.2.6-3` treats `addedAsBlank` as a Ghostwriter server-owned field. It does not send that field. The live v7.2.6 role schema omits it from `reportedFinding_insert_input` and sets it after insertion.

A lead scribe must preview the exact destination, report, rendered payload, and evidence manifest before review. The proposal hash binds these fields to the saved draft revision and a unique proposal ID. Any draft, evidence, or connection change requires a new preview. Send and reconcile bind the current delivery revision and payload hash. A remote error after dispatch is `uncertain`; reconcile it with the remote finding ID before any later action.

Markdown input does not permit raw HTML. Markdown images render as an instruction to attach reviewed evidence in Ghostwriter. If evidence is selected, the adapter sets `attachment_state` to `manual_required`: it sends the text and manifest but does not upload the evidence files.

Keep the scoped token in the private workspace key path. Do not place it in source, screenshots, or logs. Treat API delivery, browser document inspection, and manual evidence attachment as separate acceptance results. See [Ghostwriter delivery](../../docs/GHOSTWRITER.md).
