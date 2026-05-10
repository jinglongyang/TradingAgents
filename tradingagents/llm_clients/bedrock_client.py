"""AWS Bedrock client (Anthropic Claude models via Bedrock Converse API).

Uses ``langchain_aws.ChatBedrockConverse`` which supports tool use and
``with_structured_output`` (the latter via tool-calling under the hood),
making it a drop-in alternative to the direct Anthropic API for this
project's Portfolio Manager / Research Manager / Trader structured outputs.

Auth comes from the standard AWS chain (env vars, ``~/.aws/credentials``,
SSO cache, IAM role). Set ``AWS_PROFILE`` or pass ``aws_profile`` to pick
a non-default profile.

Model IDs in Bedrock differ from the direct Anthropic API:
  Direct API:        claude-sonnet-4-6
  Bedrock (us):      us.anthropic.claude-sonnet-4-6-20251015-v1:0
  Bedrock (regional): anthropic.claude-sonnet-4-6-20251015-v1:0

Cross-region inference profiles (``us.``, ``eu.`` prefix) are recommended
for production — they auto-route to the nearest available region.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from langchain_aws import ChatBedrockConverse

from .base_client import BaseLLMClient, normalize_content


_PASSTHROUGH_KWARGS = (
    "max_tokens", "temperature", "top_p", "stop_sequences",
    "callbacks", "max_retries", "additional_model_request_fields",
    "performance_config",
)


class NormalizedChatBedrockConverse(ChatBedrockConverse):
    """ChatBedrockConverse that flattens block-list content to strings.

    Claude responses through Bedrock — particularly with reasoning enabled —
    return content as a list of typed blocks. The rest of the framework
    expects a plain string, so we normalize on every invoke.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class BedrockClient(BaseLLMClient):
    """Client for AWS Bedrock-hosted models (Anthropic Claude focus)."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        # base_url is unused for Bedrock; AWS endpoint is region-derived.
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        self.warn_if_unknown_model()

        llm_kwargs: dict[str, Any] = {"model": self.model}

        # Region: kwarg > AWS_REGION env > AWS_DEFAULT_REGION env > us-east-1
        region = (
            self.kwargs.get("region_name")
            or self.kwargs.get("region")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        llm_kwargs["region_name"] = region

        # Profile: explicit kwarg > AWS_PROFILE env. Skip if neither set so
        # the default credential chain still picks up SSO / instance role.
        profile = self.kwargs.get("aws_profile") or os.environ.get("AWS_PROFILE")
        if profile:
            llm_kwargs["credentials_profile_name"] = profile

        # Pass through standard tuning knobs.
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Bedrock requires max_tokens to be set explicitly for many models;
        # default to a generous value if caller didn't specify.
        llm_kwargs.setdefault("max_tokens", 8192)

        return NormalizedChatBedrockConverse(**llm_kwargs)

    def validate_model(self) -> bool:
        """Bedrock model IDs vary by region and inference profile, so we
        accept anything that looks like a Bedrock model ID and let AWS
        reject malformed requests at runtime.
        """
        m = self.model.lower()
        return "anthropic" in m or "amazon" in m or "meta" in m or "ai21" in m or "cohere" in m
