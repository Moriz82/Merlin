#!/usr/bin/env python3
"""Run a synthetic workload through the real workspace API. No target interaction."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import platform
from pathlib import Path
import random
import sys
import tempfile
import time
import uuid

from fastapi.testclient import TestClient

# Direct execution sets sys.path to tests/performance. Add the repository root so
# the documented source-checkout command exercises this checkout's application.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workspace import APP_NAME
from workspace.auth import add_user
from workspace.cli import initialize
from workspace.server import create_app
from workspace.store import canonical, utc


ORIGIN = 'http://127.0.0.1:8710'
PASSWORD = 'synthetic-performance-only'
NAMESPACE = uuid.UUID('3f4baf97-5df9-4adb-8464-083b33318bd6')


def elapsed(start: float) -> float:
    return time.perf_counter() - start


def rounded(value: float) -> float:
    return round(value, 6)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Measure the real local API with synthetic records; it never contacts a target.'
    )
    parser.add_argument('--assets', type=int, default=10_000)
    parser.add_argument('--relationships', type=int, default=100_000)
    parser.add_argument('--sessions', type=int, default=12)
    parser.add_argument('--max-fixture-seconds', type=float, default=30.0)
    parser.add_argument('--max-initial-view-seconds', type=float, default=3.0)
    parser.add_argument('--max-p95-seconds', type=float, default=1.0)
    return parser.parse_args()


def positive(name: str, value: int) -> None:
    if value < 1:
        raise SystemExit(f'{name} must be at least 1')


def load_fixture(store, asset_count: int, relationship_count: int) -> None:
    random_source = random.Random(20260908)
    now = utc()
    asset_ids = [str(uuid.uuid5(NAMESPACE, f'asset:{number}')) for number in range(asset_count)]
    asset_rows = []
    revision_rows = []
    for number, asset_id in enumerate(asset_ids):
        revision_id = str(uuid.uuid5(NAMESPACE, f'asset-revision:{number}'))
        data = canonical({
            'label': f'synthetic-{number:05d}',
            'kind': 'host',
            'track': ('network', 'web', 'linux', 'windows', 'database')[number % 5],
        })
        asset_rows.append((asset_id, 'asset', revision_id, data, now))
        revision_rows.append((revision_id, asset_id, None, 'performance-fixture', data, now))
    relationship_rows = []
    relationship_revisions = []
    for number in range(relationship_count):
        source = asset_ids[random_source.randrange(asset_count)]
        target = asset_ids[random_source.randrange(asset_count)]
        record_id = str(uuid.uuid5(NAMESPACE, f'relationship:{number}'))
        revision_id = str(uuid.uuid5(NAMESPACE, f'relationship-revision:{number}'))
        data = canonical({'source': source, 'target': target, 'kind': 'observed'})
        relationship_rows.append((record_id, 'relationship', revision_id, data, now))
        relationship_revisions.append((revision_id, record_id, None, 'performance-fixture', data, now))
    with store.tx() as connection:
        connection.executemany('INSERT INTO records VALUES(?,?,?,?,?)', asset_rows)
        connection.executemany('INSERT INTO revisions VALUES(?,?,?,?,?,?)', revision_rows)
        connection.executemany('INSERT INTO records VALUES(?,?,?,?,?)', relationship_rows)
        connection.executemany('INSERT INTO revisions VALUES(?,?,?,?,?,?)', relationship_revisions)
        store.event(connection, 'performance-fixture', 'performance.fixture_loaded', [], assets=asset_count, relationships=relationship_count)


def login_clients(app, count: int) -> list[TestClient]:
    clients = []
    for _ in range(count):
        client = TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN})
        result = client.post('/api/login', json={'name': 'performance-host', 'password': PASSWORD})
        if result.status_code != 200:
            raise RuntimeError('Synthetic performance login failed')
        client.headers['X-CSRF-Token'] = result.json()['csrf']
        clients.append(client)
    return clients


def request(client: TestClient, method: str, path: str, **kwargs) -> float:
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    duration = elapsed(started)
    if response.status_code != 200:
        raise RuntimeError(f'Synthetic API request failed: {method} {path} returned {response.status_code}')
    return duration


def save_body(session: int) -> dict:
    if APP_NAME == 'Harbinger':
        return {
            'kind': 'finding',
            'data': {
                'title': f'Synthetic performance note {session}',
                'observation': 'Synthetic local performance fixture.',
                'asset_ids': [], 'evidence_ids': [], 'owner_id': '',
            },
        }
    return {
        'kind': 'draft',
        'data': {
            'title': f'Synthetic performance draft {session}',
            'description': 'Synthetic local performance fixture.',
            'evidence_ids': [], 'owner_id': '', 'lead_id': '',
        },
    }


def session_work(client: TestClient, session: int, asset_count: int) -> tuple[float, float]:
    number = (session * 7919) % asset_count
    search = request(client, 'GET', f'/api/assets?q=synthetic-{number:05d}&offset=0&limit=100')
    save = request(client, 'POST', '/api/records', json=save_body(session))
    return search, save


def main() -> int:
    args = parse_args()
    for name in ('assets', 'relationships', 'sessions'):
        positive(name, getattr(args, name))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f'{APP_NAME.lower()}-performance-') as temporary:
        store = initialize(Path(temporary) / 'state', ORIGIN, 'Synthetic performance')
        role = 'captain' if APP_NAME == 'Harbinger' else 'lead_scribe'
        add_user(store, 'performance-host', role, PASSWORD, APP_NAME)
        phase = time.perf_counter()
        load_fixture(store, args.assets, args.relationships)
        fixture_seconds = elapsed(phase)
        app = create_app(store.root)
        clients = login_clients(app, args.sessions)
        try:
            phase = time.perf_counter()
            request(clients[0], 'GET', '/api/assets?offset=0&limit=100')
            request(clients[0], 'GET', '/api/graph')
            initial_view_seconds = elapsed(phase)
            with ThreadPoolExecutor(max_workers=args.sessions) as executor:
                results = list(executor.map(lambda item: session_work(item[1], item[0], args.assets), enumerate(clients)))
        finally:
            for client in clients:
                client.close()
        searches = [item[0] for item in results]
        saves = [item[1] for item in results]
        timings = {
            'fixture': rounded(fixture_seconds),
            'initial_view': rounded(initial_view_seconds),
            'search_p95': rounded(percentile(searches, 0.95)),
            'save_p95': rounded(percentile(saves, 0.95)),
            'total': rounded(elapsed(started)),
        }
        thresholds = {
            'fixture': args.max_fixture_seconds,
            'initial_view': args.max_initial_view_seconds,
            'search_p95': args.max_p95_seconds,
            'save_p95': args.max_p95_seconds,
        }
        within = all(timings[name] <= limit for name, limit in thresholds.items())
        store.verify()
        print(json.dumps({
            'synthetic': True,
            'production_api': True,
            'browser_rendering': False,
            'network_targets': False,
            'scanners': False,
            'app': APP_NAME,
            'host': {'platform': platform.system(), 'machine': platform.machine()},
            'input': {'assets': args.assets, 'relationships': args.relationships, 'sessions': args.sessions},
            'timings_seconds': timings,
            'thresholds_seconds': thresholds,
            'within_thresholds': within,
        }, sort_keys=True))
        return 0 if within else 1


if __name__ == '__main__':
    raise SystemExit(main())
