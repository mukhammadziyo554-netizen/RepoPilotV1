from dataclasses import dataclass, field
from typing import Any, Generator, Mapping, Protocol


@dataclass(frozen=True)
class SourceFile:
    """A selected, immutable repository file supplied to an analysis provider."""

    path: str
    language: str
    size: int
    content: str


@dataclass(frozen=True)
class ConversationTurn:
    role: str
    text: str


@dataclass(frozen=True)
class ArchitectureComponent:
    id: str
    label: str
    kind: str
    paths: tuple[str, ...]
    description: str = ""
    module_id: str = ""


@dataclass(frozen=True)
class ArchitectureModule:
    """A real directory or file group used as a visual diagram container."""

    id: str
    label: str
    paths: tuple[str, ...]
    kind: str = "module"


@dataclass(frozen=True)
class ArchitectureConnection:
    source: str
    target: str
    label: str = "uses"
    evidence_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArchitectureData:
    """Validated provider-neutral architecture data for the web and PDF views."""

    components: tuple[ArchitectureComponent, ...]
    connections: tuple[ArchitectureConnection, ...] = ()
    limitations: str = ""
    modules: tuple[ArchitectureModule, ...] = ()


@dataclass(frozen=True)
class AnalysisRequest:
    system_prompt: str
    user_prompt: str
    repository_name: str
    commit_sha: str
    retrieved_file_count: int
    retrieval_complete: bool
    # The formatted prompts preserve the active Anthropic adapter. These explicit
    # fields are the stable product contract a future documented provider uses.
    mode: str = "overview"
    instruction: str | None = None
    repository_url: str = ""
    repository_metadata: Mapping[str, Any] = field(default_factory=dict)
    source_files: tuple[SourceFile, ...] = ()
    retrieval_coverage: Mapping[str, Any] = field(default_factory=dict)
    conversation_history: tuple[ConversationTurn, ...] = ()


@dataclass(frozen=True)
class AnalysisResponse:
    text: str
    provider: str
    model: str
    source_references: tuple[str, ...] = ()
    architecture: ArchitectureData | None = None
    completed: bool = True


class AnalysisProvider(Protocol):
    """A provider receives prepared context, never fetches GitHub data itself."""

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        ...

    def stream_analyze(self, request: AnalysisRequest) -> Generator[str, None, None]:
        ...
