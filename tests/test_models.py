from __future__ import annotations

import idp_starter

from idp_starter import ExtractionResult, ReviewReason


def test_review_reason_field_does_not_shadow_dataclass_helper():
    reason = ReviewReason(code="missing", message="Missing", field="value")

    assert reason.field == "value"
    assert idp_starter.ReviewReason is ReviewReason


def test_result_dict_omits_raw_artifacts_by_default():
    result = ExtractionResult(
        profile="test",
        profile_version="1",
        status="accepted",
        fields={},
        alternatives={},
        providers_attempted=[],
        warnings=[],
        review_reasons=[],
        timings_ms={},
        raw_text={"provider": "sensitive"},
        tables={"provider": ["row"]},
        provider_metadata={"provider": {"output": "s3://private/path"}},
    )

    assert "raw_text" not in result.to_dict()
    assert result.to_dict(include_raw=True)["raw_text"]["provider"] == "sensitive"
