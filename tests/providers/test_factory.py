from __future__ import annotations

import pytest

from idp_starter.providers import _resolve


def test_resolve_supports_embedded_environment_references(monkeypatch):
    monkeypatch.setenv("IDP_OUTPUT_BUCKET", "output-bucket")

    assert (
        _resolve("s3://${IDP_OUTPUT_BUCKET}/bda")
        == "s3://output-bucket/bda"
    )


def test_resolve_rejects_missing_embedded_environment_reference(monkeypatch):
    monkeypatch.delenv("IDP_OUTPUT_BUCKET", raising=False)

    with pytest.raises(ValueError, match="IDP_OUTPUT_BUCKET"):
        _resolve("s3://${IDP_OUTPUT_BUCKET}/bda")
