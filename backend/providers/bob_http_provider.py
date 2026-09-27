import json
import os
from typing import Generator

import requests

from ..errors import DiscoveryError
from .base import AnalysisRequest, AnalysisResponse


class BobHttpProvider:
    """IBM Bob provider using HTTP inference API (Vercel-compatible)."""

    name = "bob"

    def __init__(self, api_key: str | None = None, endpoint_url: str | None = None, team_id: str | None = None):
        self.api_key = (api_key if api_key is not None else os.getenv("BOB_API_KEY", "")).strip()
        if not self.api_key:
            raise DiscoveryError("analysis_not_configured", "IBM Bob is not configured. Add BOB_API_KEY to environment.", 503)

        # Endpoint URL must be provided or in environment
        self.endpoint_url = (endpoint_url or os.getenv("BOB_INFERENCE_ENDPOINT", "")).strip()
        if not self.endpoint_url:
            raise DiscoveryError(
                "analysis_not_configured",
                "IBM Bob inference endpoint URL is not configured. "
                "Set BOB_INFERENCE_ENDPOINT environment variable with your instance-specific endpoint from bob.ibm.com.",
                503
            )

        # Team ID is optional (only required for General-type keys)
        self.team_id = (team_id or os.getenv("BOB_TEAM_ID", "")).strip()
        self.model = "bob-2.0.5"

    def _build_headers(self) -> dict[str, str]:
        """Build request headers with authentication."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # Add team ID header if configured (for General-type API keys)
        if self.team_id:
            headers["X-Team-ID"] = self.team_id

        return headers

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        """Invoke Bob HTTP API for non-streaming analysis."""
        try:
            headers = self._build_headers()

            payload = {
                "prompt": request.user_prompt,
                "model": self.model,
            }

            response = requests.post(
                self.endpoint_url,
                json=payload,
                headers=headers,
                timeout=120,
            )

            if response.status_code == 401 or response.status_code == 403:
                raise DiscoveryError("analysis_authentication_failed", "IBM Bob rejected the configured API key or endpoint.", 502)
            elif response.status_code >= 400:
                error_msg = response.text[:200] if response.text else f"HTTP {response.status_code}"
                raise DiscoveryError("analysis_failed", f"IBM Bob inference failed: {error_msg}", 502)

            # Parse response
            try:
                data = response.json()
            except json.JSONDecodeError as error:
                raise DiscoveryError("analysis_failed", "IBM Bob returned invalid JSON.", 502) from error

            # Extract response text (format may vary)
            text = data.get("text") or data.get("response") or data.get("result") or str(data)
            if isinstance(text, dict):
                text = json.dumps(text, indent=2)

            text = str(text).strip()
            if not text:
                raise DiscoveryError("analysis_failed", "IBM Bob returned an empty response.", 502)

            return AnalysisResponse(text=text, provider=self.name, model=self.model)

        except DiscoveryError:
            raise
        except requests.RequestException as error:
            if isinstance(error, requests.Timeout):
                raise DiscoveryError("analysis_unavailable", "IBM Bob request timed out. Please try again.", 502) from error
            raise DiscoveryError("analysis_failed", f"IBM Bob HTTP request failed: {str(error)[:100]}", 502) from error
        except Exception as error:
            raise DiscoveryError("analysis_failed", f"IBM Bob analysis failed: {str(error)[:100]}", 502) from error

    def stream_analyze(self, request: AnalysisRequest) -> Generator[str, None, None]:
        """Stream analysis from Bob HTTP API (if supported)."""
        try:
            headers = self._build_headers()
            headers["Accept"] = "text/event-stream"

            payload = {
                "prompt": request.user_prompt,
                "model": self.model,
                "stream": True,
            }

            response = requests.post(
                self.endpoint_url,
                json=payload,
                headers=headers,
                timeout=120,
                stream=True,
            )

            if response.status_code == 401 or response.status_code == 403:
                raise DiscoveryError("analysis_authentication_failed", "IBM Bob rejected the configured API key or endpoint.", 502)
            elif response.status_code >= 400:
                raise DiscoveryError("analysis_failed", f"IBM Bob inference failed: HTTP {response.status_code}", 502)

            # Parse streaming response
            buffer = ""
            for line in response.iter_lines():
                if not line:
                    continue

                # Handle both SSE format and plain text streaming
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")

                line = line.strip()

                # SSE format: data: ...
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        # Extract text from various possible response formats
                        if isinstance(data, dict):
                            text = data.get("text") or data.get("content") or data.get("response")
                            if text:
                                yield text
                        else:
                            yield str(data)
                    except json.JSONDecodeError:
                        yield line[6:]  # Fallback: yield the content after "data: "
                else:
                    # Plain text streaming
                    if line and not line.startswith(":"):
                        yield line

        except DiscoveryError:
            raise
        except requests.RequestException as error:
            if isinstance(error, requests.Timeout):
                raise DiscoveryError("analysis_unavailable", "IBM Bob streaming timed out. Please try again.", 502) from error
            raise DiscoveryError("analysis_failed", f"IBM Bob streaming failed: {str(error)[:100]}", 502) from error
        except Exception as error:
            raise DiscoveryError("analysis_failed", f"IBM Bob streaming failed: {str(error)[:100]}", 502) from error
