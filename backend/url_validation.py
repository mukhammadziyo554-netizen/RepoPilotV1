from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .errors import DiscoveryError


@dataclass(frozen=True)
class RepositoryIdentifier:
    owner: str
    name: str

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


def parse_repository_url(value: object) -> RepositoryIdentifier:
    """Accept only a normal public GitHub repository URL and normalize it."""
    if not isinstance(value, str) or not value.strip():
        raise DiscoveryError("invalid_url", "Enter a public GitHub repository URL.")

    parsed = urlsplit(value.strip())
    if parsed.scheme != "https":
        raise DiscoveryError("invalid_url", "Use an HTTPS GitHub repository URL.")
    if parsed.hostname not in {"github.com", "www.github.com"} or parsed.username or parsed.password:
        raise DiscoveryError("invalid_url", "Use a github.com repository URL.")
    try:
        if parsed.port is not None:
            raise DiscoveryError("invalid_url", "Use a standard github.com repository URL without a port.")
    except ValueError as error:
        raise DiscoveryError("invalid_url", "The repository URL has an invalid port.") from error
    if parsed.query or parsed.fragment:
        raise DiscoveryError("invalid_url", "Use a repository URL without query parameters or fragments.")

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise DiscoveryError("invalid_url", "Use a repository URL such as https://github.com/owner/repository.")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        raise DiscoveryError("invalid_url", "The GitHub owner or repository name is missing.")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    if any(character not in allowed for character in owner) or any(character not in allowed for character in name):
        raise DiscoveryError("invalid_url", "The GitHub owner or repository name is invalid.")
    return RepositoryIdentifier(owner=owner, name=name)
