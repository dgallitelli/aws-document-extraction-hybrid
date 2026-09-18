"""AWS provider factory."""

from __future__ import annotations

import os
import re
from typing import Any

import boto3
from botocore.config import Config

from ..config import ProviderConfig
from .bda import BdaProvider
from .bedrock import BedrockProvider
from .textract import TextractProvider

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            variable = match.group(1)
            resolved = os.environ.get(variable)
            if resolved is None:
                raise ValueError(
                    f"Required environment variable is not set: {variable}"
                )
            return resolved

        return _ENV_REFERENCE.sub(replace, value)
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item) for key, item in value.items()}
    return value


class AwsProviderFactory:
    """Build providers lazily so unused fallbacks need no configuration."""

    def __init__(
        self,
        session: boto3.Session | None = None,
        region: str | None = None,
    ) -> None:
        self.session = session or boto3.Session()
        self.region = region or self.session.region_name
        if not self.region:
            raise ValueError("AWS Region must be supplied or configured")

    def __call__(self, config: ProviderConfig):
        options = _resolve(config.options)
        client_config = Config(
            connect_timeout=float(options.get("connect_timeout_seconds", 5)),
            read_timeout=float(options.get("read_timeout_seconds", 60)),
            retries={
                "mode": "standard",
                "total_max_attempts": int(options.get("max_attempts", 3)),
            },
        )
        if config.type == "textract":
            return TextractProvider(
                name=config.name,
                client=self.session.client(
                    "textract", region_name=self.region, config=client_config
                ),
                options=options,
            )
        if config.type == "bedrock":
            return BedrockProvider(
                name=config.name,
                bedrock_client=self.session.client(
                    "bedrock-runtime",
                    region_name=self.region,
                    config=client_config,
                ),
                s3_client=self.session.client(
                    "s3", region_name=self.region, config=client_config
                ),
                options=options,
            )
        if config.type == "bda":
            return BdaProvider(
                name=config.name,
                client=self.session.client(
                    "bedrock-data-automation-runtime",
                    region_name=self.region,
                    config=client_config,
                ),
                s3_client=self.session.client(
                    "s3", region_name=self.region, config=client_config
                ),
                options=options,
            )
        raise ValueError(f"Unsupported provider type: {config.type}")


__all__ = ["AwsProviderFactory"]
