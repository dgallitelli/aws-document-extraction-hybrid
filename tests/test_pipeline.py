from __future__ import annotations

from dataclasses import replace

import pytest
from botocore.exceptions import ClientError

from idp_starter import (
    DocumentPipeline,
    DocumentSource,
    FieldValue,
    ProviderInputError,
    ProviderResult,
)
from idp_starter.config import ProviderConfig


class FakeProvider:
    def __init__(self, name, outcome):
        self.name = name
        self.outcome = outcome

    def extract(self, source, profile):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class RecordingFactory:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def __call__(self, config):
        self.calls.append(config.name)
        return FakeProvider(config.name, self.outcomes[config.name])


def candidate(value, source, confidence=0.9):
    return FieldValue(value=value, source=source, confidence=confidence)


def reason_codes(result):
    return [reason.code for reason in result.review_reasons]


def test_complete_primary_stops_before_fallback(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("A-1", "primary"),
                    "total_amount": candidate("12.50", "primary"),
                    "effective_date": candidate("2026-01-01", "primary"),
                },
            )
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert result.status == "accepted"
    assert result.providers_attempted == ["primary"]
    assert factory.calls == ["primary"]


def test_fallback_fills_only_missing_field(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={"document_id": candidate("A-1", "primary")},
            ),
            "fallback": ProviderResult(
                "fallback",
                fields={
                    "document_id": candidate("different", "fallback"),
                    "total_amount": candidate("12.50", "fallback"),
                },
            ),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert result.fields["document_id"].value == "A-1"
    assert result.fields["total_amount"].source == "fallback"
    assert result.alternatives["document_id"][0].value == "different"
    assert "conflicting_provider_values" in reason_codes(result)


def test_fallback_fills_optional_fields_before_stopping(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("A-1", "primary"),
                    "total_amount": candidate("12.50", "primary"),
                },
            ),
            "fallback": ProviderResult(
                "fallback",
                fields={
                    "effective_date": candidate(
                        "2026-01-01",
                        "fallback",
                    )
                },
            ),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == ["primary", "fallback"]
    assert result.fields["effective_date"].source == "fallback"
    assert result.status == "accepted"


def test_low_confidence_and_invalid_value_run_fallback(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("A-1", "primary", 0.7),
                    "total_amount": candidate("not-a-number", "primary", 0.9),
                },
            ),
            "fallback": ProviderResult("fallback"),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == ["primary", "fallback"]
    assert result.status == "needs_review"
    assert "confidence_below_threshold" in reason_codes(result)
    assert "invalid_value_type" in reason_codes(result)


def test_disabling_quality_fallback_stops_but_reviews(profile):
    profile = replace(profile, fallback_on_low_confidence=False)
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("A-1", "primary", 0.7),
                    "total_amount": candidate("12", "primary"),
                    "effective_date": candidate("2026-01-01", "primary"),
                },
            )
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == ["primary"]
    assert result.status == "needs_review"


def test_provider_failure_is_sanitized_and_partial_result_survives(profile):
    factory = RecordingFactory(
        {
            "primary": RuntimeError("sensitive document detail"),
            "fallback": ProviderResult(
                "fallback",
                fields={
                    "document_id": candidate("A-1", "fallback"),
                    "total_amount": candidate("0", "fallback"),
                },
            ),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert result.fields["total_amount"].value == "0"
    assert result.status == "accepted"
    assert "primary failed: RuntimeError" in result.warnings
    assert "sensitive document detail" not in " ".join(result.warnings)


def test_provider_input_error_runs_fallback(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderInputError("unsupported input detail"),
            "fallback": ProviderResult(
                "fallback",
                fields={
                    "document_id": candidate("A-1", "fallback"),
                    "total_amount": candidate("12", "fallback"),
                    "effective_date": candidate("2026-01-01", "fallback"),
                },
            ),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == ["primary", "fallback"]
    assert result.fields["document_id"].source == "fallback"
    assert "provider_input_unsupported" in reason_codes(result)
    assert "unsupported input detail" not in " ".join(result.warnings)


def test_semantically_equal_candidate_is_not_conflict(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate(" A-1 ", "primary", 0.7),
                    "total_amount": candidate("12.50", "primary", 0.7),
                },
            ),
            "fallback": ProviderResult(
                "fallback",
                fields={
                    "document_id": candidate("A-1", "fallback"),
                    "total_amount": candidate("12.5", "fallback"),
                },
            ),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert result.alternatives.keys() == {"document_id", "total_amount"}
    assert "conflicting_provider_values" not in reason_codes(result)


def test_string_identifier_with_underscores_is_valid(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("INV_2026_001", "primary"),
                    "total_amount": candidate("12.50", "primary"),
                    "effective_date": candidate("2026-01-01", "primary"),
                },
            )
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert result.status == "accepted"
    assert "invalid_value_type" not in reason_codes(result)


def test_unconfigured_and_blank_fields_are_ignored(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("A-1", "primary"),
                    "total_amount": candidate(False, "primary"),
                    "unknown": candidate("secret", "primary"),
                    "effective_date": candidate("", "primary"),
                },
            )
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert result.fields["total_amount"].value is False
    assert "unknown" not in result.fields
    assert any("ignored unconfigured field unknown" in item for item in result.warnings)


def test_all_providers_fail(profile):
    factory = RecordingFactory(
        {"primary": RuntimeError("one"), "fallback": TimeoutError("two")}
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert result.status == "needs_review"
    assert result.fields == {}
    assert "no_fields_extracted" in reason_codes(result)


def test_fatal_configuration_error_stops_before_fallback(profile):
    factory = RecordingFactory(
        {
            "primary": ValueError("invalid configuration"),
            "fallback": ProviderResult(
                "fallback",
                fields={
                    "document_id": candidate("A-1", "fallback"),
                    "total_amount": candidate("12", "fallback"),
                },
            ),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == ["primary"]
    assert result.status == "needs_review"
    assert "fatal_provider_error" in reason_codes(result)


def test_fatal_client_error_stops_before_fallback(profile):
    error = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "sensitive detail",
            }
        },
        "AnalyzeDocument",
    )
    factory = RecordingFactory(
        {
            "primary": error,
            "fallback": ProviderResult("fallback"),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == ["primary"]
    assert "authorization_or_policy_error" in reason_codes(result)
    assert "sensitive detail" not in " ".join(result.warnings)


def test_retryable_client_error_runs_fallback(profile):
    error = ClientError(
        {
            "Error": {
                "Code": "ThrottlingException",
                "Message": "sensitive detail",
            }
        },
        "AnalyzeDocument",
    )
    factory = RecordingFactory(
        {
            "primary": error,
            "fallback": ProviderResult(
                "fallback",
                fields={
                    "document_id": candidate("A-1", "fallback"),
                    "total_amount": candidate("12", "fallback"),
                    "effective_date": candidate("2026-01-01", "fallback"),
                },
            ),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == ["primary", "fallback"]
    assert result.status == "accepted"
    assert "sensitive detail" not in " ".join(result.warnings)


def test_model_provenance_requires_review(profile):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": FieldValue(
                        "A-1",
                        "primary",
                        provenance={"requires_review": True},
                    ),
                    "total_amount": candidate("12", "primary"),
                },
            ),
            "fallback": ProviderResult("fallback"),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert "provider_requires_review" in reason_codes(result)


def test_total_deadline_marks_result_for_review(profile, monkeypatch):
    profile = replace(profile, deadline_seconds=1)
    monotonic_values = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(
        "idp_starter.pipeline.time.monotonic",
        lambda: next(monotonic_values),
    )
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("A-1", "primary"),
                    "total_amount": candidate("12", "primary"),
                },
            )
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert "processing_deadline_exceeded" in reason_codes(result)


def test_subsecond_remaining_budget_starts_no_provider(profile, monkeypatch):
    profile = replace(profile, deadline_seconds=1)
    monotonic_values = iter([0.0, 0.5])
    monkeypatch.setattr(
        "idp_starter.pipeline.time.monotonic",
        lambda: next(monotonic_values),
    )
    factory = RecordingFactory({})

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert factory.calls == []
    assert "processing_deadline_exceeded" in reason_codes(result)


def test_provider_timeouts_are_clamped_to_remaining_budget():
    config = ProviderConfig(
        name="bda",
        type="bda",
        options={
            "connect_timeout_seconds": 10,
            "read_timeout_seconds": 20,
            "timeout_seconds": 30,
        },
    )

    bounded = DocumentPipeline._bounded_config(config, 2.5)

    assert bounded.options["connect_timeout_seconds"] == 2.5
    assert bounded.options["read_timeout_seconds"] == 2.5
    assert bounded.options["timeout_seconds"] == 2.5


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1_000"])
def test_non_finite_or_python_only_number_is_invalid(profile, value):
    factory = RecordingFactory(
        {
            "primary": ProviderResult(
                "primary",
                fields={
                    "document_id": candidate("A-1", "primary"),
                    "total_amount": candidate(value, "primary"),
                    "effective_date": candidate("2026-01-01", "primary"),
                },
            ),
            "fallback": ProviderResult("fallback"),
        }
    )

    result = DocumentPipeline(profile, factory).process(
        DocumentSource("bucket", "document.pdf")
    )

    assert "invalid_value_type" in reason_codes(result)
