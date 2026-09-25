# Merlin

Merlin is a local writing desk. It receives leads, keeps revisioned report drafts, and offers a reviewed Ghostwriter delivery path. It does not run scanners, model transport, or raw HTML rendering. This source repository is public; engagement data and credentials remain private.

## First local start

Use Docker Compose. This local example uses synthetic data and binds only to loopback.

```sh
./manage.sh plan
./manage.sh build
./manage.sh init "Synthetic practice" synthetic http://127.0.0.1:8711 scribe-lead
./manage.sh up
./manage.sh status
./manage.sh verify
```

Open `http://127.0.0.1:8711`. Use the password that you entered during `init`. Run `./manage.sh stop` when the desk is not in use.

The default service is local-only. Use the same engagement ID in Harbinger and Merlin. Review [Operations](docs/OPERATIONS.md) before pairing hosts, enabling a LAN listener, or configuring Ghostwriter.

## Guides

- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Data handling](docs/DATA-HANDLING.md)
- [Development](docs/DEVELOPMENT.md)
- [Readiness](docs/READINESS.md)
- [Third-party inventory](THIRD-PARTY.md)
- [Ghostwriter guide](docs/GHOSTWRITER.md)
- [Client info stripper](workspace/client_info_stripper/README.md)
- [Ghostwriter adapter](workspace/ghostwriter_adapter/README.md)
