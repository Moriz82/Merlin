# Local Ghostwriter acceptance lab

Use this lab only with synthetic data. Merlin does not ship Ghostwriter or its source. The current acceptance host uses the official Ghostwriter `v7.2.6` Docker release under `/home/moriz/Documents/CPTC/.lab/ghostwriter-v7.2.6`.

Start the pinned lab from that checkout:

```sh
docker compose -p ghostwriter-merlin-lab -f /home/moriz/Documents/CPTC/.lab/ghostwriter-v7.2.6/local.yml up -d --wait
docker compose -p ghostwriter-merlin-lab -f /home/moriz/Documents/CPTC/.lab/ghostwriter-v7.2.6/local.yml ps
```

The accepted browser endpoint binds to `127.0.0.1:9081`. The lab nginx container also joins `cptc-team-workspace` with the alias `ghostwriter`. Configure synthetic Merlin with `http://ghostwriter`. Keep the scoped token in a `0600` file outside all repositories.

Create a synthetic client, project, report, severity, and finding type in Ghostwriter. Stop Merlin. Run `./manage.sh ghostwriter` with those IDs and the private token file. Start Merlin. Preview the exact outgoing payload, mark that fixed proposal reviewed, and send it once.

Record the remote finding ID. Add a reviewed synthetic evidence file through Ghostwriter. Export the report with Ghostwriter's own DOCX exporter. Verify the DOCX package, title, evidence body, and caption. Render the DOCX and inspect the report page. An API receipt alone does not prove the document or attachment layout.

The 2026-09-09 check used Ghostwriter v7.2.6 and adapter `ghostwriter-reportedFinding-v7.2.6-3`. Ghostwriter stored one reviewed synthetic finding and one synthetic text evidence file. The exported DOCX contained the finding title, evidence body, and numbered caption. LibreOffice rendered the report without an error. The sample Ghostwriter template left its affected-entity and severity display slots blank. The stored Ghostwriter record had severity `Critical`; this template limitation does not change the delivery receipt.

Stop the lab when the acceptance work is complete:

```sh
docker compose -p ghostwriter-merlin-lab -f /home/moriz/Documents/CPTC/.lab/ghostwriter-v7.2.6/local.yml stop
```
