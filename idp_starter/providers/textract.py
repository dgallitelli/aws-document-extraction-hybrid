"""Amazon Textract adapters with operation-specific normalization."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any
import unicodedata

from ..config import DocumentProfile
from ..errors import ProviderInputError
from ..models import DocumentSource, FieldValue, ProviderResult


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score / 100 if 0 <= score <= 100 else None


def _text_lines(blocks: list[dict[str, Any]]) -> str:
    lines = [
        block.get("Text", "")
        for block in blocks
        if block.get("BlockType") == "LINE" and block.get("Text")
    ]
    return "\n".join(lines)


def _normalize_number(value: str) -> str:
    text = "".join(
        character
        for character in value.strip()
        if unicodedata.category(character) != "Sc"
    )
    text = re.sub(r"\s+", "", text)
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text) is None:
        return value
    normalized = text.replace(",", "")
    if negative:
        normalized = f"-{normalized}"
    try:
        if not Decimal(normalized).is_finite():
            return value
    except InvalidOperation:
        return value
    return normalized


def _normalize_date(value: str) -> str:
    text = value.strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass

    for date_format in (
        "%Y/%m/%d",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue

    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        first, second, year = (int(part) for part in match.groups())
        if second > 12:
            month, day = first, second
        elif first > 12:
            day, month = first, second
        else:
            return value
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return value
    return value


def _normalize_value(value: str, value_type: str) -> str:
    if value_type == "number":
        return _normalize_number(value)
    if value_type == "date":
        return _normalize_date(value)
    return value


class TextractProvider:
    def __init__(self, name: str, client: Any, options: dict[str, Any]) -> None:
        self.name = name
        self.client = client
        self.mode = options.get("mode")
        if self.mode not in {"expense", "id", "queries"}:
            raise ValueError("Textract mode must be expense, id, or queries")
        self.feature_types = options.get("feature_types", ["QUERIES"])
        self.language = options.get("language", "en")
        if self.mode == "queries" and self.language != "en":
            raise ValueError("Textract Queries currently supports English only")
        if self.mode == "queries" and (
            not isinstance(self.feature_types, list)
            or "QUERIES" not in self.feature_types
        ):
            raise ValueError(
                "Textract Queries mode requires QUERIES in feature_types"
            )

    def extract(
        self, source: DocumentSource, profile: DocumentProfile
    ) -> ProviderResult:
        document = {"S3Object": {"Bucket": source.bucket, "Name": source.key}}
        if self.mode == "expense":
            response = self.client.analyze_expense(Document=document)
            return self.normalize_response(response, profile)
        if self.mode == "id":
            response = self.client.analyze_id(DocumentPages=[document])
            return self.normalize_response(response, profile)

        if len(profile.fields) > 15:
            raise ProviderInputError(
                "Synchronous Textract Queries supports at most 15 queries per page"
            )
        queries = [
            {
                "Text": field.query or field.description or f"What is {field.name}?",
                "Alias": field.name,
            }
            for field in profile.fields
        ]
        response = self.client.analyze_document(
            Document=document,
            FeatureTypes=self.feature_types,
            QueriesConfig={"Queries": queries},
        )
        return self.normalize_response(response, profile)

    def normalize_response(
        self,
        response: dict[str, Any],
        profile: DocumentProfile,
    ) -> ProviderResult:
        """Normalize a captured response for this configured operation."""

        if self.mode == "expense":
            return self._normalize_expense(response, profile)
        if self.mode == "id":
            return self._normalize_id(response, profile)
        return self._normalize_queries(response, profile)

    def _normalize_expense(
        self, response: dict[str, Any], profile: DocumentProfile
    ) -> ProviderResult:
        fields: dict[str, FieldValue] = {}
        raw_blocks: list[dict[str, Any]] = []
        line_items: list[Any] = []

        for document in response.get("ExpenseDocuments", []):
            raw_blocks.extend(document.get("Blocks", []))
            line_items.extend(document.get("LineItemGroups", []))
            for item in document.get("SummaryFields", []):
                normalized_type = item.get("Type", {}).get("Text")
                raw_key = (
                    item.get("LabelDetection", {}).get("Text")
                    if normalized_type == "OTHER"
                    else normalized_type
                ) or item.get("LabelDetection", {}).get("Text")
                value_detection = item.get("ValueDetection", {})
                value = value_detection.get("Text")
                if not raw_key or value in {None, ""}:
                    continue
                canonical = profile.canonical_name(raw_key)
                if canonical is None:
                    continue
                normalized_value = _normalize_value(
                    value,
                    profile.field_by_name[canonical].value_type,
                )
                provenance = {
                    "page": item.get("PageNumber"),
                    "geometry": value_detection.get("Geometry"),
                }
                if normalized_value != value:
                    provenance["raw_value"] = value
                candidate = FieldValue(
                    value=normalized_value,
                    source=self.name,
                    confidence=_confidence(value_detection.get("Confidence")),
                    raw_key=raw_key,
                    provenance=provenance,
                )
                self._keep_best(fields, canonical, candidate)

        return ProviderResult(
            provider=self.name,
            fields=fields,
            raw_text=_text_lines(raw_blocks),
            tables=line_items,
            metadata={
                "api": "AnalyzeExpense",
                "pages": response.get("DocumentMetadata", {}).get("Pages"),
            },
        )

    def _normalize_id(
        self, response: dict[str, Any], profile: DocumentProfile
    ) -> ProviderResult:
        fields: dict[str, FieldValue] = {}
        raw_blocks: list[dict[str, Any]] = []

        for document in response.get("IdentityDocuments", []):
            raw_blocks.extend(document.get("Blocks", []))
            for item in document.get("IdentityDocumentFields", []):
                raw_key = item.get("Type", {}).get("Text")
                value_detection = item.get("ValueDetection", {})
                value = value_detection.get("Text")
                if not raw_key or value in {None, ""}:
                    continue
                canonical = profile.canonical_name(raw_key)
                if canonical is None:
                    continue
                candidate = FieldValue(
                    value=value,
                    source=self.name,
                    confidence=_confidence(value_detection.get("Confidence")),
                    raw_key=raw_key,
                    provenance={
                        "document_index": document.get("DocumentIndex"),
                        "normalized_value": value_detection.get("NormalizedValue"),
                    },
                )
                self._keep_best(fields, canonical, candidate)

        return ProviderResult(
            provider=self.name,
            fields=fields,
            raw_text=_text_lines(raw_blocks),
            metadata={
                "api": "AnalyzeID",
                "pages": response.get("DocumentMetadata", {}).get("Pages"),
            },
        )

    def _normalize_queries(
        self, response: dict[str, Any], profile: DocumentProfile
    ) -> ProviderResult:
        blocks = response.get("Blocks", [])
        by_id = {
            block["Id"]: block for block in blocks if isinstance(block.get("Id"), str)
        }
        fields: dict[str, FieldValue] = {}

        for query in blocks:
            if query.get("BlockType") != "QUERY":
                continue
            query_data = query.get("Query", {})
            raw_key = query_data.get("Alias") or query_data.get("Text")
            if not raw_key:
                continue
            canonical = profile.canonical_name(raw_key)
            if canonical is None:
                continue
            for relationship in query.get("Relationships", []):
                if relationship.get("Type") != "ANSWER":
                    continue
                for answer_id in relationship.get("Ids", []):
                    answer = by_id.get(answer_id, {})
                    value = answer.get("Text")
                    if value in {None, ""}:
                        continue
                    candidate = FieldValue(
                        value=value,
                        source=self.name,
                        confidence=_confidence(answer.get("Confidence")),
                        raw_key=raw_key,
                        provenance={
                            "page": answer.get("Page"),
                            "geometry": answer.get("Geometry"),
                            "query": query_data.get("Text"),
                        },
                    )
                    self._keep_best(fields, canonical, candidate)

        table_refs = [
            {
                "id": block.get("Id"),
                "page": block.get("Page"),
                "confidence": _confidence(block.get("Confidence")),
            }
            for block in blocks
            if block.get("BlockType") == "TABLE"
        ]
        return ProviderResult(
            provider=self.name,
            fields=fields,
            raw_text=_text_lines(blocks),
            tables=table_refs,
            metadata={
                "api": "AnalyzeDocument",
                "pages": response.get("DocumentMetadata", {}).get("Pages"),
            },
        )

    @staticmethod
    def _keep_best(
        fields: dict[str, FieldValue], name: str, candidate: FieldValue
    ) -> None:
        existing = fields.get(name)
        existing_score = existing.confidence if existing else None
        candidate_score = candidate.confidence
        if existing is None or (
            candidate_score is not None
            and (existing_score is None or candidate_score > existing_score)
        ):
            fields[name] = candidate
