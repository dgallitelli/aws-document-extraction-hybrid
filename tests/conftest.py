from __future__ import annotations

import pytest

from idp_starter.config import DocumentProfile


@pytest.fixture
def profile() -> DocumentProfile:
    return DocumentProfile.from_dict(
        {
            "name": "test-document",
            "version": "1.0",
            "fields": {
                "document_id": {
                    "required": True,
                    "aliases": ["DOCUMENT_NUMBER"],
                    "require_confidence": True,
                    "confidence_thresholds": {
                        "primary": 0.8,
                        "fallback": 0.8,
                    },
                },
                "total_amount": {
                    "required": True,
                    "aliases": ["TOTAL", "AMOUNT_DUE"],
                    "value_type": "number",
                    "require_confidence": True,
                    "confidence_thresholds": {
                        "primary": 0.8,
                        "fallback": 0.8,
                    },
                },
                "effective_date": {
                    "aliases": ["DOCUMENT_DATE"],
                    "value_type": "date",
                },
            },
            "providers": [
                {"name": "primary", "type": "fake"},
                {"name": "fallback", "type": "fake"},
            ],
        }
    )
