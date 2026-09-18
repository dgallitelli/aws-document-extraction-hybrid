from __future__ import annotations

import pytest

from idp_starter import ConfigError, DocumentProfile, DocumentSource
from idp_starter.providers.textract import TextractProvider, _confidence


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, 0.0),
        (1, 0.01),
        (99, 0.99),
        (100, 1.0),
        (150, None),
        (True, None),
        (None, None),
    ],
)
def test_textract_confidence_is_percentage(raw, expected):
    assert _confidence(raw) == expected


def test_expense_normalization(profile):
    response = {
        "DocumentMetadata": {"Pages": 1},
        "ExpenseDocuments": [
            {
                "SummaryFields": [
                    {
                        "Type": {"Text": "DOCUMENT_NUMBER"},
                        "ValueDetection": {
                            "Text": "INV-1",
                            "Confidence": 99,
                            "Geometry": {"BoundingBox": {"Top": 0.1}},
                        },
                        "PageNumber": 1,
                    },
                    {
                        "Type": {"Text": "TOTAL"},
                        "ValueDetection": {"Text": "12.50", "Confidence": 91},
                    },
                ],
                "LineItemGroups": [{"LineItemGroupIndex": 1}],
                "Blocks": [{"BlockType": "LINE", "Text": "Invoice INV-1"}],
            }
        ],
    }
    provider = TextractProvider(
        "textract-expense", client=None, options={"mode": "expense"}
    )

    result = provider._normalize_expense(response, profile)

    assert result.fields["document_id"].value == "INV-1"
    assert result.fields["document_id"].confidence == 0.99
    assert result.fields["document_id"].provenance["page"] == 1
    assert result.fields["total_amount"].value == "12.50"
    assert result.raw_text == "Invoice INV-1"
    assert result.tables == [{"LineItemGroupIndex": 1}]


def test_expense_other_type_uses_detected_label(profile):
    response = {
        "ExpenseDocuments": [
            {
                "SummaryFields": [
                    {
                        "Type": {"Text": "OTHER"},
                        "LabelDetection": {"Text": "DOCUMENT_NUMBER"},
                        "ValueDetection": {
                            "Text": "INV-OTHER",
                            "Confidence": 95,
                        },
                    }
                ]
            }
        ]
    }
    provider = TextractProvider(
        "textract-expense",
        client=None,
        options={"mode": "expense"},
    )

    result = provider._normalize_expense(response, profile)

    assert result.fields["document_id"].value == "INV-OTHER"


def test_expense_normalizes_common_amount_and_unambiguous_date(profile):
    response = {
        "ExpenseDocuments": [
            {
                "SummaryFields": [
                    {
                        "Type": {"Text": "TOTAL"},
                        "ValueDetection": {
                            "Text": "$1,234.50",
                            "Confidence": 95,
                        },
                    },
                    {
                        "Type": {"Text": "DOCUMENT_DATE"},
                        "ValueDetection": {
                            "Text": "12/25/2025",
                            "Confidence": 94,
                        },
                    },
                ]
            }
        ]
    }
    provider = TextractProvider(
        "textract-expense",
        client=None,
        options={"mode": "expense"},
    )

    result = provider._normalize_expense(response, profile)

    assert result.fields["total_amount"].value == "1234.50"
    assert result.fields["total_amount"].provenance["raw_value"] == "$1,234.50"
    assert result.fields["effective_date"].value == "2025-12-25"
    assert (
        result.fields["effective_date"].provenance["raw_value"]
        == "12/25/2025"
    )


def test_id_normalization_preserves_normalized_value(profile):
    response = {
        "IdentityDocuments": [
            {
                "DocumentIndex": 1,
                "IdentityDocumentFields": [
                    {
                        "Type": {"Text": "DOCUMENT_NUMBER"},
                        "ValueDetection": {
                            "Text": "ID-9",
                            "Confidence": 88,
                            "NormalizedValue": {"Value": "ID9"},
                        },
                    }
                ],
                "Blocks": [],
            }
        ]
    }
    provider = TextractProvider(
        "textract-id", client=None, options={"mode": "id"}
    )

    result = provider._normalize_id(response, profile)

    assert result.fields["document_id"].confidence == 0.88
    assert result.fields["document_id"].provenance["normalized_value"] == {
        "Value": "ID9"
    }


class QueryClient:
    def __init__(self):
        self.request = None

    def analyze_document(self, **kwargs):
        self.request = kwargs
        return {
            "DocumentMetadata": {"Pages": 1},
            "Blocks": [
                {
                    "Id": "q1",
                    "BlockType": "QUERY",
                    "Query": {
                        "Alias": "document_id",
                        "Text": "What is document_id?",
                    },
                    "Relationships": [{"Type": "ANSWER", "Ids": ["a1"]}],
                },
                {
                    "Id": "a1",
                    "BlockType": "QUERY_RESULT",
                    "Text": "DOC-7",
                    "Confidence": 98,
                    "Page": 1,
                },
            ],
        }


def test_query_request_and_linked_answer(profile):
    client = QueryClient()
    provider = TextractProvider(
        "textract-queries", client=client, options={"mode": "queries"}
    )

    result = provider.extract(DocumentSource("bucket", "form.pdf"), profile)

    assert client.request["FeatureTypes"] == ["QUERIES"]
    assert client.request["QueriesConfig"]["Queries"][0]["Alias"] == "document_id"
    assert result.fields["document_id"].value == "DOC-7"
    assert result.fields["document_id"].confidence == 0.98


def test_queries_reject_more_than_fifteen_fields():
    with pytest.raises(ConfigError, match="at most 15"):
        DocumentProfile.from_dict(
            {
                "name": "too-many-queries",
                "version": "1",
                "fields": {f"field_{index}": {} for index in range(16)},
                "providers": [
                    {
                        "name": "queries",
                        "type": "textract",
                        "mode": "queries",
                    }
                ],
            }
        )


def test_queries_reject_non_english_configuration():
    with pytest.raises(ValueError, match="English only"):
        TextractProvider(
            "queries",
            client=QueryClient(),
            options={"mode": "queries", "language": "fr"},
        )


def test_queries_require_queries_feature_type():
    with pytest.raises(ValueError, match="requires QUERIES"):
        TextractProvider(
            "queries",
            client=QueryClient(),
            options={"mode": "queries", "feature_types": ["TABLES"]},
        )
