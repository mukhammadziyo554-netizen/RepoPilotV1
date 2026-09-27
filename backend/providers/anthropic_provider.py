import os
from typing import Generator

import anthropic

from ..errors import DiscoveryError
from .base import AnalysisRequest, AnalysisResponse


class AnthropicProvider:
    """Active provider implementation using Anthropic's Messages API."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = (api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")).strip()
        if not key:
            raise DiscoveryError("analysis_not_configured", "Anthropic is not configured. Add ANTHROPIC_API_KEY and restart RepoPilot.", 503)
        self.model = (model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")).strip()
        self.client = anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=1)

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=3_500,
                system=request.system_prompt,
                messages=[{"role": "user", "content": request.user_prompt}],
            )
        except anthropic.AuthenticationError as error:
            raise DiscoveryError("analysis_authentication_failed", "Anthropic rejected the configured API key.", 502) from error
        except anthropic.RateLimitError as error:
            raise DiscoveryError("analysis_rate_limited", "Anthropic rate-limited this analysis. Please try again shortly.", 429) from error
        except anthropic.APIConnectionError as error:
            raise DiscoveryError("analysis_unavailable", "Anthropic could not be reached. Please try again.", 502) from error
        except anthropic.APIError as error:
            raise DiscoveryError("analysis_failed", "Anthropic could not complete this analysis. Please retry.", 502) from error

        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()
        if not text:
            raise DiscoveryError("analysis_failed", "Anthropic returned an empty analysis. Please retry.", 502)
        return AnalysisResponse(text=text, provider=self.name, model=self.model)

    def stream_analyze(self, request: AnalysisRequest) -> Generator[str, None, None]:
        """Forward Anthropic's native text stream without synthetic typing effects."""
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=3_500,
                system=request.system_prompt,
                messages=[{"role": "user", "content": request.user_prompt}],
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        yield text
        except anthropic.AuthenticationError as error:
            raise DiscoveryError("analysis_authentication_failed", "Anthropic rejected the configured API key.", 502) from error
        except anthropic.RateLimitError as error:
            raise DiscoveryError("analysis_rate_limited", "Anthropic rate-limited this analysis. Please try again shortly.", 429) from error
        except anthropic.APIConnectionError as error:
            raise DiscoveryError("analysis_unavailable", "Anthropic could not be reached. Please try again.", 502) from error
        except anthropic.APIError as error:
            raise DiscoveryError("analysis_failed", "Anthropic could not complete this analysis. Please retry.", 502) from error
