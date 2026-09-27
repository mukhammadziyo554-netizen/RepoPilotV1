from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import GITHUB_API_URL, REQUEST_TIMEOUT, github_token
from .errors import DiscoveryError
from .url_validation import RepositoryIdentifier


class GitHubClient:
    """Small, bounded client for trusted GitHub REST API paths only."""

    def __init__(self, session: requests.Session | None = None):
        if session is not None:
            self.session = session
            return
        self.session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.25,
            allowed_methods=frozenset({"GET"}),
            status_forcelist=(500, 502, 503, 504),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "RepoPilot-discovery",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token := github_token():
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(self, path: str, params: dict[str, str] | None = None, allow_not_found: bool = False) -> Any:
        try:
            response = self.session.get(
                f"{GITHUB_API_URL}{path}", headers=self._headers(), params=params, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as error:
            raise DiscoveryError("github_unavailable", "GitHub could not be reached. Please try again.", 502) from error

        if response.status_code == 404:
            if allow_not_found:
                return None
            raise DiscoveryError("repository_unavailable", "This repository was not found or is not publicly accessible.", 404)
        if response.status_code in {401, 403}:
            remaining = response.headers.get("X-RateLimit-Remaining")
            retry_at = response.headers.get("X-RateLimit-Reset")
            if remaining == "0":
                detail = " GitHub's limit will reset shortly." if retry_at else ""
                raise DiscoveryError("github_rate_limited", f"GitHub rate-limited this request.{detail}", 429)
            raise DiscoveryError("github_access_denied", "GitHub denied access to this repository request.", 403)
        if not response.ok:
            raise DiscoveryError("github_error", f"GitHub returned an unexpected error ({response.status_code}).", 502)
        try:
            return response.json()
        except ValueError as error:
            raise DiscoveryError("github_error", "GitHub returned an invalid response.", 502) from error

    @staticmethod
    def _base_path(repository: RepositoryIdentifier) -> str:
        return f"/repos/{quote(repository.owner, safe='')}/{quote(repository.name, safe='')}"

    def repository(self, identifier: RepositoryIdentifier) -> dict[str, Any]:
        payload = self._get(self._base_path(identifier))
        if not isinstance(payload, dict):
            raise DiscoveryError("github_error", "GitHub returned an unexpected repository response.", 502)
        return payload

    def languages(self, identifier: RepositoryIdentifier) -> dict[str, int]:
        payload = self._get(f"{self._base_path(identifier)}/languages")
        return payload if isinstance(payload, dict) else {}

    def latest_commit(self, identifier: RepositoryIdentifier, branch: str) -> dict[str, Any]:
        payload = self._get(f"{self._base_path(identifier)}/commits/{quote(branch, safe='')}")
        if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str):
            raise DiscoveryError("github_error", "GitHub did not return a valid default-branch commit.", 502)
        return payload

    def tree(self, identifier: RepositoryIdentifier, commit_sha: str) -> dict[str, Any]:
        payload = self._get(f"{self._base_path(identifier)}/git/trees/{quote(commit_sha, safe='')}", {"recursive": "1"})
        if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
            raise DiscoveryError("github_error", "GitHub did not return a valid repository tree.", 502)
        return payload

    def file_content(self, identifier: RepositoryIdentifier, path: str, commit_sha: str) -> dict[str, Any] | None:
        """Fetch one validated repository path at the selected immutable revision."""
        payload = self._get(
            f"{self._base_path(identifier)}/contents/{quote(path, safe='/')}",
            {"ref": commit_sha},
            allow_not_found=True,
        )
        return payload if isinstance(payload, dict) else None
