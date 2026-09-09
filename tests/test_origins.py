import uuid
import pytest

from workspace.cli import initialize
from workspace.origins import exact_origin, serving_port
from workspace.store import digest
from workspace.transfer import provision_keys, public_card


def test_origin_policy_separates_browser_peer_and_client_http(tmp_path):
    assert exact_origin('http://127.0.0.1:8711', 'synthetic', 'browser')[0] == 'http://127.0.0.1:8711'
    assert exact_origin('http://merlin:8711', 'synthetic', 'peer')[0] == 'http://merlin:8711'
    assert exact_origin('http://ghostwriter:8080', 'synthetic', 'ghostwriter')[0] == 'http://ghostwriter:8080'
    with pytest.raises(ValueError):
        exact_origin('http://merlin:8711', 'synthetic', 'browser')
    with pytest.raises(ValueError):
        exact_origin('http://harbinger:8710', 'client', 'peer')
    with pytest.raises(ValueError):
        exact_origin('https://user@example.test/', 'client', 'peer')


def test_peer_card_uses_the_explicit_reachable_origin(tmp_path):
    store = initialize(
        tmp_path / 'state', 'http://127.0.0.1:8711', 'Synthetic',
        engagement_id=str(uuid.uuid4()), peer_origin='http://merlin:8711',
    )
    provision_keys(store)
    card = public_card(store)
    assert card['origin'] == 'http://merlin:8711'
    assert len(digest(card)) == 64


def test_serving_port_must_match_the_approved_origin():
    _, local = exact_origin('http://127.0.0.1:8711', 'synthetic', 'browser')
    _, tls = exact_origin('https://merlin.example.test', 'client', 'browser')
    assert serving_port(local, None) == 8711
    assert serving_port(local, '8711') == 8711
    assert serving_port(tls, '443') == 443
    with pytest.raises(ValueError):
        serving_port(tls, '8711')
    with pytest.raises(ValueError):
        serving_port(local, 'not-a-port')
