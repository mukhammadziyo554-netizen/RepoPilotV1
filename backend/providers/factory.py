import os
import subprocess

from ..errors import DiscoveryError
from .anthropic_provider import AnthropicProvider
from .bob_provider import BobProvider
from .bob_http_provider import BobHttpProvider
from .base import AnalysisProvider


def get_analysis_provider() -> AnalysisProvider:
    """Select the configured provider without coupling analysis to a vendor SDK."""
    provider_name = os.getenv("ANALYSIS_PROVIDER", "anthropic").strip().lower()

    if provider_name == "anthropic":
        return AnthropicProvider()

    elif provider_name == "bob":
        # For Bob: intelligently choose between CLI (local) and HTTP (Vercel) implementations
        # Priority: HTTP endpoint if configured > CLI if available > Error with guidance

        endpoint_url = os.getenv("BOB_INFERENCE_ENDPOINT", "").strip()
        if endpoint_url:
            # Use HTTP provider for Vercel/cloud deployment
            return BobHttpProvider()

        # Try CLI provider if endpoint not configured (local development)
        try:
            # Quick check if bob CLI is available
            subprocess.run(["bob", "--version"], capture_output=True, timeout=5, check=False)
            return BobProvider()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            # Bob CLI not available, provide helpful error message
            raise DiscoveryError(
                "analysis_not_configured",
                "IBM Bob is not available. For local development: install Bob Shell 2.0.5. "
                "For Vercel: set BOB_INFERENCE_ENDPOINT with your instance-specific endpoint from bob.ibm.com.",
                503
            )

    raise DiscoveryError("analysis_provider_unavailable", f"The configured analysis provider '{provider_name}' is not available.", 503)
