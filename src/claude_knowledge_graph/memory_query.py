#!/usr/bin/env python3
"""Memory query engine: semantic search over accumulated knowledge.

Provides CLI-friendly search and AI-agent-friendly context retrieval.
"""

import json
from datetime import datetime
from pathlib import Path

from claude_knowledge_graph.config import (
    PROCESSED_DIR,
    PROFILE_PATH,
)
from claude_knowledge_graph.embeddings import (
    cosine_similarity,
    get_embedding,
    is_configured,
    load_index,
    start_embed_server,
    stop_embed_server,
)


def _load_all_written_qas() -> list[tuple[str, dict]]:
    """Load all written Q&A pairs from processed directory."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for filepath in sorted(PROCESSED_DIR.glob("*.json")):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception):
            continue
        if qa.get("status") in ("written", "processed"):
            file_id = filepath.stem
            results.append((file_id, qa))
    return results


def _apply_filters(
    qa: dict, filters: dict | None
) -> bool:
    """Check if a Q&A pair passes the given filters."""
    if not filters:
        return True

    qwen = qa.get("qwen_result", {})

    if "memory_type" in filters:
        if qwen.get("memory_type", "dynamic") != filters["memory_type"]:
            return False

    if "category" in filters:
        if qwen.get("category", "other") != filters["category"]:
            return False

    if "importance_min" in filters:
        if qwen.get("importance", 3) < filters["importance_min"]:
            return False

    if "tags" in filters:
        qa_tags = set(qwen.get("tags", []))
        filter_tags = set(filters["tags"]) if isinstance(filters["tags"], list) else {filters["tags"]}
        if not filter_tags & qa_tags:
            return False

    if "date_from" in filters:
        ts = qa.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            if dt.strftime("%Y-%m-%d") < filters["date_from"]:
                return False
        except (ValueError, TypeError):
            return False

    if "date_to" in filters:
        ts = qa.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            if dt.strftime("%Y-%m-%d") > filters["date_to"]:
                return False
        except (ValueError, TypeError):
            return False

    return True


def query(
    query_text: str,
    top_k: int = 5,
    filters: dict | None = None,
) -> list[dict]:
    """Semantic search across all accumulated Q&A pairs.

    Args:
        query_text: Natural language query
        top_k: Number of results to return
        filters: Optional filters (memory_type, category, importance_min, tags, date_from, date_to)

    Returns list of:
        {file_id, title, summary, similarity, memory_type, importance, concepts, category, date, cwd}
    """
    if not is_configured():
        raise RuntimeError(
            "Embedding model not configured. "
            "Set CKG_EMBED_MODEL_PATH or download a model to ~/.local/share/claude-knowledge-graph/models/"
        )

    index = load_index()
    entries = index.get("entries", {})

    if not entries:
        return []

    # Load all Q&A data
    all_qas = _load_all_written_qas()
    qa_map = {file_id: qa for file_id, qa in all_qas}

    # Generate query embedding
    query_embedding = get_embedding(query_text)

    # Compute similarities
    scored: list[tuple[str, float, dict]] = []
    for file_id, entry in entries.items():
        embedding = entry.get("embedding", [])
        if not embedding:
            continue

        qa = qa_map.get(file_id)
        if qa is None:
            continue

        if not _apply_filters(qa, filters):
            continue

        sim = cosine_similarity(query_embedding, embedding)
        scored.append((file_id, sim, qa))

    # Sort by similarity, return top_k
    scored.sort(key=lambda x: x[1], reverse=True)

    results = []
    for file_id, sim, qa in scored[:top_k]:
        qwen = qa.get("qwen_result", {})
        ts = qa.get("timestamp", "")
        try:
            date_str = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_str = "unknown"

        results.append({
            "file_id": file_id,
            "title": qwen.get("title", "Untitled"),
            "summary": qwen.get("summary", ""),
            "similarity": round(sim, 4),
            "memory_type": qwen.get("memory_type", "dynamic"),
            "importance": qwen.get("importance", 3),
            "concepts": qwen.get("key_concepts", []),
            "category": qwen.get("category", "other"),
            "tags": qwen.get("tags", []),
            "date": date_str,
            "cwd": qa.get("cwd", ""),
        })

    return results


def query_concepts(
    query_text: str,
    top_k: int = 5,
) -> list[dict]:
    """Find concepts most relevant to a query.

    Aggregates Q&A similarities per concept and returns top concepts.
    """
    results = query(query_text, top_k=top_k * 3)  # Get more for aggregation

    concept_scores: dict[str, list[float]] = {}
    for r in results:
        for concept in r.get("concepts", []):
            concept_scores.setdefault(concept, []).append(r["similarity"])

    # Average similarity per concept
    aggregated = []
    for concept, scores in concept_scores.items():
        avg_sim = sum(scores) / len(scores)
        aggregated.append({
            "concept": concept,
            "avg_similarity": round(avg_sim, 4),
            "occurrences": len(scores),
        })

    aggregated.sort(key=lambda x: x["avg_similarity"], reverse=True)
    return aggregated[:top_k]


def get_context(
    query_text: str,
    top_k: int = 5,
) -> str:
    """Get combined user profile + relevant memories as a context string.

    Designed for AI agents to consume as additional context.
    """
    parts = []

    # User profile
    if PROFILE_PATH and PROFILE_PATH.exists():
        profile_content = PROFILE_PATH.read_text()
        # Strip frontmatter
        if profile_content.startswith("---"):
            end = profile_content.find("---", 3)
            if end != -1:
                profile_content = profile_content[end + 3:].strip()
        parts.append(profile_content)
    else:
        parts.append("# User Profile\n\n_No profile available yet._")

    # Relevant memories
    try:
        results = query(query_text, top_k=top_k)
        if results:
            memory_lines = ["\n# Relevant Memories\n"]
            for i, r in enumerate(results, 1):
                memory_lines.append(
                    f"{i}. [{r['similarity']:.2f}] {r['title']} "
                    f"({r['date']}) — {r['memory_type']}, importance: {r['importance']}"
                )
                memory_lines.append(f"   {r['summary']}")
                if r["concepts"]:
                    memory_lines.append(f"   Concepts: {', '.join(r['concepts'])}")
                memory_lines.append("")
            parts.append("\n".join(memory_lines))
        else:
            parts.append("\n# Relevant Memories\n\n_No relevant memories found._")
    except Exception as e:
        parts.append(f"\n# Relevant Memories\n\n_Search failed: {e}_")

    return "\n".join(parts)


def format_results_table(results: list[dict]) -> str:
    """Format query results as a readable table for CLI output."""
    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        sim_bar = "█" * int(r["similarity"] * 10)
        lines.append(
            f"  {i}. [{r['similarity']:.2f}] {sim_bar} "
            f"{r['title']} ({r['date']})"
        )
        lines.append(
            f"     {r['memory_type']}, importance: {r['importance']}, "
            f"category: {r['category']}"
        )
        lines.append(f"     {r['summary']}")
        if r["concepts"]:
            concept_links = ", ".join(f"[[{c}]]" for c in r["concepts"])
            lines.append(f"     Concepts: {concept_links}")
        lines.append("")

    return "\n".join(lines)
