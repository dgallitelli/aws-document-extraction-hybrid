"""JSON profile loading and validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when a document profile is invalid."""


_TEXTRACT_QUERY_PATTERN = re.compile(
    r"""^[a-zA-Z0-9\s!"\#\$%'&\(\)\*\+,\-\./:;=\?@\[\]\\\^_`\{\|\}~><]+$"""
)


def _strict_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a JSON boolean")
    return value


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


@dataclass(frozen=True)
class FieldDefinition:
    name: str
    description: str = ""
    required: bool = False
    aliases: tuple[str, ...] = ()
    query: str | None = None
    require_confidence: bool = False
    confidence_thresholds: dict[str, float] = field(default_factory=dict)
    value_type: str = "string"
    pattern: str | None = None
    allowed_values: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "FieldDefinition":
        if not isinstance(data, dict):
            raise ConfigError(f"Field {name!r} must be an object")
        aliases = data.get("aliases", [])
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) for alias in aliases
        ):
            raise ConfigError(f"Field {name!r} aliases must be a list of strings")
        query = data.get("query")
        if query is not None and not isinstance(query, str):
            raise ConfigError(f"Field {name!r} query must be a string")
        thresholds = data.get("confidence_thresholds", {})
        if not isinstance(thresholds, dict):
            raise ConfigError(
                f"Field {name!r} confidence_thresholds must be an object"
            )
        for provider_name, threshold in thresholds.items():
            if (
                not isinstance(provider_name, str)
                or isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not 0 <= threshold <= 1
            ):
                raise ConfigError(
                    f"Field {name!r} has an invalid confidence threshold"
                )
        value_type = data.get("value_type", "string")
        if value_type not in {"string", "number", "integer", "date", "boolean"}:
            raise ConfigError(f"Field {name!r} has an invalid value_type")
        pattern = data.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ConfigError(f"Field {name!r} pattern must be a string")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"Field {name!r} pattern is invalid") from exc
        allowed_values = data.get("allowed_values", [])
        if not isinstance(allowed_values, list) or not all(
            isinstance(value, str) for value in allowed_values
        ):
            raise ConfigError(
                f"Field {name!r} allowed_values must be a list of strings"
            )
        return cls(
            name=name,
            description=str(data.get("description", "")),
            required=_strict_bool(data, "required", False),
            aliases=tuple(aliases),
            query=query,
            require_confidence=_strict_bool(
                data, "require_confidence", False
            ),
            confidence_thresholds={
                provider_name: float(threshold)
                for provider_name, threshold in thresholds.items()
            },
            value_type=value_type,
            pattern=pattern,
            allowed_values=tuple(allowed_values),
        )

    @property
    def lookup_keys(self) -> set[str]:
        return {normalize_key(self.name), *(normalize_key(alias) for alias in self.aliases)}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, index: int, data: dict[str, Any]) -> "ProviderConfig":
        if not isinstance(data, dict):
            raise ConfigError(f"Provider #{index + 1} must be an object")
        provider_type = data.get("type")
        if not isinstance(provider_type, str) or not provider_type:
            raise ConfigError(f"Provider #{index + 1} requires a type")
        name = data.get("name", provider_type)
        if not isinstance(name, str) or not name:
            raise ConfigError(f"Provider #{index + 1} has an invalid name")
        return cls(
            name=name,
            type=provider_type,
            options={key: value for key, value in data.items() if key not in {"name", "type"}},
        )


@dataclass(frozen=True)
class DocumentProfile:
    name: str
    version: str
    fields: tuple[FieldDefinition, ...]
    providers: tuple[ProviderConfig, ...]
    fallback_on_low_confidence: bool = True
    deadline_seconds: float | None = None
    allowed_regions: tuple[str, ...] = ()
    allowed_source_buckets: tuple[str, ...] = ()
    allowed_source_prefixes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentProfile":
        if not isinstance(data, dict):
            raise ConfigError("Profile must be a JSON object")
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError("Profile requires a name")

        raw_fields = data.get("fields")
        if not isinstance(raw_fields, dict) or not raw_fields:
            raise ConfigError("Profile requires at least one field")
        fields = tuple(
            FieldDefinition.from_dict(field_name, field_data)
            for field_name, field_data in raw_fields.items()
        )
        normalized_names = [normalize_key(item.name) for item in fields]
        if len(normalized_names) != len(set(normalized_names)):
            raise ConfigError("Field names must be unique after normalization")

        raw_providers = data.get("providers")
        if not isinstance(raw_providers, list) or not raw_providers:
            raise ConfigError("Profile requires at least one provider")
        providers = tuple(
            ProviderConfig.from_dict(index, provider)
            for index, provider in enumerate(raw_providers)
        )
        provider_names = [provider.name for provider in providers]
        if len(provider_names) != len(set(provider_names)):
            raise ConfigError("Provider names must be unique")
        if any(
            provider.type == "textract"
            and provider.options.get("mode") == "queries"
            for provider in providers
        ):
            cls._validate_textract_queries(fields)
        for field_definition in fields:
            unknown_providers = set(
                field_definition.confidence_thresholds
            ) - set(provider_names)
            if unknown_providers:
                raise ConfigError(
                    f"Field {field_definition.name!r} references unknown "
                    f"confidence providers: {sorted(unknown_providers)}"
                )

        version = data.get("version")
        if not isinstance(version, str) or not version:
            raise ConfigError("Profile requires an explicit version")
        deadline_seconds = data.get("deadline_seconds")
        if deadline_seconds is not None and (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or deadline_seconds <= 0
        ):
            raise ConfigError("deadline_seconds must be a positive number")

        profile = cls(
            name=name,
            version=version,
            fields=fields,
            providers=providers,
            fallback_on_low_confidence=_strict_bool(
                data, "fallback_on_low_confidence", True
            ),
            deadline_seconds=(
                float(deadline_seconds) if deadline_seconds is not None else None
            ),
            allowed_regions=cls._string_list(data, "allowed_regions"),
            allowed_source_buckets=cls._string_list(
                data, "allowed_source_buckets"
            ),
            allowed_source_prefixes=cls._string_list(
                data, "allowed_source_prefixes"
            ),
        )
        profile._validate_aliases()
        return profile

    @staticmethod
    def _string_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
        values = data.get(key, [])
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            raise ConfigError(f"{key} must be a list of non-empty strings")
        return tuple(values)

    @staticmethod
    def _validate_textract_queries(
        fields: tuple[FieldDefinition, ...],
    ) -> None:
        if len(fields) > 15:
            raise ConfigError(
                "Synchronous Textract Queries supports at most 15 fields per page"
            )
        for field_definition in fields:
            query = (
                field_definition.query
                or field_definition.description
                or f"What is {field_definition.name}?"
            )
            for label, value in (
                ("alias", field_definition.name),
                ("query", query),
            ):
                if (
                    not 1 <= len(value) <= 200
                    or _TEXTRACT_QUERY_PATTERN.fullmatch(value) is None
                ):
                    raise ConfigError(
                        f"Textract {label} for field "
                        f"{field_definition.name!r} violates Query constraints"
                    )

    def _validate_aliases(self) -> None:
        owner_by_key: dict[str, str] = {}
        for item in self.fields:
            for key in item.lookup_keys:
                previous = owner_by_key.get(key)
                if previous and previous != item.name:
                    raise ConfigError(
                        f"Alias {key!r} is shared by {previous!r} and {item.name!r}"
                    )
                owner_by_key[key] = item.name

    @property
    def field_by_name(self) -> dict[str, FieldDefinition]:
        return {field.name: field for field in self.fields}

    def canonical_name(self, raw_key: str) -> str | None:
        normalized = normalize_key(raw_key)
        for item in self.fields:
            if normalized in item.lookup_keys:
                return item.name
        return None


def load_profile(path: str | Path) -> DocumentProfile:
    profile_path = Path(path)
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Profile not found: {profile_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Profile is not valid JSON: {exc}") from exc
    return DocumentProfile.from_dict(data)
