from dataclasses import dataclass


@dataclass
class DiscoveryError(Exception):
    """A safe error that can be returned to a RepoPilot client."""

    code: str
    message: str
    status_code: int = 400

