"""Provider-neutral preparation and normalization of repository analysis."""

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .errors import DiscoveryError
from .providers.base import (
    AnalysisProvider,
    AnalysisRequest,
    AnalysisResponse,
    ArchitectureComponent,
    ArchitectureConnection,
    ArchitectureData,
    ArchitectureModule,
    ConversationTurn,
    SourceFile,
)

INSTRUCTIONS_PATH = Path(__file__).with_name("analysis_instructions.md")


def load_analysis_instructions() -> str:
    """Load the one central, provider-independent instruction file."""
    try:
        instructions = INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise DiscoveryError("analysis_configuration_error", "RepoPilot's analysis instructions could not be loaded.", 500) from error
    if not instructions:
        raise DiscoveryError("analysis_configuration_error", "RepoPilot's analysis instructions are empty.", 500)
    return instructions


def _source_documents(source_files: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f'<file path="{item["path"]}" language="{item.get("language") or "Unknown"}" size="{item["size"]}">\n'
        f'{item["content"]}\n</file>'
        for item in source_files
        if item.get("status") == "retrieved"
    )


def _repository_context(retrieval: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    repository = retrieval["repository"]
    coverage = retrieval["retrieval_coverage"]
    source_files = retrieval.get("selected_files", [])
    skipped_counts = coverage["skipped"]
    coverage_message = coverage["message"]
    coverage_statement = (
        "Retrieval coverage is complete for the selected candidate set."
        if coverage["complete"]
        else f"Retrieval coverage is incomplete: {coverage_message} Skipped counts: {skipped_counts}."
    )
    context = f"""Repository: {repository['full_name']}
Canonical URL: {repository['url']}
Description: {repository.get('description') or 'Not provided'}
Primary language: {repository.get('primary_language') or 'Not detected'}
Languages: {', '.join((repository.get('languages') or {}).keys()) or 'Not reported'}
Default branch: {repository['default_branch']}
Commit SHA: {coverage['commit_sha']}
Retrieved files: {coverage['files_retrieved']} of {coverage['files_selected']}
{coverage_statement}

<repository_source_files>
{_source_documents(source_files)}
</repository_source_files>
"""
    return context, repository, coverage


def _request_mode(coverage: dict[str, Any]) -> str:
    """Keep old direct callers useful while routes always supply an explicit mode."""
    selected = str(coverage.get("mode") or "").strip().lower()
    if selected in {"overview", "architecture", "custom"}:
        return selected
    return "custom" if str(coverage.get("instruction") or "").strip() else "overview"


def _source_file_contract(retrieval: dict[str, Any]) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(
            path=str(item["path"]),
            language=str(item.get("language") or "Unknown"),
            size=int(item.get("size") or 0),
            content=str(item.get("content") or ""),
        )
        for item in retrieval.get("selected_files", [])
        if item.get("status") == "retrieved"
    )


def _repository_metadata(repository: dict[str, Any]) -> dict[str, Any]:
    """Only GitHub facts already retrieved by RepoPilot enter the provider contract."""
    return {
        "full_name": repository.get("full_name", ""),
        "url": repository.get("url", ""),
        "description": repository.get("description"),
        "primary_language": repository.get("primary_language"),
        "languages": dict(repository.get("languages") or {}),
        "default_branch": repository.get("default_branch", ""),
    }


def _initial_response_mode(instruction: object, mode: object = None) -> str:
    user_instruction = str(instruction or "").strip()
    selected_mode = str(mode or "").strip().lower()
    if selected_mode == "architecture":
        return f"""Response mode: architecture. Create a professional, evidence-based technical diagram from only the supplied source files. Start with exactly one fenced `repopilot-architecture` JSON block, using this schema:
```repopilot-architecture
{{"modules":[{{"id":"core","label":"Core module","paths":["real/path"]}}],"components":[{{"id":"entry","label":"real-file-name","kind":"frontend|backend|api|database|library|cli|service|config","module":"core","paths":["real/path"],"description":"short verified role"}}],"connections":[{{"from":"entry","to":"other","label":"imports|calls|exposes|uses|owns|depends on","evidence_paths":["real/source/path"]}}],"limitations":"brief factual limitation or empty string"}}
```
Use 1–8 meaningful modules and 1–14 important files/components. Modules must group real related paths (normally directories or package areas); component labels should use the actual filename or a short role plus the real filename. Every component must cite a retrieved path. Every connection must be directional, use one of the listed relationship labels, and include a retrieved evidence path where that relationship is established. Visualize principal code only—never create generic conceptual boxes or imaginary layers. Then provide concise `## Architecture explanation` and `## Component relationships` sections. Include a `## Evidence and limitations` section only when material. Do not invent components, paths, services, execution paths, or relationships. {f"Focus note: {user_instruction}" if user_instruction else ""}"""
    if selected_mode == "custom" or (not selected_mode and user_instruction):
        if not user_instruction:
            raise DiscoveryError("invalid_request", "Enter a question for Ask RepoPilot mode.")
        return f"""Response mode: user-directed request. Answer only the user's instruction below. Do not add the default overview, architecture view, or unrelated sections. Respect an explicitly requested detailed or comprehensive scope.

User instruction: {user_instruction}"""
    if selected_mode in {"", "overview"}:
        return f"""Response mode: default overview. Provide only the concise overview required by the system instructions. This is a hard output contract: privately count the words before returning and revise the answer to 72–92 words (within the required 60–100-word range); never exceed 120 words. Keep the required three Markdown sections and their minimum per-section detail. Do not report a word count. If the retrieved evidence is sparse, use the available words to state the limitation accurately without inventing details. Do not add a comprehensive report, file inventory, recommendations, or a coverage section. {f"Optional focus note: {user_instruction}" if user_instruction else ""}"""
    return _initial_response_mode(user_instruction, "overview")


def prepare_analysis_request(retrieval: dict[str, Any]) -> AnalysisRequest:
    context, repository, coverage = _repository_context(retrieval)
    mode = _request_mode(coverage)
    user_prompt = f"""{_initial_response_mode(coverage.get('instruction'), coverage.get('mode'))}

{context}"""
    return AnalysisRequest(
        system_prompt=load_analysis_instructions(),
        user_prompt=user_prompt,
        repository_name=repository["full_name"],
        commit_sha=coverage["commit_sha"],
        retrieved_file_count=coverage["files_retrieved"],
        retrieval_complete=coverage["complete"],
        mode=mode,
        instruction=str(coverage.get("instruction") or "").strip() or None,
        repository_url=repository["url"],
        repository_metadata=_repository_metadata(repository),
        source_files=_source_file_contract(retrieval),
        retrieval_coverage=dict(coverage),
    )


def prepare_follow_up_request(
    retrieval: dict[str, Any], question: object, history: object
) -> AnalysisRequest:
    """Prepare a bounded, provider-neutral continuation at the original commit."""
    prompt = str(question or "").strip()[:500]
    if not prompt:
        raise DiscoveryError("invalid_request", "Enter a follow-up question.")
    prior_messages = history if isinstance(history, list) else []
    transcript: list[str] = []
    history_contract: list[ConversationTurn] = []
    remaining = 16_000
    for item in prior_messages[-12:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        text = text[:3_000]
        if len(text) > remaining:
            text = text[-remaining:]
        if not text:
            break
        speaker = "User" if item["role"] == "user" else "RepoPilot"
        transcript.append(f"{speaker}: {text}")
        history_contract.append(ConversationTurn(role=item["role"], text=text))
        remaining -= len(text)
        if remaining <= 0:
            break

    context, repository, coverage = _repository_context(retrieval)
    continuation = "\n".join(transcript) or "No prior conversation messages were supplied."
    user_prompt = f"""Response mode: follow-up. Answer the latest user question directly, using the prior conversation only when it is relevant. Do not repeat the initial repository overview or already-given explanations unless the user explicitly asks for them. Do not add unrelated sections.

{context}

<conversation_history>
{continuation}
</conversation_history>

Follow-up question: {prompt}

Answer the follow-up question directly. Use only the verified repository metadata and source files above. Cite inspected paths in backticks. If the files are insufficient, say exactly what cannot be verified rather than guessing."""
    return AnalysisRequest(
        system_prompt=load_analysis_instructions(),
        user_prompt=user_prompt,
        repository_name=repository["full_name"],
        commit_sha=coverage["commit_sha"],
        retrieved_file_count=coverage["files_retrieved"],
        retrieval_complete=coverage["complete"],
        mode=_request_mode(coverage),
        instruction=prompt,
        repository_url=repository["url"],
        repository_metadata=_repository_metadata(repository),
        source_files=_source_file_contract(retrieval),
        retrieval_coverage=dict(coverage),
        conversation_history=tuple(history_contract),
    )


_ARCHITECTURE_BLOCK = re.compile(r"```repopilot-architecture\s*\n([\s\S]*?)```", re.IGNORECASE)
_ARCHITECTURE_KINDS = {"frontend", "backend", "api", "database", "library", "cli", "service", "config"}
_COMPONENT_ID = re.compile(r"^[a-z0-9-]{1,40}$", re.IGNORECASE)
_RELATIONSHIP_LABELS = {"imports", "calls", "exposes", "uses", "owns", "depends on"}


def _fallback_module_id(path: str) -> str:
    """Derive a visual file group from a real directory, never a conceptual layer."""
    parent = path.rsplit("/", 1)[0] if "/" in path else "root"
    return re.sub(r"[^a-z0-9-]+", "-", parent.lower()).strip("-")[:40] or "root"


def _fallback_modules(components: list[ArchitectureComponent]) -> list[ArchitectureModule]:
    grouped: dict[str, list[str]] = {}
    for component in components:
        identifier = component.module_id or _fallback_module_id(component.paths[0])
        grouped.setdefault(identifier, []).extend(component.paths)
    return [ArchitectureModule(
        id=identifier,
        label="Repository root" if identifier == "root" else identifier.replace("-", "/"),
        paths=tuple(dict.fromkeys(paths)),
    ) for identifier, paths in grouped.items()]


def _validated_modules(items: object, available_paths: set[str]) -> list[ArchitectureModule]:
    modules: list[ArchitectureModule] = []
    seen_ids: set[str] = set()
    for item in (items if isinstance(items, (list, tuple)) else [])[:8]:
        identifier = str(item.get("id") or "").strip() if isinstance(item, dict) else str(getattr(item, "id", "")).strip()
        label = str(item.get("label") or "").strip() if isinstance(item, dict) else str(getattr(item, "label", "")).strip()
        raw_paths = item.get("paths", ()) if isinstance(item, dict) else getattr(item, "paths", ())
        paths = tuple(path for path in raw_paths[:12] if isinstance(path, str) and path in available_paths)
        if not _COMPONENT_ID.fullmatch(identifier) or identifier in seen_ids or not label or not paths:
            continue
        seen_ids.add(identifier)
        modules.append(ArchitectureModule(identifier, label[:52], paths, "module"))
    return modules


def parse_architecture_data(text: object, source_files: tuple[SourceFile, ...]) -> ArchitectureData | None:
    """Normalize the documented architecture response convention without vendor code.

    Invalid paths and relationships are rejected instead of becoming clickable UI or
    export data. A model may omit this optional convention; normal text still works.
    """
    block = _ARCHITECTURE_BLOCK.search(str(text or ""))
    if not block:
        return None
    try:
        payload = json.loads(block.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    available_paths = {file.path for file in source_files}
    modules = _validated_modules(payload.get("modules"), available_paths)
    module_ids = {module.id for module in modules}
    components: list[ArchitectureComponent] = []
    seen_ids: set[str] = set()
    for item in (payload.get("components") if isinstance(payload.get("components"), list) else [])[:14]:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()[:46]
        if not _COMPONENT_ID.fullmatch(identifier) or identifier in seen_ids or not label:
            continue
        paths = tuple(
            path for path in item.get("paths", [])[:3]
            if isinstance(path, str) and path in available_paths
        ) if isinstance(item.get("paths"), list) else ()
        # Every diagram node must point to real inspected evidence.
        if not paths:
            continue
        seen_ids.add(identifier)
        kind = str(item.get("kind") or "library").lower()
        module_id = str(item.get("module") or "").strip()
        if module_id not in module_ids:
            module_id = _fallback_module_id(paths[0])
        components.append(ArchitectureComponent(
            id=identifier,
            label=label,
            kind=kind if kind in _ARCHITECTURE_KINDS else "library",
            paths=paths,
            description=str(item.get("description") or "").strip()[:180],
            module_id=module_id,
        ))
    if not components:
        return None
    if not modules:
        modules = _fallback_modules(components)
        module_ids = {module.id for module in modules}
    components = [ArchitectureComponent(
        item.id, item.label, item.kind, item.paths, item.description,
        item.module_id if item.module_id in module_ids else _fallback_module_id(item.paths[0]),
    ) for item in components]
    component_ids = {component.id for component in components}
    component_paths = {component.id: set(component.paths) for component in components}
    connections: list[ArchitectureConnection] = []
    for item in (payload.get("connections") if isinstance(payload.get("connections"), list) else [])[:14]:
        if not isinstance(item, dict):
            continue
        source, target = str(item.get("from") or ""), str(item.get("to") or "")
        label = str(item.get("label") or "uses").strip().lower()
        evidence = tuple(path for path in item.get("evidence_paths", [])[:3] if isinstance(path, str) and path in available_paths) if isinstance(item.get("evidence_paths"), list) else ()
        if source in component_ids and target in component_ids and source != target and label in _RELATIONSHIP_LABELS and evidence and set(evidence).intersection(component_paths[source]):
            connections.append(ArchitectureConnection(source=source, target=target, label=label, evidence_paths=evidence))
    return ArchitectureData(
        components=tuple(components),
        modules=tuple(modules),
        connections=tuple(connections),
        limitations=str(payload.get("limitations") or "").strip()[:280],
    )


def _validated_architecture_data(data: ArchitectureData, source_files: tuple[SourceFile, ...]) -> ArchitectureData | None:
    """Apply the same evidence boundary to provider-native structured output."""
    available_paths = {file.path for file in source_files}
    modules = _validated_modules(data.modules, available_paths)
    module_ids = {module.id for module in modules}
    components: list[ArchitectureComponent] = []
    seen_ids: set[str] = set()
    for item in data.components[:14]:
        if not _COMPONENT_ID.fullmatch(item.id) or item.id in seen_ids or not item.label.strip():
            continue
        paths = tuple(path for path in item.paths[:3] if path in available_paths)
        if not paths:
            continue
        seen_ids.add(item.id)
        components.append(ArchitectureComponent(
            id=item.id,
            label=item.label.strip()[:46],
            kind=item.kind if item.kind in _ARCHITECTURE_KINDS else "library",
            paths=paths,
            description=item.description.strip()[:180],
            module_id=item.module_id if item.module_id in module_ids else _fallback_module_id(paths[0]),
        ))
    if not components:
        return None
    if not modules:
        modules = _fallback_modules(components)
        module_ids = {module.id for module in modules}
    valid_ids = {item.id for item in components}
    component_paths = {item.id: set(item.paths) for item in components}
    connections = tuple(
        ArchitectureConnection(item.source, item.target, item.label.lower()[:64], tuple(path for path in item.evidence_paths[:3] if path in available_paths))
        for item in data.connections[:14]
        if item.source in valid_ids and item.target in valid_ids and item.source != item.target
        and item.label.lower() in _RELATIONSHIP_LABELS
        and set(item.evidence_paths).intersection(component_paths[item.source])
    )
    return ArchitectureData(components=tuple(components), modules=tuple(modules), connections=connections, limitations=data.limitations.strip()[:280])


def analysis_payload(request: AnalysisRequest, response: AnalysisResponse) -> dict[str, Any]:
    """Produce stable analysis data consumed by HTTP, streaming, UI, and PDF flows."""
    architecture = response.architecture
    if architecture is None and request.mode == "architecture":
        architecture = parse_architecture_data(response.text, request.source_files)
    elif architecture is not None:
        architecture = _validated_architecture_data(architecture, request.source_files)
    references = response.source_references or tuple(
        file.path for file in request.source_files if f"`{file.path}`" in response.text
    )
    available_paths = {file.path for file in request.source_files}
    references = tuple(path for path in references if path in available_paths)
    payload: dict[str, Any] = {
        "text": response.text,
        "provider": response.provider,
        "model": response.model,
        "completed": response.completed,
        "mode": request.mode,
        "source_references": list(references),
        "source_file_count": request.retrieved_file_count,
        "commit_sha": request.commit_sha,
        "retrieval_complete": request.retrieval_complete,
    }
    if architecture is not None:
        architecture_payload = asdict(architecture)
        architecture_payload["modules"] = list(architecture_payload["modules"])
        architecture_payload["components"] = list(architecture_payload["components"])
        architecture_payload["connections"] = list(architecture_payload["connections"])
        for component in architecture_payload["components"]:
            component["paths"] = list(component["paths"])
            component["module"] = component.pop("module_id")
        for module in architecture_payload["modules"]:
            module["paths"] = list(module["paths"])
        for connection in architecture_payload["connections"]:
            connection["from"] = connection.pop("source")
            connection["to"] = connection.pop("target")
            connection["evidence_paths"] = list(connection["evidence_paths"])
        payload["architecture"] = architecture_payload
    return payload


def analyze_retrieval(retrieval: dict[str, Any], provider: AnalysisProvider) -> dict[str, Any]:
    request = prepare_analysis_request(retrieval)
    response: AnalysisResponse = provider.analyze(request)
    retrieval["analysis"] = analysis_payload(request, response)
    return retrieval
