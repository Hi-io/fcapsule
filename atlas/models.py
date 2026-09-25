from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(StrictModel):
    environment: str | None = Field(default=None, max_length=80)
    cluster: str | None = Field(default=None, max_length=200)
    namespace: str | None = Field(default=None, max_length=200)
    service: str | None = Field(default=None, max_length=200)
    workload: str | None = Field(default=None, max_length=200)
    cnfc_id: str | None = Field(default=None, max_length=200)
    vnfc_id: str | None = Field(default=None, max_length=200)


class Observation(StrictModel):
    kind: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=1, max_length=120)
    value: Any
    unit: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=120)
    observed_at: datetime | None = None
    reference: str | None = Field(default=None, max_length=500)

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value


class Hypothesis(StrictModel):
    statement: str = Field(min_length=1, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    supporting_refs: list[str] = Field(default_factory=list, max_length=30)


class CaseEnvelope(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    normalization_version: str | None = Field(default=None, max_length=80)
    instance_id: str = Field(min_length=1, max_length=128)
    episode_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=1, ge=1, le=2147483647)
    observed_at: datetime
    scope: Scope
    summary: str = Field(min_length=1, max_length=2000)
    observations: list[Observation] = Field(default_factory=list, max_length=100)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=50)
    fingerprint: str | None = Field(default=None, max_length=256)

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value


class SearchRequest(StrictModel):
    query: str | None = Field(default=None, max_length=500)
    fingerprint: str | None = Field(default=None, max_length=256)
    instance_id: str | None = Field(default=None, max_length=128)
    scope: Scope | None = None
    observed_after: datetime | None = None
    observed_before: datetime | None = None
    # `before` is retained for the FCAPSule producer adapter.
    before: datetime | None = None
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("observed_after", "observed_before", "before")
    @classmethod
    def optional_timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value
