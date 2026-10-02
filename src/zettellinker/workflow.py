from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from .config import VaultConfig
from .langchain_adapters import (
    LangChainEmbedder,
    LangChainZettelVectorStore,
    note_to_document,
)
from .models import Finding, IndexStats, Note, Suggestion
from .semantic import Embedder, SearchIndex, SemanticEngine
from .vault import VaultGraph, discover_notes, normalize_identity
from .writer import LinkWriter, WriteResult


class ZettelLinkerState(TypedDict, total=False):
    """The unified state passing through the LangGraph workflow."""

    vault_path: str
    config: VaultConfig
    target_note: str | None
    embedder: Any | None
    index_factory: Any | None
    progress_callback: Callable[[str], None] | None

    notes: list[Note]
    documents: list[Document]
    graph: VaultGraph | None
    engine: SemanticEngine | None
    vectorstore: LangChainZettelVectorStore | None
    audit_findings: list[Finding]
    index_stats: IndexStats | None
    suggestions: list[Suggestion]
    write_result: WriteResult | None

    logs: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


def discover_notes_node(state: ZettelLinkerState) -> dict[str, Any]:
    vault = Path(state["vault_path"]).expanduser().resolve()
    config = state["config"]
    progress = state.get("progress_callback")
    notes = state.get("notes")
    if not notes:
        if progress:
            progress(f"Discovering notes in {vault}...")
        notes = discover_notes(vault, config)
    documents = [note_to_document(note) for note in notes]
    log = f"Discovered {len(notes)} markdown notes in vault ({vault.name})"
    return {
        "notes": notes,
        "documents": documents,
        "logs": [log],
    }


def build_graph_node(state: ZettelLinkerState) -> dict[str, Any]:
    notes = state.get("notes", [])
    progress = state.get("progress_callback")
    if progress:
        progress("Building vault link graph...")

    graph = VaultGraph(notes)
    resolved_count = sum(1 for r in graph.resolutions if r.status == "resolved")
    log = (
        f"Constructed graph with {len(notes)} vertices and {len(graph.resolutions)} total wikilinks "
        f"({resolved_count} successfully resolved)"
    )
    return {
        "graph": graph,
        "logs": [log],
    }


def audit_vault_node(state: ZettelLinkerState) -> dict[str, Any]:
    graph = state.get("graph")
    config = state.get("config")
    if not graph or not config:
        return {"audit_findings": [], "logs": ["No graph or config available for audit"]}

    progress = state.get("progress_callback")
    if progress:
        progress("Auditing graph health...")

    findings = graph.findings(config)
    ghosts = sum(1 for f in findings if f.kind == "ghost_link")
    ambig = sum(1 for f in findings if f.kind == "ambiguous_link")
    orphans = sum(1 for f in findings if f.kind == "orphan_note")
    recip = sum(1 for f in findings if f.kind == "missing_reciprocal")

    log = (
        f"Audit findings: {len(findings)} total "
        f"({ghosts} ghost links, {ambig} ambiguous, {orphans} orphans, {recip} missing reciprocals)"
    )
    return {
        "audit_findings": findings,
        "logs": [log],
    }


def index_semantic_node(state: ZettelLinkerState) -> dict[str, Any]:
    vault = Path(state["vault_path"]).expanduser().resolve()
    config = state["config"]
    notes = state.get("notes", [])
    embedder = state.get("embedder")
    index_factory = state.get("index_factory")
    progress = state.get("progress_callback")

    engine = state.get("engine")
    if engine is None:
        engine = SemanticEngine(
            vault,
            config,
            embedder=embedder,
            index_factory=index_factory,
            progress=progress,
        )

    stats = engine.refresh(notes)
    vectorstore = LangChainZettelVectorStore.from_engine(engine)

    log = (
        f"Semantic index updated: {stats.embedded} notes newly embedded, "
        f"{stats.reused} reused from cache, {stats.removed} removed"
    )
    return {
        "engine": engine,
        "vectorstore": vectorstore,
        "index_stats": stats,
        "logs": [log],
    }


def generate_suggestions_node(state: ZettelLinkerState) -> dict[str, Any]:
    engine = state.get("engine")
    graph = state.get("graph")
    target_note = state.get("target_note")
    progress = state.get("progress_callback")

    if not engine or not graph:
        return {"suggestions": [], "errors": ["Engine or graph not initialized for suggestions"]}

    if target_note:
        if progress:
            progress(f"Generating semantic suggestions for note: {target_note}...")
        identity = _resolve_note_identity(graph, target_note)
        suggestions = engine.suggestions_for(identity)
        log = f"Found {len(suggestions)} suggestions for '{identity}'"
    else:
        if progress:
            progress("Generating semantic suggestions across entire vault...")
        suggestions = engine.all_suggestions()
        log = f"Found {len(suggestions)} total semantic suggestions across the vault"

    return {
        "suggestions": suggestions,
        "logs": [log],
    }


def write_links_node(state: ZettelLinkerState) -> dict[str, Any]:
    engine = state.get("engine")
    suggestions = state.get("suggestions", [])
    target_note = state.get("target_note")
    progress = state.get("progress_callback")

    if not engine or not suggestions:
        return {"write_result": None, "logs": ["No suggestions available to write"]}

    if progress:
        progress(f"Applying {len(suggestions)} link connections to vault...")

    writer = LinkWriter(engine)
    source_only = None
    if target_note and engine.graph:
        source_only = _resolve_note_identity(engine.graph, target_note)

    result = writer.apply(suggestions, source_only=source_only)
    log = (
        f"Wrote {result.links_added} links across {len(result.modified)} notes "
        f"(transaction: {result.transaction or 'none'})"
    )
    return {
        "write_result": result,
        "logs": [log],
    }


def _resolve_note_identity(graph: VaultGraph, value: str) -> str:
    normalized = normalize_identity(value)
    if normalized in graph.by_identity:
        return normalized
    matches = graph.by_basename.get(Path(normalized).name, [])
    if len(matches) == 1:
        return matches[0].identity
    if len(matches) > 1:
        options = ", ".join(note.relative_path for note in matches)
        raise ValueError(f"Note name is ambiguous: '{value}'. Matches: {options}")
    raise ValueError(f"Note '{value}' was not found in vault")


def _should_write_condition(state: ZettelLinkerState) -> Literal["write_links", "__end__"]:
    config = state.get("config")
    suggestions = state.get("suggestions", [])
    if config and config.auto_write.enabled and suggestions:
        return "write_links"
    return "__end__"


def create_scan_graph() -> Any:
    """Build compiled LangGraph for full vault scan with audits and optional auto-writing."""
    workflow = StateGraph(ZettelLinkerState)

    workflow.add_node("discover_notes", discover_notes_node)
    workflow.add_node("build_graph", build_graph_node)
    workflow.add_node("audit_vault", audit_vault_node)
    workflow.add_node("index_semantic", index_semantic_node)
    workflow.add_node("generate_suggestions", generate_suggestions_node)
    workflow.add_node("write_links", write_links_node)

    workflow.add_edge(START, "discover_notes")
    workflow.add_edge("discover_notes", "build_graph")
    workflow.add_edge("build_graph", "audit_vault")
    workflow.add_edge("audit_vault", "index_semantic")
    workflow.add_edge("index_semantic", "generate_suggestions")

    workflow.add_conditional_edges(
        "generate_suggestions",
        _should_write_condition,
        {
            "write_links": "write_links",
            "__end__": END,
        },
    )
    workflow.add_edge("write_links", END)

    return workflow.compile()


def create_suggest_graph() -> Any:
    """Build compiled LangGraph for focused single-note suggestion pipeline."""
    workflow = StateGraph(ZettelLinkerState)

    workflow.add_node("discover_notes", discover_notes_node)
    workflow.add_node("build_graph", build_graph_node)
    workflow.add_node("index_semantic", index_semantic_node)
    workflow.add_node("generate_suggestions", generate_suggestions_node)
    workflow.add_node("write_links", write_links_node)

    workflow.add_edge(START, "discover_notes")
    workflow.add_edge("discover_notes", "build_graph")
    workflow.add_edge("build_graph", "index_semantic")
    workflow.add_edge("index_semantic", "generate_suggestions")

    workflow.add_conditional_edges(
        "generate_suggestions",
        _should_write_condition,
        {
            "write_links": "write_links",
            "__end__": END,
        },
    )
    workflow.add_edge("write_links", END)

    return workflow.compile()


def create_audit_graph() -> Any:
    """Build compiled LangGraph for fast vault graph health check without embeddings."""
    workflow = StateGraph(ZettelLinkerState)

    workflow.add_node("discover_notes", discover_notes_node)
    workflow.add_node("build_graph", build_graph_node)
    workflow.add_node("audit_vault", audit_vault_node)

    workflow.add_edge(START, "discover_notes")
    workflow.add_edge("discover_notes", "build_graph")
    workflow.add_edge("build_graph", "audit_vault")
    workflow.add_edge("audit_vault", END)

    return workflow.compile()


def run_vault_scan(
    vault: Path,
    config: VaultConfig,
    embedder: Embedder | None = None,
    index_factory: type[SearchIndex] | None = None,
    progress: Callable[[str], None] | None = None,
    notes: list[Note] | None = None,
) -> ZettelLinkerState:
    """Execute the full vault scan LangGraph workflow."""
    graph = create_scan_graph()
    initial_state: ZettelLinkerState = {
        "vault_path": str(vault.resolve()),
        "config": config,
        "target_note": None,
        "embedder": embedder,
        "index_factory": index_factory,
        "progress_callback": progress,
        "logs": [],
        "errors": [],
    }
    if notes:
        initial_state["notes"] = notes
    return graph.invoke(initial_state)


def run_note_suggest(
    vault: Path,
    config: VaultConfig,
    note: str,
    embedder: Embedder | None = None,
    index_factory: type[SearchIndex] | None = None,
    progress: Callable[[str], None] | None = None,
    notes: list[Note] | None = None,
) -> ZettelLinkerState:
    """Execute the note-targeted suggestion LangGraph workflow."""
    graph = create_suggest_graph()
    initial_state: ZettelLinkerState = {
        "vault_path": str(vault.resolve()),
        "config": config,
        "target_note": note,
        "embedder": embedder,
        "index_factory": index_factory,
        "progress_callback": progress,
        "logs": [],
        "errors": [],
    }
    if notes:
        initial_state["notes"] = notes
    state = graph.invoke(initial_state)
    state["note"] = _resolve_note_identity(state["graph"], note)
    return state


def run_vault_audit(
    vault: Path,
    config: VaultConfig,
    progress: Callable[[str], None] | None = None,
    notes: list[Note] | None = None,
) -> ZettelLinkerState:
    """Execute the audit LangGraph workflow."""
    graph = create_audit_graph()
    initial_state: ZettelLinkerState = {
        "vault_path": str(vault.resolve()),
        "config": config,
        "progress_callback": progress,
        "logs": [],
        "errors": [],
    }
    if notes:
        initial_state["notes"] = notes
    return graph.invoke(initial_state)


resolve_note_identity = _resolve_note_identity

