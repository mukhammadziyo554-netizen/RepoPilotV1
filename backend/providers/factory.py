import os

from ..errors import DiscoveryError
from .anthropic_provider import AnthropicProvider
from .base import AnalysisProvider


def get_analysis_provider() -> AnalysisProvider:
    """Select the configured provider without coupling analysis to a vendor SDK."""
    provider_name = os.getenv("ANALYSIS_PROVIDER", "anthropic").strip().lower()
    if provider_name == "anthropic":
        return AnthropicProvider()
    raise DiscoveryError("analysis_provider_unavailable", f"The configured analysis provider '{provider_name}' is not available.", 503)
