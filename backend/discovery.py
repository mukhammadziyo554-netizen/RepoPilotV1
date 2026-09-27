from collections import Counter
from typing import Any

from .config import MAX_TREE_ENTRIES
from .errors import DiscoveryError
from .github_client import GitHubClient
from .url_validation import parse_repository_url


CHARACTERISTIC_NAMES = {
    "package.json": "Node.js package manifest",
    "requirements.txt": "Python requirements",
    "pyproject.toml": "Python project configuration",
    "pipfile": "Python Pipenv manifest",
    "poetry.lock": "Python Poetry lockfile",
    "cargo.toml": "Rust package manifest",
    "go.mod": "Go module definition",
    "dockerfile": "Docker build configuration",
    "docker-compose.yml": "Docker Compose configuration",
    "docker-compose.yaml": "Docker Compose configuration",
    "makefile": "Make build configuration",
    "readme.md": "Repository documentation",
}


def calculate_language_statistics(languages: dict[str, int] | None) -> dict[str, Any]:
    """Format GitHub language-byte data for display without asking an AI to estimate it."""
    valid = {name: byte_count for name, byte_count in (languages or {}).items() if isinstance(name, str) and isinstance(byte_count, int) and byte_count > 0}
    total_bytes = sum(valid.values())
    if not total_bytes:
        return {"total_bytes": 0, "languages": []}
    return {
        "total_bytes": total_bytes,
        "languages": [
            {"name": name, "bytes": byte_count, "percentage": round(byte_count / total_bytes * 100, 1)}
            for name, byte_count in sorted(valid.items(), key=lambda item: (-item[1], item[0].lower()))
        ],
    }


def _entry_type(entry: dict[str, Any]) -> str:
    if entry.get("type") == "tree":
        return "directory"
    if entry.get("mode") == "120000":
        return "symlink"
    if entry.get("type") == "commit":
        return "submodule"
    return "file"


def parse_tree(entries: list[dict[str, Any]], truncated: bool) -> tuple[dict[str, Any], dict[str, int], dict[str, Any]]:
    """Convert GitHub's flat recursive tree into a safe nested directory representation."""
    root: dict[str, Any] = {"name": "", "path": "", "type": "directory", "children": []}
    directories: dict[str, dict[str, Any]] = {"": root}
    counts: Counter[str] = Counter()
    total_bytes = 0
    characteristics: list[dict[str, str]] = []
    test_directories: list[str] = []

    safe_entries = [entry for entry in entries[:MAX_TREE_ENTRIES] if isinstance(entry, dict) and isinstance(entry.get("path"), str)]
    for entry in safe_entries:
        path = entry["path"].strip("/")
        parts = [part for part in path.split("/") if part and part not in {".", ".."}]
        if not parts:
            continue
        parent_path = ""
        for index, part in enumerate(parts[:-1]):
            current_path = "/".join(parts[: index + 1])
            if current_path not in directories:
                node = {"name": part, "path": current_path, "type": "directory", "children": []}
                directories[parent_path]["children"].append(node)
                directories[current_path] = node
            parent_path = current_path

        node_type = _entry_type(entry)
        full_path = "/".join(parts)
        node: dict[str, Any]
        if node_type == "directory" and full_path in directories:
            node = directories[full_path]
        else:
            node = {"name": parts[-1], "path": full_path, "type": node_type}
            if node_type == "directory":
                node["children"] = []
            directories[parent_path]["children"].append(node)
        if isinstance(entry.get("size"), int):
            node["size"] = entry["size"]
            total_bytes += entry["size"]
        if isinstance(entry.get("sha"), str):
            node["sha"] = entry["sha"]
        counts[node_type] += 1
        if node_type == "directory":
            directories[node["path"]] = node

        lowered_name = node["name"].lower()
        if node_type == "file" and lowered_name in CHARACTERISTIC_NAMES:
            characteristics.append({"path": node["path"], "kind": CHARACTERISTIC_NAMES[lowered_name]})
        if node_type == "directory" and lowered_name in {"test", "tests", "__tests__", "spec", "specs"}:
            test_directories.append(node["path"])

    def sort_children(node: dict[str, Any]) -> None:
        if "children" not in node:
            return
        node["children"].sort(key=lambda child: (child["type"] != "directory", child["name"].lower()))
        for child in node["children"]:
            sort_children(child)

    sort_children(root)
    coverage = {
        "complete": not truncated and len(entries) <= MAX_TREE_ENTRIES,
        "truncated_by_github": truncated,
        "entry_limit_reached": len(entries) > MAX_TREE_ENTRIES,
        "message": "The full recursive tree was returned." if not truncated and len(entries) <= MAX_TREE_ENTRIES
        else "The directory inventory is partial; GitHub truncated the recursive tree or RepoPilot reached its safety limit.",
    }
    statistics = {
        "files": counts["file"],
        "directories": counts["directory"],
        "symlinks": counts["symlink"],
        "submodules": counts["submodule"],
        "bytes_reported_by_github": total_bytes,
    }
    return root, statistics, {"recognized_files": characteristics, "test_directories": test_directories, "coverage": coverage}


def load_repository_revision(url: object, client: GitHubClient) -> tuple[Any, dict[str, Any], dict[str, int], dict[str, Any], dict[str, Any]]:
    """Load metadata and one immutable default-branch revision for discovery or retrieval."""
    identifier = parse_repository_url(url)
    metadata = client.repository(identifier)
    if metadata.get("private"):
        raise DiscoveryError("repository_unavailable", "This repository is private and cannot be discovered with the current GitHub access.", 403)
    branch = metadata.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise DiscoveryError("repository_unavailable", "This repository has no default branch to discover.", 422)

    languages = client.languages(identifier)
    commit = client.latest_commit(identifier, branch)
    tree_payload = client.tree(identifier, commit["sha"])
    return identifier, metadata, languages, commit, tree_payload


def discover_repository(url: object, client: GitHubClient) -> dict[str, Any]:
    identifier, metadata, languages, commit, tree_payload = load_repository_revision(url, client)
    return build_discovery_response(identifier, metadata, languages, commit, tree_payload)


def build_discovery_response(
    identifier: Any, metadata: dict[str, Any], languages: dict[str, int], commit: dict[str, Any], tree_payload: dict[str, Any]
) -> dict[str, Any]:
    """Format one fetched repository revision for the stable discovery API contract."""
    tree, statistics, characteristics = parse_tree(tree_payload["tree"], bool(tree_payload.get("truncated")))

    owner = metadata.get("owner") if isinstance(metadata.get("owner"), dict) else {}
    license_data = metadata.get("license") if isinstance(metadata.get("license"), dict) else {}
    return {
        "repository": {
            "name": metadata.get("name") or identifier.name,
            "full_name": metadata.get("full_name") or f"{identifier.owner}/{identifier.name}",
            "owner": {"login": owner.get("login") or identifier.owner, "url": owner.get("html_url")},
            "description": metadata.get("description"),
            "url": metadata.get("html_url") or identifier.canonical_url,
            "primary_language": metadata.get("language"),
            "languages": languages,
            "default_branch": metadata.get("default_branch"),
            "visibility": metadata.get("visibility") or ("private" if metadata.get("private") else "public"),
            "archived": bool(metadata.get("archived")),
            "stars": metadata.get("stargazers_count", 0),
            "forks": metadata.get("forks_count", 0),
            "updated_at": metadata.get("updated_at"),
            "license": license_data.get("spdx_id") or license_data.get("name"),
            "latest_commit": {"sha": commit["sha"], "url": commit.get("html_url")},
        },
        "tree": tree,
        "file_statistics": statistics,
        "language_statistics": calculate_language_statistics(languages),
        "characteristics": {key: value for key, value in characteristics.items() if key != "coverage"},
        "discovery_coverage": characteristics["coverage"],
    }
