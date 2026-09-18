"""Provider orchestration, deterministic merging, and review signaling."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Protocol

from botocore.exceptions import ClientError, ParamValidationError

from .config import DocumentProfile, ProviderConfig
from .errors import ProviderInputError
from .models import (
    DocumentSource,
    ExtractionResult,
    FieldValue,
    ProviderResult,
    ReviewReason,
)


class Provider(Protocol):
    name: str

    def extract(
        self, source: DocumentSource, profile: DocumentProfile
    ) -> ProviderResult: ...


ProviderFactory = Callable[[ProviderConfig], Provider]


class DocumentPipeline:
    def __init__(
        self, profile: DocumentProfile, provider_factory: ProviderFactory
    ) -> None:
        self.profile = profile
        self.provider_factory = provider_factory

    def process(self, source: DocumentSource) -> ExtractionResult:
        fields: dict[str, FieldValue] = {}
        alternatives: dict[str, list[FieldValue]] = {}
        attempted: list[str] = []
        warnings: list[str] = []
        timings_ms: dict[str, int] = {}
        raw_text: dict[str, str] = {}
        tables: dict[str, list[object]] = {}
        metadata: dict[str, dict[str, object]] = {}
        operational_review_reasons: list[ReviewReason] = []
        deadline_at = (
            time.monotonic() + self.profile.deadline_seconds
            if self.profile.deadline_seconds is not None
            else None
        )

        for provider_config in self.profile.providers:
            if deadline_at is not None:
                remaining = deadline_at - time.monotonic()
                if remaining < 1:
                    operational_review_reasons.append(
                        ReviewReason(
                            code="processing_deadline_exceeded",
                            message="Processing deadline exceeded",
                        )
                    )
                    break
                provider_config = self._bounded_config(provider_config, remaining)
            attempted.append(provider_config.name)
            started = time.perf_counter()
            try:
                provider = self.provider_factory(provider_config)
                result = provider.extract(source, self.profile)
            except ProviderInputError as exc:
                warnings.append(
                    f"{provider_config.name} skipped: {exc.__class__.__name__}"
                )
                operational_review_reasons.append(
                    ReviewReason(
                        code="provider_input_unsupported",
                        message="Provider does not support this input",
                        provider=provider_config.name,
                    )
                )
                timings_ms[provider_config.name] = round(
                    (time.perf_counter() - started) * 1000
                )
                continue
            except (ValueError, ParamValidationError) as exc:
                warnings.append(
                    f"{provider_config.name} stopped: {exc.__class__.__name__}"
                )
                operational_review_reasons.append(
                    ReviewReason(
                        code="fatal_provider_error",
                        message="Fatal provider configuration or input error",
                        provider=provider_config.name,
                    )
                )
                timings_ms[provider_config.name] = round(
                    (time.perf_counter() - started) * 1000
                )
                break
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "ClientError")
                warnings.append(f"{provider_config.name} failed: {error_code}")
                timings_ms[provider_config.name] = round(
                    (time.perf_counter() - started) * 1000
                )
                if self._is_fatal_client_error(error_code):
                    operational_review_reasons.append(
                        ReviewReason(
                            code="authorization_or_policy_error",
                            message="Authorization or request policy error",
                            provider=provider_config.name,
                            details={"error_code": error_code},
                        )
                    )
                    break
                continue
            except Exception as exc:
                warnings.append(
                    f"{provider_config.name} failed: {exc.__class__.__name__}"
                )
                timings_ms[provider_config.name] = round(
                    (time.perf_counter() - started) * 1000
                )
                continue

            timings_ms[provider_config.name] = round(
                (time.perf_counter() - started) * 1000
            )
            warnings.extend(
                f"{provider_config.name}: {warning}" for warning in result.warnings
            )
            if result.raw_text:
                raw_text[provider_config.name] = result.raw_text
            if result.tables:
                tables[provider_config.name] = result.tables
            if result.metadata:
                metadata[provider_config.name] = result.metadata

            for field_name, candidate in result.fields.items():
                if field_name not in self.profile.field_by_name:
                    warnings.append(
                        f"{provider_config.name}: ignored unconfigured field "
                        f"{field_name}"
                    )
                    continue
                if candidate.value is None or candidate.value == "":
                    continue
                existing = fields.get(field_name)
                if existing is None:
                    fields[field_name] = candidate
                else:
                    alternatives.setdefault(field_name, []).append(candidate)

            if deadline_at is not None and time.monotonic() >= deadline_at:
                operational_review_reasons.append(
                    ReviewReason(
                        code="processing_deadline_exceeded",
                        message="Processing deadline exceeded",
                    )
                )
                break
            if self._can_stop(fields):
                break

        review_reasons = self._review_reasons(fields, alternatives)
        review_reasons.extend(operational_review_reasons)
        if not fields:
            review_reasons.append(
                ReviewReason(
                    code="no_fields_extracted",
                    message="No fields were extracted",
                )
            )
        warnings = list(dict.fromkeys(warnings))
        review_reasons = list(
            {
                (
                    reason.code,
                    reason.field,
                    reason.provider,
                    str(reason.details),
                ): reason
                for reason in review_reasons
            }.values()
        )

        return ExtractionResult(
            profile=self.profile.name,
            profile_version=self.profile.version,
            status="needs_review" if review_reasons else "accepted",
            fields=fields,
            alternatives=alternatives,
            providers_attempted=attempted,
            warnings=warnings,
            review_reasons=review_reasons,
            timings_ms=timings_ms,
            raw_text=raw_text,
            tables=tables,
            provider_metadata=metadata,
        )

    def _can_stop(self, fields: dict[str, FieldValue]) -> bool:
        if any(field.name not in fields for field in self.profile.fields):
            return False
        if not self.profile.fallback_on_low_confidence:
            return True
        return not self._quality_reasons(fields) and not self._validation_reasons(fields)

    def _review_reasons(
        self,
        fields: dict[str, FieldValue],
        alternatives: dict[str, list[FieldValue]],
    ) -> list[ReviewReason]:
        reasons = [
            ReviewReason(
                code="missing_required_field",
                message="Required field is missing",
                field=field.name,
            )
            for field in self.profile.fields
            if field.required and field.name not in fields
        ]
        reasons.extend(self._quality_reasons(fields))
        reasons.extend(self._validation_reasons(fields))
        for field_name, candidates in alternatives.items():
            if any(
                not self._equivalent(fields[field_name].value, candidate.value)
                for candidate in candidates
            ):
                reasons.append(
                    ReviewReason(
                        code="conflicting_provider_values",
                        message="Providers returned conflicting values",
                        field=field_name,
                    )
                )
        return reasons

    def _quality_reasons(
        self, fields: dict[str, FieldValue]
    ) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        definitions = self.profile.field_by_name
        for field_name, candidate in fields.items():
            definition = definitions[field_name]
            if candidate.provenance.get("requires_review"):
                reasons.append(
                    ReviewReason(
                        code="provider_requires_review",
                        message="Provider output requires review",
                        field=field_name,
                        provider=candidate.source,
                    )
                )
            elif candidate.confidence is None:
                if definition.require_confidence:
                    reasons.append(
                        ReviewReason(
                            code="missing_confidence",
                            message="Field requires confidence but is unscored",
                            field=field_name,
                            provider=candidate.source,
                        )
                    )
            else:
                threshold = definition.confidence_thresholds.get(candidate.source)
                if threshold is None:
                    if definition.require_confidence:
                        reasons.append(
                            ReviewReason(
                                code="missing_confidence_threshold",
                                message="No confidence threshold is configured",
                                field=field_name,
                                provider=candidate.source,
                            )
                        )
                    continue
                if candidate.confidence >= threshold:
                    continue
                reasons.append(
                    ReviewReason(
                        code="confidence_below_threshold",
                        message="Field confidence is below threshold",
                        field=field_name,
                        provider=candidate.source,
                        details={
                            "confidence": candidate.confidence,
                            "threshold": threshold,
                        },
                    )
                )
        return reasons

    def _validation_reasons(
        self, fields: dict[str, FieldValue]
    ) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        definitions = self.profile.field_by_name
        for field_name, candidate in fields.items():
            definition = definitions[field_name]
            text = str(candidate.value)
            if definition.allowed_values and text not in definition.allowed_values:
                reasons.append(
                    ReviewReason(
                        code="unexpected_value",
                        message="Field value is not in the allowed set",
                        field=field_name,
                        provider=candidate.source,
                    )
                )
            if definition.pattern and re.fullmatch(definition.pattern, text) is None:
                reasons.append(
                    ReviewReason(
                        code="pattern_mismatch",
                        message="Field value does not match the configured pattern",
                        field=field_name,
                        provider=candidate.source,
                    )
                )
            if not self._matches_type(text, definition.value_type):
                reasons.append(
                    ReviewReason(
                        code="invalid_value_type",
                        message="Field value has the wrong type or format",
                        field=field_name,
                        provider=candidate.source,
                        details={"expected_type": definition.value_type},
                    )
                )
        return reasons

    @staticmethod
    def _matches_type(value: str, value_type: str) -> bool:
        try:
            if value_type == "number":
                if "_" in value:
                    return False
                number = Decimal(value.replace(",", ""))
                if not number.is_finite():
                    return False
            elif value_type == "integer":
                if "_" in value:
                    return False
                int(value)
            elif value_type == "date":
                date.fromisoformat(value)
            elif value_type == "boolean":
                if value.lower() not in {"true", "false"}:
                    return False
        except (InvalidOperation, ValueError):
            return False
        return True

    @staticmethod
    def _equivalent(left: object, right: object) -> bool:
        if left == right:
            return True
        left_text = str(left).strip()
        right_text = str(right).strip()
        if left_text == right_text:
            return True
        if "_" in left_text or "_" in right_text:
            return False
        try:
            left_number = Decimal(left_text.replace(",", ""))
            right_number = Decimal(right_text.replace(",", ""))
            return (
                left_number.is_finite()
                and right_number.is_finite()
                and left_number == right_number
            )
        except InvalidOperation:
            return False

    @staticmethod
    def _bounded_config(
        config: ProviderConfig, remaining_seconds: float
    ) -> ProviderConfig:
        options = dict(config.options)
        current_read_timeout = float(
            options.get("read_timeout_seconds", remaining_seconds)
        )
        options["read_timeout_seconds"] = max(
            1, min(current_read_timeout, remaining_seconds)
        )
        current_connect_timeout = float(
            options.get("connect_timeout_seconds", remaining_seconds)
        )
        options["connect_timeout_seconds"] = max(
            1, min(current_connect_timeout, remaining_seconds)
        )
        if config.type == "bda":
            current_timeout = float(options.get("timeout_seconds", remaining_seconds))
            options["timeout_seconds"] = max(
                1, min(current_timeout, remaining_seconds)
            )
        return replace(config, options=options)

    @staticmethod
    def _is_fatal_client_error(error_code: str) -> bool:
        return error_code in {
            "AccessDenied",
            "AccessDeniedException",
            "InvalidParameterException",
            "UnauthorizedException",
            "ValidationException",
        }
