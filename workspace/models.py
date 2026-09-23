"""Strict browser inputs. The model/transport cannot set trusted record state."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Strict):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=512)


class Finding(Strict):
    title: str = Field(min_length=1, max_length=200)
    observation: str = Field(min_length=1, max_length=50000)
    impact: str = Field(default="", max_length=50000)
    steps: str = Field(default="", max_length=50000)
    notes: str = Field(default="", max_length=50000)
    asset_ids: list[str] = Field(default_factory=list, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_needed: str = Field(default="", max_length=1000)
    owner_id: str = Field(default="", max_length=80)
    evidence_state: Literal["candidate", "observed", "confirmed", "incomplete", "unsupported"] = "candidate"
    writing_state: Literal["draft", "needs_evidence", "ready", "submitted"] = "draft"


class Draft(Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=100000)
    impact: str = Field(default="", max_length=50000)
    remediation: str = Field(default="", max_length=50000)
    references: str = Field(default="", max_length=20000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    owner_id: str = Field(default="", max_length=80)
    lead_id: str = Field(default="", max_length=80)


class NewRecord(Strict):
    kind: Literal["finding", "draft", "comment"]
    data: dict


class EditRecord(Strict):
    base_revision_id: str = Field(min_length=1, max_length=80)
    data: dict


class MergeReview(Strict):
    upload_revision_id: str = Field(min_length=1, max_length=80)
    preview_revision_id: str = Field(min_length=1, max_length=80)
    preview_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledged: bool = False


class EvidenceApproval(Strict):
    base_revision_id: str = Field(min_length=1, max_length=80)
    artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Question(Strict):
    finding_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=10000)


class TransferSelection(Strict):
    record_ids: list[str] = Field(min_length=1, max_length=1000)
    recipient_id: str = Field(min_length=1, max_length=80)


class Export(TransferSelection):
    review_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ConflictResolution(Strict):
    decision: Literal['keep_local', 'accept_incoming']
    conflict_revision_id: str = Field(min_length=1, max_length=80)
    manifest_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    local_revisions: dict[str, str] = Field(max_length=1000)


class DeliverySelection(Strict):
    draft_id: str = Field(min_length=1, max_length=80)
    draft_revision_id: str = Field(min_length=1, max_length=80)
    report_id: str = Field(min_length=1, max_length=80)


class Review(DeliverySelection):
    proposal_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
    proposal_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class DeliveryAction(Strict):
    delivery_revision_id: str = Field(min_length=1, max_length=80)
    payload_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class DeliveryReconcile(DeliveryAction):
    remote_id: str = Field(pattern=r'^[1-9][0-9]{0,18}$')

    @field_validator('remote_id')
    @classmethod
    def postgres_bigint(cls, value: str) -> str:
        if int(value) > 9223372036854775807:
            raise ValueError('Ghostwriter ID exceeds PostgreSQL bigint range')
        return value
