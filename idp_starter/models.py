"""Stable domain models shared by all extraction providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field as dataclass_field
from typing import Any


@dataclass(frozen=True)
class DocumentSource:
    """An input document stored in Amazon S3."""

    bucket: str
    key: str
    request_id: str | None = None

    @property
    def s3_uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


@dataclass(frozen=True)
class FieldValue:
    """A normalized field candidate with provenance."""

    value: Any
    source: str
    confidence: float | None = None
    raw_key: str | None = None
    provenance: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass
class ProviderResult:
    """Normalized output produced by one AWS provider."""

    provider: str
    fields: dict[str, FieldValue] = dataclass_field(default_factory=dict)
    raw_text: str = ""
    tables: list[Any] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        result = asdict(self)
        if not include_raw:
            for key in ("raw_text", "tables", "metadata"):
                result.pop(key)
        return result


@dataclass(frozen=True)
class ReviewReason:
    """Stable machine-readable reason for human review."""

    code: str
    message: str
    field: str | None = None
    provider: str | None = None
    details: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass
class ExtractionResult:
    """Customer-independent output envelope."""

    profile: str
    profile_version: str
    status: str
    fields: dict[str, FieldValue]
    alternatives: dict[str, list[FieldValue]]
    providers_attempted: list[str]
    warnings: list[str]
    review_reasons: list[ReviewReason]
    timings_ms: dict[str, int]
    raw_text: dict[str, str]
    tables: dict[str, list[Any]]
    provider_metadata: dict[str, dict[str, Any]]

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        result = asdict(self)
        if not include_raw:
            for key in ("raw_text", "tables", "provider_metadata"):
                result.pop(key)
        return result
