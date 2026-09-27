"""Analysis-provider boundary. Supports Anthropic (API-based) and IBM Bob (CLI-based, local only)."""

from .base import (
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
from .factory import get_analysis_provider

__all__ = [
    "AnalysisProvider", "AnalysisRequest", "AnalysisResponse", "ArchitectureComponent",
    "ArchitectureConnection", "ArchitectureData", "ArchitectureModule", "ConversationTurn", "SourceFile", "get_analysis_provider",
]
