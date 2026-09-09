import hashlib
import json
from pathlib import Path
import uuid
import pytest

from workspace.store import utc
from workspace.transfer import CONTRACT_SHA256, validate_manifest


def sample_manifest():
    return {
        "schema_version": 1,
        "kind": "record_bundle",
        "bundle_id": str(uuid.uuid4()),
        "engagement_id": str(uuid.uuid4()),
        "source_instance": str(uuid.uuid4()),
        "recipient_id": str(uuid.uuid4()),
        "created_at": utc(),
        "records": [],
        "files": [],
    }


def test_checked_in_contract_hash_and_validation():
    path = Path(__file__).parents[1] / "contracts" / "team-transfer-v1.schema.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CONTRACT_SHA256
    assert (path.parent / "CONTRACT_SHA256").read_text().strip() == CONTRACT_SHA256
    validate_manifest(sample_manifest())


def test_contract_rejects_unexpected_fields_and_kinds():
    invalid = sample_manifest()
    invalid["unexpected"] = "blocked"
    try:
        validate_manifest(invalid)
    except ValueError as error:
        assert "contract" in str(error).lower()
    else:
        raise AssertionError("unexpected fields must fail")

    invalid = sample_manifest()
    invalid["records"] = [{
        "id": str(uuid.uuid4()),
        "kind": "arbitrary",
        "revision_id": str(uuid.uuid4()),
        "data": {},
        "updated_at": utc(),
    }]
    try:
        validate_manifest(invalid)
    except ValueError as error:
        assert "contract" in str(error).lower()
    else:
        raise AssertionError("unregistered record kinds must fail")


@pytest.mark.parametrize(('field', 'value'), [
    ('bundle_id', 'not-a-uuid'),
    ('engagement_id', 'not-a-uuid'),
    ('created_at', 'not-a-time'),
])
def test_contract_enforces_declared_uuid_and_time_formats(field, value):
    invalid = sample_manifest()
    invalid[field] = value
    with pytest.raises(ValueError):
        validate_manifest(invalid)
