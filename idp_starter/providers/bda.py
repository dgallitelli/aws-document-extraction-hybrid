"""Amazon Bedrock Data Automation adapter."""

from __future__ import annotations

import json
import hashlib
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from ..config import DocumentProfile
from ..errors import ProviderInputError
from ..models import DocumentSource, FieldValue, ProviderResult


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if 0 <= score <= 1 else None


def _bool_option(options: dict[str, Any], key: str, default: bool) -> bool:
    value = options.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"BDA option {key} must be a boolean")
    return value


class BdaProvider:
    def __init__(
        self,
        name: str,
        client: Any,
        s3_client: Any,
        options: dict[str, Any],
    ) -> None:
        self.name = name
        self.client = client
        self.s3 = s3_client
        self.mode = options.get("mode", "async")
        if self.mode not in {"sync", "async"}:
            raise ValueError("BDA mode must be sync or async")
        self.profile_arn = options.get("data_automation_profile_arn")
        if not self.profile_arn:
            raise ValueError("BDA provider requires data_automation_profile_arn")
        self.project_arn = options.get("data_automation_project_arn")
        self.stage = options.get("stage", "LIVE")
        self.blueprints = options.get("blueprints", [])
        self.output_s3_uri = options.get("output_s3_uri")
        self.timeout_seconds = int(options.get("timeout_seconds", 300))
        self.poll_seconds = float(options.get("poll_seconds", 2))
        self.max_output_object_bytes = int(
            options.get("max_output_object_bytes", 10_000_000)
        )
        self.cleanup_output = _bool_option(options, "cleanup_output", False)

    def extract(
        self, source: DocumentSource, profile: DocumentProfile
    ) -> ProviderResult:
        if self.mode == "sync":
            if PurePosixPath(source.key).suffix.lower() not in {
                ".gif",
                ".jpeg",
                ".jpg",
                ".png",
                ".webp",
            }:
                raise ProviderInputError(
                    "Synchronous BDA accepts image inputs only"
                )
            response = self.client.invoke_data_automation(**self._request(source))
            return self.normalize_response(response, profile)

        if not self.output_s3_uri:
            raise ValueError("Async BDA provider requires output_s3_uri")
        token_source = (
            f"{source.request_id}|{source.s3_uri}|{profile.name}|"
            f"{profile.version}|{self.project_arn or self.blueprints}"
            if source.request_id
            else str(uuid4())
        )
        client_token = hashlib.sha256(token_source.encode("utf-8")).hexdigest()
        job_uri = f"{self.output_s3_uri.rstrip('/')}/{client_token}/"
        request = self._request(source)
        request["outputConfiguration"] = {"s3Uri": job_uri}
        request["clientToken"] = client_token
        result = None

        try:
            response = self.client.invoke_data_automation_async(**request)
            invocation_arn = response["invocationArn"]
            deadline = time.monotonic() + self.timeout_seconds
            while time.monotonic() < deadline:
                status = self.client.get_data_automation_status(
                    invocationArn=invocation_arn
                )
                state = status.get("status")
                if state == "Success":
                    output_uri = status["outputConfiguration"]["s3Uri"]
                    if not self._is_within_prefix(output_uri, job_uri):
                        raise RuntimeError(
                            "BDA returned output outside the requested S3 prefix"
                        )
                    field_payloads, text_payloads = self._s3_payloads(
                        output_uri,
                        allowed_prefix=job_uri,
                    )
                    result = self._normalize(
                        field_payloads,
                        profile,
                        {
                            "api": "InvokeDataAutomationAsync",
                            "invocation_arn": invocation_arn,
                            "output_s3_uri": output_uri,
                            "duration_seconds": status.get("jobDurationInSeconds"),
                        },
                        text_payloads=text_payloads,
                    )
                    break
                if state in {"ClientError", "ServiceError"}:
                    error_type = status.get("errorType")
                    error_message = status.get("errorMessage")
                    detail = ": ".join(
                        str(value)
                        for value in (error_type, error_message)
                        if value
                    )
                    suffix = f" ({detail})" if detail else ""
                    raise RuntimeError(
                        f"BDA invocation failed with {state}{suffix}"
                    )
                time.sleep(self.poll_seconds)
            else:
                raise TimeoutError(
                    f"BDA invocation exceeded {self.timeout_seconds} seconds"
                )
        finally:
            if self.cleanup_output:
                self._delete_prefix(job_uri)

        if result is None:
            raise RuntimeError("BDA completed without a normalized result")
        if self.cleanup_output:
            result.metadata["output_cleaned_up"] = True
        return result

    def normalize_response(
        self,
        response: dict[str, Any],
        profile: DocumentProfile,
    ) -> ProviderResult:
        """Normalize a captured synchronous BDA API response."""

        field_payloads, text_payloads, segment_statuses = self._sync_payloads(
            response
        )
        return self._normalize(
            field_payloads,
            profile,
            {
                "api": "InvokeDataAutomation",
                "segment_statuses": segment_statuses,
            },
            text_payloads=text_payloads,
        )

    def normalize_payload(
        self,
        payload: Any,
        profile: DocumentProfile,
    ) -> ProviderResult:
        """Normalize one decoded custom-output payload."""

        return self._normalize(
            [payload],
            profile,
            {"api": "captured-custom-output"},
            text_payloads=[],
        )

    def _request(self, source: DocumentSource) -> dict[str, Any]:
        request: dict[str, Any] = {
            "inputConfiguration": {"s3Uri": source.s3_uri},
            "dataAutomationProfileArn": self.profile_arn,
        }
        if self.project_arn:
            request["dataAutomationConfiguration"] = {
                "dataAutomationProjectArn": self.project_arn,
                "stage": self.stage,
            }
        if self.blueprints:
            request["blueprints"] = self.blueprints
        if not self.project_arn and not self.blueprints:
            raise ValueError("BDA provider requires a project ARN or blueprints")
        return request

    @staticmethod
    def _sync_payloads(
        response: dict[str, Any],
    ) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
        field_payloads: list[Any] = []
        text_payloads: list[Any] = []
        statuses: list[dict[str, Any]] = []
        for segment in response.get("outputSegments", []):
            if segment.get("customOutputStatus") is not None:
                statuses.append(
                    {"customOutputStatus": segment["customOutputStatus"]}
                )
            custom_output = segment.get("customOutput")
            if custom_output is not None and custom_output != "":
                field_payloads.append(BdaProvider._decode(custom_output))
            standard_output = segment.get("standardOutput")
            if standard_output is not None and standard_output != "":
                text_payloads.append(BdaProvider._decode(standard_output))
        return field_payloads, text_payloads, statuses

    def _s3_payloads(
        self,
        s3_uri: str,
        *,
        allowed_prefix: str | None = None,
    ) -> tuple[list[Any], list[Any]]:
        parsed = urlparse(s3_uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError(f"Invalid BDA output S3 URI: {s3_uri}")
        bucket = parsed.netloc
        key_or_prefix = parsed.path.lstrip("/")
        field_payloads: list[Any] = []
        text_payloads: list[Any] = []

        if key_or_prefix.lower().endswith("job_metadata.json"):
            manifest = self._read_json_object(bucket, key_or_prefix)
            custom_uris: list[str] = []
            standard_uris: list[str] = []
            self._collect_key_values(
                manifest,
                "custom_output_path",
                custom_uris,
            )
            self._collect_key_values(
                manifest,
                "standard_output_path",
                standard_uris,
            )
            for uri in custom_uris:
                field_payloads.append(
                    self._read_json_uri(uri, allowed_prefix or s3_uri)
                )
            for uri in standard_uris:
                text_payloads.append(
                    self._read_json_uri(uri, allowed_prefix or s3_uri)
                )
        else:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=bucket,
                Prefix=key_or_prefix,
            ):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if not key.lower().endswith(".json"):
                        continue
                    payload = self._read_json_object(bucket, key)
                    if self._contains_key(
                        payload,
                        {"inference_result", "inferenceResult"},
                    ):
                        field_payloads.append(payload)
                    else:
                        text_payloads.append(payload)
        if not field_payloads and not text_payloads:
            raise RuntimeError("BDA completed without JSON output")
        return field_payloads, text_payloads

    def _read_json_uri(self, s3_uri: str, allowed_prefix: str) -> Any:
        if not isinstance(s3_uri, str) or not self._is_within_prefix(
            s3_uri,
            allowed_prefix,
        ):
            raise RuntimeError(
                "BDA manifest referenced output outside the requested S3 prefix"
            )
        parsed = urlparse(s3_uri)
        return self._read_json_object(
            parsed.netloc,
            parsed.path.lstrip("/"),
        )

    def _read_json_object(self, bucket: str, key: str) -> Any:
        body = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read(
            self.max_output_object_bytes + 1
        )
        if len(body) > self.max_output_object_bytes:
            raise RuntimeError(f"BDA JSON output is too large: {key}")
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid BDA JSON output: {key}") from exc

    def _normalize(
        self,
        payloads: list[Any],
        profile: DocumentProfile,
        metadata: dict[str, Any],
        text_payloads: list[Any] | None = None,
    ) -> ProviderResult:
        fields: dict[str, FieldValue] = {}
        raw_text: list[str] = []
        normalization_warnings: list[str] = []
        for payload in payloads:
            self._walk(
                payload,
                profile,
                fields,
                raw_text,
                (),
                allow_fields=False,
            )
            field_payload = payload
            field_path: tuple[str, ...] = ()
            if isinstance(payload, dict):
                if "inference_result" in payload:
                    field_payload = payload["inference_result"]
                    field_path = ("inference_result",)
                elif "inferenceResult" in payload:
                    field_payload = payload["inferenceResult"]
                    field_path = ("inferenceResult",)
            self._walk(
                field_payload,
                profile,
                fields,
                raw_text,
                field_path,
                allow_fields=True,
            )
            self._merge_explainability(
                payload,
                profile,
                fields,
                normalization_warnings,
            )
        for payload in text_payloads or []:
            self._walk(payload, profile, fields, raw_text, (), allow_fields=False)
        warnings = list(dict.fromkeys(normalization_warnings))
        if not fields:
            warnings.append("No configured fields found in BDA output")
        blueprint_matches: list[Any] = []
        for payload in payloads:
            self._collect_key_values(payload, "matched_blueprint", blueprint_matches)
            self._collect_key_values(payload, "matchedBlueprint", blueprint_matches)
        metadata = {
            **metadata,
            "payload_count": len(payloads),
            "text_payload_count": len(text_payloads or []),
            "matched_blueprints": blueprint_matches,
        }
        return ProviderResult(
            provider=self.name,
            fields=fields,
            raw_text="\n\n".join(dict.fromkeys(raw_text)),
            warnings=warnings,
            metadata=metadata,
        )

    def _merge_explainability(
        self,
        payload: Any,
        profile: DocumentProfile,
        fields: dict[str, FieldValue],
        warnings: list[str],
    ) -> None:
        if not isinstance(payload, dict):
            return
        inference_key = "inference_result"
        inference = payload.get(inference_key)
        if inference is None:
            inference_key = "inferenceResult"
            inference = payload.get(inference_key)
        explanations = payload.get("explainability_info")
        if explanations is None:
            explanations = payload.get("explainabilityInfo", [])
        if isinstance(explanations, dict):
            explanations = [explanations]
        if not isinstance(explanations, list):
            return
        for explanation in explanations:
            if isinstance(explanation, dict):
                self._walk_explained(
                    inference,
                    explanation,
                    profile,
                    fields,
                    (inference_key,),
                    warnings,
                )

    def _walk_explained(
        self,
        inference: Any,
        explanation: Any,
        profile: DocumentProfile,
        fields: dict[str, FieldValue],
        path: tuple[str, ...],
        warnings: list[str],
    ) -> None:
        if isinstance(inference, list):
            explanation_items = explanation if isinstance(explanation, list) else []
            for index, item in enumerate(inference):
                item_explanation = (
                    explanation_items[index]
                    if index < len(explanation_items)
                    else {}
                )
                self._walk_explained(
                    item,
                    item_explanation,
                    profile,
                    fields,
                    (*path, str(index)),
                    warnings,
                )
            return
        if not isinstance(inference, dict):
            return

        explanation_map = explanation if isinstance(explanation, dict) else {}
        for raw_key, value in inference.items():
            field_explanation = explanation_map.get(raw_key, {})
            canonical = profile.canonical_name(raw_key)
            if (
                canonical is not None
                and not isinstance(value, (dict, list))
                and value not in {None, ""}
                and isinstance(field_explanation, dict)
            ):
                confidence = _confidence(field_explanation.get("confidence"))
                grounding = field_explanation.get("geometry")
                requires_review = field_explanation.get("success") is False
                if (
                    confidence is not None
                    or grounding is not None
                    or requires_review
                ):
                    candidate = FieldValue(
                        value=value,
                        source=self.name,
                        confidence=confidence,
                        raw_key=raw_key,
                        provenance={
                            "path": ".".join((*path, raw_key)),
                            "visual_grounding": grounding,
                            "requires_review": requires_review,
                        },
                    )
                    existing = fields.get(canonical)
                    if existing is None:
                        fields[canonical] = candidate
                    elif existing.value == candidate.value:
                        fields[canonical] = self._merge_candidates(
                            existing,
                            candidate,
                        )
                    else:
                        winner = self._merge_candidates(existing, candidate)
                        fields[canonical] = FieldValue(
                            value=winner.value,
                            source=winner.source,
                            confidence=winner.confidence,
                            raw_key=winner.raw_key,
                            provenance={
                                **winner.provenance,
                                "requires_review": True,
                                "conflicting_value": (
                                    candidate.value
                                    if winner.value == existing.value
                                    else existing.value
                                ),
                            },
                        )
                        warnings.append(
                            f"Conflicting BDA values for field {canonical}"
                        )
            self._walk_explained(
                value,
                field_explanation,
                profile,
                fields,
                (*path, raw_key),
                warnings,
            )

    @staticmethod
    def _merge_candidates(
        existing: FieldValue,
        candidate: FieldValue,
    ) -> FieldValue:
        existing_score = existing.confidence
        candidate_score = candidate.confidence
        winner, other = existing, candidate
        if candidate_score is not None and (
            existing_score is None or candidate_score > existing_score
        ):
            winner, other = candidate, existing

        provenance = {**other.provenance, **winner.provenance}
        if winner.provenance.get("visual_grounding") is None:
            provenance["visual_grounding"] = other.provenance.get(
                "visual_grounding"
            )
        provenance["requires_review"] = bool(
            existing.provenance.get("requires_review")
            or candidate.provenance.get("requires_review")
        )
        return FieldValue(
            value=winner.value,
            source=winner.source,
            confidence=winner.confidence,
            raw_key=winner.raw_key,
            provenance=provenance,
        )

    def _walk(
        self,
        node: Any,
        profile: DocumentProfile,
        fields: dict[str, FieldValue],
        raw_text: list[str],
        path: tuple[str, ...],
        allow_fields: bool,
    ) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                self._walk(
                    item,
                    profile,
                    fields,
                    raw_text,
                    (*path, str(index)),
                    allow_fields,
                )
            return
        if not isinstance(node, dict):
            return

        for raw_key, value in node.items():
            canonical = profile.canonical_name(raw_key) if allow_fields else None
            if canonical is not None:
                candidate = self._candidate(value, raw_key, path)
                if candidate is not None:
                    existing = fields.get(canonical)
                    if existing is None or self._is_better(candidate, existing):
                        fields[canonical] = candidate

            if raw_key in {"markdown", "representation", "documentText", "rawText"}:
                if isinstance(value, str) and value:
                    raw_text.append(value)
            self._walk(
                value,
                profile,
                fields,
                raw_text,
                (*path, raw_key),
                allow_fields,
            )

    def _delete_prefix(self, s3_uri: str) -> None:
        parsed = urlparse(s3_uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError(f"Invalid BDA cleanup S3 URI: {s3_uri}")
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")
        if not prefix:
            raise ValueError("Refusing to delete an empty BDA output prefix")
        versioning = self.s3.get_bucket_versioning(Bucket=bucket).get("Status")
        objects: list[dict[str, str]] = []
        if versioning in {"Enabled", "Suspended"}:
            paginator = self.s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for collection in ("Versions", "DeleteMarkers"):
                    objects.extend(
                        {
                            "Key": item["Key"],
                            "VersionId": item["VersionId"],
                        }
                        for item in page.get(collection, [])
                    )
        else:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                objects.extend(
                    {"Key": item["Key"]}
                    for item in page.get("Contents", [])
                )
        for start in range(0, len(objects), 1000):
            batch = objects[start : start + 1000]
            if batch:
                response = self.s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )
                if response.get("Errors"):
                    raise RuntimeError("BDA output cleanup was incomplete")

    @staticmethod
    def _is_within_prefix(output_uri: str, requested_uri: str) -> bool:
        output = urlparse(output_uri)
        requested = urlparse(requested_uri)
        requested_prefix = requested.path.lstrip("/")
        output_prefix = output.path.lstrip("/")
        return (
            output.scheme == requested.scheme == "s3"
            and output.netloc == requested.netloc
            and bool(requested_prefix)
            and output_prefix.startswith(requested_prefix)
        )

    @staticmethod
    def _contains_key(node: Any, keys: set[str]) -> bool:
        if isinstance(node, dict):
            return any(key in node for key in keys) or any(
                BdaProvider._contains_key(value, keys) for value in node.values()
            )
        if isinstance(node, list):
            return any(BdaProvider._contains_key(value, keys) for value in node)
        return False

    @staticmethod
    def _collect_key_values(node: Any, key: str, values: list[Any]) -> None:
        if isinstance(node, dict):
            if key in node:
                values.append(node[key])
            for value in node.values():
                BdaProvider._collect_key_values(value, key, values)
        elif isinstance(node, list):
            for value in node:
                BdaProvider._collect_key_values(value, key, values)

    def _candidate(
        self, value: Any, raw_key: str, path: tuple[str, ...]
    ) -> FieldValue | None:
        confidence = None
        actual_value = value
        grounding = None
        if isinstance(value, dict):
            actual_value = next(
                (
                    value[key]
                    for key in (
                        "value",
                        "text",
                        "valueString",
                        "inferenceResult",
                        "inference_result",
                    )
                    if key in value
                ),
                None,
            )
            confidence = _confidence(
                next(
                    (
                        value[key]
                        for key in ("confidence", "confidenceScore", "score")
                        if key in value
                    ),
                    None,
                )
            )
            grounding = value.get("geometry") or value.get("visualGrounding")
        if actual_value is None or actual_value == "" or isinstance(
            actual_value, (dict, list)
        ):
            return None
        return FieldValue(
            value=actual_value,
            source=self.name,
            confidence=confidence,
            raw_key=raw_key,
            provenance={
                "path": ".".join((*path, raw_key)),
                "visual_grounding": grounding,
            },
        )

    @staticmethod
    def _is_better(candidate: FieldValue, existing: FieldValue) -> bool:
        if candidate.confidence is None:
            return False
        return existing.confidence is None or candidate.confidence > existing.confidence

    @staticmethod
    def _decode(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"rawText": value}
