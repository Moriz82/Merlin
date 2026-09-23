# Operations

Use one private workspace per application. Give each person an individual account. Keep `state/`, backups, transfer bundles, evidence, and Ghostwriter tokens on approved encrypted storage in client mode.

## Prepare and start

Run these commands from the repository root:

```sh
./manage.sh plan
./manage.sh build
./manage.sh init "ENGAGEMENT NAME" synthetic http://127.0.0.1:8711 scribe-lead ENGAGEMENT-ID http://merlin:8711
./manage.sh up
./manage.sh status
./manage.sh verify
```

Create one engagement UUID and use it on both hosts. Keep the generated application instance IDs different. `init` creates the first account and transfer keys. Use `./manage.sh add-user NAME scribe` for each scribe. Stop the service before host-side account, key, pairing, Ghostwriter, backup, or restore work.

## Enable a client LAN listener

Keep `HOST_BIND=127.0.0.1` for local work. For client mode, first verify the encrypted storage check and the approved team address. Use a DNS name that matches the certificate. Use a certificate chain trusted by both application containers.

Place the server certificate and key in `tls/`. Set the container paths in `.env`:

```text
HOST_BIND=TEAM-LAN-ADDRESS
APP_PORT=8711
APP_TLS_CERT=/tls/merlin.crt
APP_TLS_KEY=/tls/merlin.key
APP_PEER_CA_BUNDLE=/tls/team-ca.crt
```

`APP_PEER_CA_BUNDLE` is the CA certificate that validates the enrolled Harbinger HTTPS certificate. It affects peer transfer only. Include `:8711` in both HTTPS origins when `APP_PORT=8711`. If the origin has no port, set `APP_PORT=443`. A mismatch blocks startup. Do not bypass certificate verification. Confirm the page, login, peer transfer, and reconnect behavior from a second team device before use.

## Pair Merlin and Harbinger

Stop both applications. On each host, create a public pairing card:

```sh
umask 077
./manage.sh peer-card > merlin-peer.json
```

Exchange the card through the approved team channel. Compare its printed fingerprint through a separate channel. Enroll only after the name, application, engagement, origin, and fingerprint match:

```sh
./manage.sh enroll harbinger-peer.json VERIFIED-FINGERPRINT
./manage.sh info
```

Start both applications. Review each received lead and its evidence provenance. If LAN transfer fails, import the reviewed `.age` bundle. Import is idempotent. Do not transfer a SQLite database.

The receiving host admits only a request signed by its enrolled peer. The signature binds the source, recipient, timestamp, one-use nonce, declared length, and body checksum. A rejected or uncertain transfer is never retried automatically.

## Configure Ghostwriter

Stop Merlin. Use a scoped API token for one approved Ghostwriter origin and report. Keep the token in a private `0600` file or enter it at the local prompt:

```sh
./manage.sh ghostwriter https://ghostwriter.example.test REPORT-ID SEVERITY-ID FINDING-TYPE-ID /PRIVATE/ghostwriter.token
./manage.sh up
```

Open the draft. Select **Preview delivery**. Check the exact origin, report, rendered payload, evidence manifest, and hashes. Select **Mark reviewed**, then send that fixed proposal once. If the state is **Uncertain**, use the remote finding ID to reconcile. Do not retry the create mutation.

Enter the Ghostwriter finding ID as decimal digits. Merlin preserves the exact ID, including values larger than JavaScript's safe integer range. Check the remote record before reconciliation.

The adapter sends finding text and an evidence manifest. It does not upload evidence files. Attach reviewed files in Ghostwriter and verify the rendered document before final delivery.

## Connection loss

The browser shows the last successful sync time. It keeps the loaded view and unsaved text in memory. It disables server writes until the event connection returns. Save urgent text to a file in the approved encrypted workspace. The browser does not store client prose in local storage.

An open event stream does not extend the 30-minute idle session limit. If the session expires, Merlin keeps the draft text on screen and shows **Sign in again**. Select **Save draft file** first if text is unsaved. Signing in again clears local text; the dialog requires an explicit choice. The Docker `/healthz` check reports a degraded state when the audit writer or private storage blocks writes.

Evidence lists and the draft evidence picker load bounded pages. Select **Load more evidence** to reach older records. The count on screen describes loaded records.

## Back up and restore

Stop the service. Back up to a new directory:

```sh
./manage.sh stop
./manage.sh backup /APPROVED/NEW/merlin-backup
```

Verify the receipt and keep the source workspace. Restore only when `state/` is absent. Preserve the old state under a separate name, then restore and verify:

```sh
./manage.sh restore /APPROVED/merlin-backup
./manage.sh info
./manage.sh verify
```

Start only after the engagement and instance identity are correct.

The backup command mounts the stopped source workspace read-only. It reads committed WAL data when clean shutdown leaves SQLite sidecars. It does not change the source bytes. The backup manifest records the size and SHA-256 checksum of every file. The backup seals its SQLite copy and does not keep WAL sidecars. Restore opens the source in immutable mode and rechecks each staged copy before it publishes the new workspace.
