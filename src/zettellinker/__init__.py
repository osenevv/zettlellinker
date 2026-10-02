"""ZettelLinker package with LangChain and LangGraph orchestration."""

__version__ = "0.2.0"

from .langchain_adapters import (
    LangChainEmbedder,
    LangChainZettelVectorStore,
    ZettelVaultRetriever,
    document_to_note,
    note_to_document,
)
from .workflow import (
    ZettelLinkerState,
    create_audit_graph,
    create_scan_graph,
    create_suggest_graph,
    resolve_note_identity,
    run_note_suggest,
    run_vault_audit,
    run_vault_scan,
)

__all__ = [
    "__version__",
    "LangChainEmbedder",
    "LangChainZettelVectorStore",
    "ZettelVaultRetriever",
    "note_to_document",
    "document_to_note",
    "ZettelLinkerState",
    "create_scan_graph",
    "create_suggest_graph",
    "create_audit_graph",
    "resolve_note_identity",
    "run_vault_scan",
    "run_note_suggest",
    "run_vault_audit",
]
