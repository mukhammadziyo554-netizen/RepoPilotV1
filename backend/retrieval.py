"""Deterministic, bounded retrieval of useful text files from a GitHub tree."""

import base64
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Generator

from .config import MAX_FILE_BYTES, MAX_RETRIEVED_FILES, MAX_TOTAL_SOURCE_BYTES
from .discovery import build_discovery_response, load_repository_revision
from .github_client import GitHubClient
from .url_validation import parse_repository_url

SOURCE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".kt", ".kts",
    ".go", ".rs", ".rb", ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".scala",
    ".dart", ".ex", ".exs", ".erl", ".hrl", ".lua", ".r", ".sh", ".bash", ".zsh", ".ps1",
    ".sql", ".html", ".css", ".scss", ".sass", ".vue", ".svelte",
}
TEXT_CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".xml", ".gradle", ".properties"}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".zip", ".gz", ".tar", ".7z",
    ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2", ".ttf", ".eot", ".dll", ".so", ".dylib", ".exe",
    ".class", ".jar", ".lock", ".map",
}
SKIP_DIRECTORIES = {
    ".git", ".github", "node_modules", "vendor", "dist", "build", "coverage", ".next", ".cache", "__pycache__",
    ".venv", "venv", "target", "out", "bin", "obj", ".idea", ".vscode",
}
MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "requirements.txt", "pipfile", "cargo.toml", "go.mod", "composer.json",
    "gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "makefile", "dockerfile", "docker-compose.yml",
    "docker-compose.yaml",
}
ENTRYPOINT_NAMES = {
    "main.py", "app.py", "wsgi.py", "asgi.py", "manage.py", "index.js", "index.ts", "server.js", "server.ts",
    "main.go", "main.rs", "program.cs", "application.java", "__init__.py",
}
INSTRUCTION_TOPICS = {
    "authentication": {"auth", "authentication", "login", "logout", "session", "jwt", "oauth", "sso", "identity"},
    "api": {"api", "route", "routes", "router", "controller", "handler", "endpoint", "http"},
    "database": {"database", "db", "model", "models", "schema", "migration", "migrations", "orm", "sql"},
}


def detect_language(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    names = {
        ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
        ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
        ".php": "PHP", ".cs": "C#", ".c": "C", ".h": "C/C++", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
        ".swift": "Swift", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".dart": "Dart", ".vue": "Vue",
        ".svelte": "Svelte", ".sql": "SQL", ".sh": "Shell", ".toml": "TOML", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    }
    return names.get(suffix)


def _instruction_terms(instruction: str) -> set[str]:
    words = {word for word in re.findall(r"[a-z][a-z0-9_-]{2,}", instruction.lower())}
    for topic_words in INSTRUCTION_TOPICS.values():
        if words & topic_words:
            words |= topic_words
    return words


def _skip_reason(path: str, size: object) -> str | None:
    file_path = PurePosixPath(path.lower())
    name = file_path.name
    if any(part in SKIP_DIRECTORIES for part in file_path.parts):
        return "excluded_directory"
    if name == ".env" or name.startswith(".env.") or file_path.suffix in {".pem", ".key", ".p12", ".pfx"} or any(token in name for token in ("secret", "credential", "private", "id_rsa", "token", "password")):
        return "sensitive_file"
    if file_path.suffix in BINARY_SUFFIXES or name.endswith(".min.js") or name.endswith(".min.css"):
        return "binary_or_generated"
    if isinstance(size, int) and size > MAX_FILE_BYTES:
        return "file_too_large"
    if file_path.suffix not in SOURCE_SUFFIXES | TEXT_CONFIG_SUFFIXES and name not in MANIFEST_NAMES | ENTRYPOINT_NAMES | {"readme.md", "readme"}:
        return "unsupported_file_type"
    return None


def select_files(entries: list[dict[str, Any]], instruction: str = "") -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Score tree blobs using explicit, explainable path and filename rules."""
    terms = _instruction_terms(instruction)
    skipped: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "blob" or entry.get("mode") == "120000":
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        reason = _skip_reason(path, entry.get("size"))
        if reason:
            skipped[reason] += 1
            continue
        name = PurePosixPath(path).name.lower()
        lowered_path = path.lower()
        score = 50
        reasons: list[str] = []
        if name in MANIFEST_NAMES:
            score -= 35
            reasons.append("dependency or build manifest")
        if name in ENTRYPOINT_NAMES:
            score -= 25
            reasons.append("common application entry point")
        if name in {"readme.md", "readme"}:
            score -= 15
            reasons.append("repository documentation")
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in SOURCE_SUFFIXES:
            score -= 12
            reasons.append(f"source file ({suffix})")
        elif suffix in TEXT_CONFIG_SUFFIXES:
            score -= 8
            reasons.append(f"text configuration ({suffix})")
        matched = sorted(term for term in terms if term in lowered_path)
        if matched:
            score -= min(24, len(matched) * 8)
            reasons.append(f"matches focus terms: {', '.join(matched[:3])}")
        candidates.append({"path": path, "size": entry.get("size"), "score": score, "reasons": reasons or ["supported text file"]})

    candidates.sort(key=lambda item: (item["score"], len(PurePosixPath(item["path"]).parts), item["path"].lower()))
    if len(candidates) > MAX_RETRIEVED_FILES:
        skipped["retrieval_file_limit"] += len(candidates) - MAX_RETRIEVED_FILES
    return candidates[:MAX_RETRIEVED_FILES], dict(skipped)


def _decode_file(payload: dict[str, Any]) -> bytes | None:
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        return None
    try:
        return base64.b64decode(payload["content"], validate=False)
    except (TypeError, ValueError):
        return None


def stream_retrieve_repository_sources(
    url: object, instruction: object, client: GitHubClient, mode: object = None
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    """Yield real retrieval milestones, then return the complete retrieval result."""
    focus = str(instruction or "").strip()[:500]
    analysis_mode = str(mode or "").strip().lower()
    if analysis_mode not in {"overview", "architecture", "custom"}:
        analysis_mode = None
    yield {"event": "status", "data": {"stage": "github", "message": "Connecting to GitHub…"}}
    identifier, metadata, languages, commit, tree_payload = load_repository_revision(url, client)
    yield {"event": "status", "data": {"stage": "structure", "message": "Retrieved repository metadata and directory structure."}}
    discovery = build_discovery_response(identifier, metadata, languages, commit, tree_payload)
    yield {"event": "repository", "data": {key: discovery[key] for key in ("repository", "tree", "file_statistics", "language_statistics", "characteristics", "discovery_coverage")}}
    yield {"event": "status", "data": {"stage": "selection", "message": "Selecting relevant source files…"}}
    selected, skipped = select_files(tree_payload["tree"], focus)
    retrieved: list[dict[str, Any]] = []
    total_bytes = 0
    for index, candidate in enumerate(selected, start=1):
        expected_size = candidate.get("size")
        if isinstance(expected_size, int) and total_bytes + expected_size > MAX_TOTAL_SOURCE_BYTES:
            skipped["total_content_limit"] = skipped.get("total_content_limit", 0) + 1
            continue
        payload = client.file_content(identifier, candidate["path"], commit["sha"])
        if payload is None:
            skipped["file_unavailable"] = skipped.get("file_unavailable", 0) + 1
            continue
        raw = _decode_file(payload)
        if raw is None or b"\x00" in raw:
            skipped["binary_or_unreadable"] = skipped.get("binary_or_unreadable", 0) + 1
            continue
        if len(raw) > MAX_FILE_BYTES:
            skipped["file_too_large"] = skipped.get("file_too_large", 0) + 1
            continue
        if total_bytes + len(raw) > MAX_TOTAL_SOURCE_BYTES:
            skipped["total_content_limit"] = skipped.get("total_content_limit", 0) + 1
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped["binary_or_unreadable"] = skipped.get("binary_or_unreadable", 0) + 1
            continue
        total_bytes += len(raw)
        retrieved.append({
            "path": candidate["path"], "language": detect_language(candidate["path"]), "content": content,
            "size": len(raw), "status": "retrieved", "selection_reasons": candidate["reasons"], "commit_sha": commit["sha"],
        })
        yield {"event": "retrieval", "data": {"retrieved": len(retrieved), "selected": len(selected), "path": candidate["path"], "message": f"Retrieved {len(retrieved)} of {len(selected)} selected files."}}

    discovery["selected_files"] = retrieved
    discovery["retrieval_coverage"] = {
        "commit_sha": commit["sha"], "instruction": focus or None, "mode": analysis_mode,
        "files_selected": len(selected), "files_retrieved": len(retrieved),
        "total_content_bytes": total_bytes, "limits": {"max_files": MAX_RETRIEVED_FILES, "max_file_bytes": MAX_FILE_BYTES, "max_total_bytes": MAX_TOTAL_SOURCE_BYTES},
        "skipped": skipped,
        "complete": not skipped and len(retrieved) == len(selected),
        "message": "Selected source files were retrieved at the default-branch commit." if not skipped and len(retrieved) == len(selected)
        else "Retrieval is partial. See skipped counts and configured limits.",
    }
    yield {"event": "context", "data": {"message": "Prepared repository context from retrieved source files."}}
    return discovery


def retrieve_repository_sources(url: object, instruction: object, client: GitHubClient, mode: object = None) -> dict[str, Any]:
    """Run streaming-capable retrieval to completion for the non-streaming API."""
    steps = stream_retrieve_repository_sources(url, instruction, client, mode)
    while True:
        try:
            next(steps)
        except StopIteration as complete:
            return complete.value


def stream_retrieve_follow_up_sources(
    previous_retrieval: dict[str, Any], question: object, client: GitHubClient
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    """Select question-relevant files at the already selected immutable commit.

    Existing source bodies are reused from the temporary conversation cache. New
    candidates are fetched only from the exact commit used for the initial analysis.
    """
    focus = str(question or "").strip()[:500]
    repository = previous_retrieval["repository"]
    previous_coverage = previous_retrieval["retrieval_coverage"]
    commit_sha = previous_coverage["commit_sha"]
    identifier = parse_repository_url(repository["url"])
    yield {"event": "status", "data": {"stage": "source_search", "message": "Selecting relevant files at the analyzed commit…"}}
    tree_payload = client.tree(identifier, commit_sha)
    selected, skipped = select_files(tree_payload["tree"], focus)
    existing = {
        item["path"]: item for item in previous_retrieval.get("selected_files", [])
        if item.get("status") == "retrieved" and item.get("commit_sha") == commit_sha
    }
    sources: list[dict[str, Any]] = []
    total_bytes = 0
    reused = 0
    downloaded = 0
    for candidate in selected:
        source = existing.get(candidate["path"])
        if source is not None:
            raw_size = source.get("size", 0)
            if not isinstance(raw_size, int) or total_bytes + raw_size > MAX_TOTAL_SOURCE_BYTES:
                skipped["total_content_limit"] = skipped.get("total_content_limit", 0) + 1
                continue
            sources.append(source)
            total_bytes += raw_size
            reused += 1
            yield {"event": "retrieval", "data": {"retrieved": len(sources), "selected": len(selected), "reused": reused, "downloaded": downloaded, "path": candidate["path"], "message": f"Reused {reused} and retrieved {downloaded} relevant files."}}
            continue

        expected_size = candidate.get("size")
        if isinstance(expected_size, int) and total_bytes + expected_size > MAX_TOTAL_SOURCE_BYTES:
            skipped["total_content_limit"] = skipped.get("total_content_limit", 0) + 1
            continue
        payload = client.file_content(identifier, candidate["path"], commit_sha)
        if payload is None:
            skipped["file_unavailable"] = skipped.get("file_unavailable", 0) + 1
            continue
        raw = _decode_file(payload)
        if raw is None or b"\x00" in raw:
            skipped["binary_or_unreadable"] = skipped.get("binary_or_unreadable", 0) + 1
            continue
        if len(raw) > MAX_FILE_BYTES:
            skipped["file_too_large"] = skipped.get("file_too_large", 0) + 1
            continue
        if total_bytes + len(raw) > MAX_TOTAL_SOURCE_BYTES:
            skipped["total_content_limit"] = skipped.get("total_content_limit", 0) + 1
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped["binary_or_unreadable"] = skipped.get("binary_or_unreadable", 0) + 1
            continue
        source = {
            "path": candidate["path"], "language": detect_language(candidate["path"]), "content": content,
            "size": len(raw), "status": "retrieved", "selection_reasons": candidate["reasons"], "commit_sha": commit_sha,
        }
        sources.append(source)
        total_bytes += len(raw)
        downloaded += 1
        yield {"event": "retrieval", "data": {"retrieved": len(sources), "selected": len(selected), "reused": reused, "downloaded": downloaded, "path": candidate["path"], "message": f"Reused {reused} and retrieved {downloaded} relevant files."}}

    complete = not skipped and len(sources) == len(selected)
    follow_up = {
        key: previous_retrieval[key]
        for key in ("repository", "tree", "file_statistics", "language_statistics", "characteristics", "discovery_coverage")
    }
    follow_up["selected_files"] = sources
    follow_up["retrieval_coverage"] = {
        "commit_sha": commit_sha,
        "instruction": focus,
        "files_selected": len(selected),
        "files_retrieved": len(sources),
        "reused_files": reused,
        "downloaded_files": downloaded,
        "total_content_bytes": total_bytes,
        "limits": {"max_files": MAX_RETRIEVED_FILES, "max_file_bytes": MAX_FILE_BYTES, "max_total_bytes": MAX_TOTAL_SOURCE_BYTES},
        "skipped": skipped,
        "complete": complete,
        "message": (
            "Relevant source files were prepared at the original analyzed commit."
            if complete else "Relevant source retrieval is partial. See skipped counts and configured limits."
        ),
    }
    yield {"event": "context", "data": {"message": "Prepared follow-up context from verified source files."}}
    return follow_up
