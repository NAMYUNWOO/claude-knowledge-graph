#!/usr/bin/env python3
"""Obsidian note writer: converts processed Q&A pairs to knowledge graph notes.

Writes markdown files directly to the Obsidian vault directory.
Creates session notes, daily indexes, concept notes, and a Map of Content (MOC).
"""

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from claude_knowledge_graph.config import (
    CONCEPTS_DIR,
    DAILY_DIR,
    KNOWLEDGE_GRAPH_DIR,
    LOGS_DIR,
    MOC_PATH,
    PROCESSED_DIR,
    PROFILE_PATH,
    SESSIONS_DIR,
)
from claude_knowledge_graph.memory_relations import (
    build_version_history,
    classify_concept_relations,
)

LOG_FILE = LOGS_DIR / "obsidian_writer.log"


def log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = name.strip()
    return name[:100] if name else "untitled"


def truncate(text: str, max_len: int = 500) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def get_processed_files() -> list[Path]:
    """Get all processed Q&A pair files."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(PROCESSED_DIR.glob("*.json"))


# ── Step 1: Session filename helper ──


def session_filename(qa: dict) -> str:
    """Generate a session note filename from Q&A data."""
    qwen = qa.get("qwen_result", {})
    title = qwen.get("title", "Untitled")
    ts = qa.get("timestamp", "")
    try:
        date_str = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = datetime.now().strftime("%Y-%m-%d")
    return f"{date_str}_{sanitize_filename(title)}"


# ── Step 2: Weighted similarity ──


def _load_embeddings_index() -> dict:
    """Load embeddings index for similarity enhancement (cached per session)."""
    if not hasattr(_load_embeddings_index, "_cache"):
        try:
            from claude_knowledge_graph.embeddings import load_index
            _load_embeddings_index._cache = load_index()
        except Exception:
            _load_embeddings_index._cache = {}
    return _load_embeddings_index._cache


def compute_similarity(qa1: dict, qa2: dict) -> tuple[float, list[str]]:
    """Compute weighted similarity between two Q&A pairs.

    Returns (score, list of reason strings).
    Uses tag/concept overlap + optional embedding cosine similarity.
    """
    qwen1 = qa1.get("qwen_result", {})
    qwen2 = qa2.get("qwen_result", {})

    score = 0.0
    reasons: list[str] = []

    # Shared tags: 0.2 per tag, capped at 0.8
    tags1 = set(qwen1.get("tags", []))
    tags2 = set(qwen2.get("tags", []))
    shared_tags = tags1 & tags2
    if shared_tags:
        tag_score = min(len(shared_tags) * 0.2, 0.8)
        score += tag_score
        reasons.append(f"shared tags: {', '.join(sorted(shared_tags))}")

    # Shared concepts: 0.4 per concept, capped at 1.2
    concepts1 = set(qwen1.get("key_concepts", []))
    concepts2 = set(qwen2.get("key_concepts", []))
    shared_concepts = concepts1 & concepts2
    if shared_concepts:
        concept_score = min(len(shared_concepts) * 0.4, 1.2)
        score += concept_score
        reasons.append(f"shared concepts: {', '.join(sorted(shared_concepts))}")

    # Same category: 0.15
    cat1 = qwen1.get("category", "")
    cat2 = qwen2.get("category", "")
    if cat1 and cat2 and cat1 == cat2:
        score += 0.15
        reasons.append(f"same category: {cat1}")

    # Same cwd (project): 0.2
    cwd1 = qa1.get("cwd", "").replace("\\", "/")
    cwd2 = qa2.get("cwd", "").replace("\\", "/")
    if cwd1 and cwd2 and cwd1 == cwd2:
        score += 0.2
        reasons.append("same project")

    # Embedding cosine similarity: weighted 0.5, capped at 0.5
    embed_index = _load_embeddings_index()
    entries = embed_index.get("entries", {})
    if entries:
        # Find embeddings by session_id matching
        id1 = qa1.get("session_id", "")
        id2 = qa2.get("session_id", "")
        emb1 = None
        emb2 = None
        for file_id, entry in entries.items():
            if id1 and id1 in file_id:
                emb1 = entry.get("embedding")
            if id2 and id2 in file_id:
                emb2 = entry.get("embedding")
        if emb1 and emb2:
            from claude_knowledge_graph.embeddings import cosine_similarity as cos_sim
            cos = cos_sim(emb1, emb2)
            embed_score = min(cos * 0.5, 0.5)
            if embed_score > 0.1:
                score += embed_score
                reasons.append(f"semantic: {cos:.2f}")

    return score, reasons


# ── Step 3: Build session relations ──


def build_session_relations(
    qa_list: list[dict],
) -> dict[str, list[tuple[str, float, list[str]]]]:
    """Map each session filename to 'See Also' entries based on similarity.

    Returns {filename: [(other_filename, score, reasons), ...]}.
    Strong matches (>=0.8) are unlimited; moderate (0.6-0.8) capped at 5.
    """
    if len(qa_list) < 2:
        return {}

    # Pre-compute filenames
    fnames = [session_filename(qa) for qa in qa_list]

    # Compute all pairwise similarities
    pairs: dict[str, list[tuple[str, float, list[str]]]] = defaultdict(list)
    for i in range(len(qa_list)):
        for j in range(i + 1, len(qa_list)):
            score, reasons = compute_similarity(qa_list[i], qa_list[j])
            if score >= 0.6:
                pairs[fnames[i]].append((fnames[j], score, reasons))
                pairs[fnames[j]].append((fnames[i], score, reasons))

    # Apply tiered cap: strong (>=0.8) unlimited, moderate (0.6-0.8) max 5
    result: dict[str, list[tuple[str, float, list[str]]]] = {}
    for fname, matches in pairs.items():
        strong = [(m, s, r) for m, s, r in matches if s >= 0.8]
        moderate = sorted(
            [(m, s, r) for m, s, r in matches if s < 0.8],
            key=lambda x: x[1],
            reverse=True,
        )[:5]
        result[fname] = sorted(strong + moderate, key=lambda x: x[1], reverse=True)

    return result


# ── Step 4: Write session note ──


def write_session_note(
    qa: dict, see_also: list[tuple[str, float, list[str]]]
) -> Path:
    """Write an individual session note to sessions/ directory."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    fname = session_filename(qa)
    session_file = SESSIONS_DIR / f"{fname}.md"

    qwen = qa.get("qwen_result", {})
    title = qwen.get("title", "Untitled")
    summary = qwen.get("summary", "")
    tags = qwen.get("tags", [])
    category = qwen.get("category", "other")
    key_concepts = qwen.get("key_concepts", [])
    prompt = qa.get("prompt", "")
    response = qa.get("response", "")
    session_id = qa.get("session_id", "")
    cwd = qa.get("cwd", "").replace("\\", "/")

    ts = qa.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
    except (ValueError, TypeError):
        date_str = datetime.now().strftime("%Y-%m-%d")
        time_str = "00:00"

    tags_yaml = "\n".join(f"  - {t}" for t in tags) if tags else "  - untagged"
    tags_inline = " ".join(f"#{t}" for t in tags) if tags else ""
    concepts_str = ", ".join(f"[[{c}]]" for c in key_concepts) if key_concepts else ""

    # Tool summary data
    tool_summary = qa.get("tool_summary", {})
    files_modified = tool_summary.get("files_modified", [])
    commands_executed = tool_summary.get("commands_executed", [])
    tool_counts = tool_summary.get("tool_counts", {})
    total_tools = sum(tool_counts.values()) if tool_counts else 0

    # Frontmatter extras for tool summary
    tool_frontmatter = ""
    if tool_summary:
        tool_frontmatter = f"\nfiles_modified: {len(files_modified)}\ntools_used: {total_tools}"

    # See Also section
    see_also_lines = ""
    if see_also:
        lines = []
        for other_fname, _score, reasons in see_also:
            reason_str = " — " + "; ".join(reasons) if reasons else ""
            lines.append(f"- [[{other_fname}]]{reason_str}")
        see_also_lines = "\n## See Also\n\n" + "\n".join(lines) + "\n"

    # Session Activity section
    activity_section = ""
    if tool_summary:
        activity_parts = []
        if files_modified:
            file_lines = "\n".join(f"  - `{f}`" for f in files_modified)
            activity_parts.append(f"### Files Modified ({len(files_modified)})\n{file_lines}")
        if commands_executed:
            cmd_lines = "\n".join(f"  - `{c}`" for c in commands_executed)
            activity_parts.append(f"### Commands Executed ({len(commands_executed)})\n{cmd_lines}")
        if tool_counts:
            counts_str = ", ".join(f"{k}: {v}" for k, v in sorted(tool_counts.items()))
            activity_parts.append(f"**Tool Usage** ({total_tools} total): {counts_str}")
        if activity_parts:
            activity_section = "\n## Session Activity\n\n" + "\n\n".join(activity_parts) + "\n"

    # Memory type and importance
    memory_type = qwen.get("memory_type", "dynamic")
    importance = qwen.get("importance", 3)
    memory_frontmatter = f"\nmemory_type: {memory_type}\nimportance: {importance}"

    # Memory type callout
    if memory_type == "static":
        memory_callout = f"\n> [!info] Static Memory (Importance: {importance}/5)\n"
    else:
        memory_callout = f"\n> [!abstract] Dynamic Memory (Importance: {importance}/5)\n"

    content = f"""---
title: "{title}"
date: {date_str}
time: "{time_str}"
session_id: {session_id}
category: {category}
tags:
{tags_yaml}
type: session
memory_type: {memory_type}
importance: {importance}
cwd: "{cwd}"{tool_frontmatter}
---

# {title}
{memory_callout}
**Summary**: {summary}

**Category**: {category}

**Tags**: {tags_inline}

**Key Concepts**: {concepts_str}

**Project**: `{cwd}`
{activity_section}{see_also_lines}
## Conversation

> [!question] Prompt
> {prompt.replace(chr(10), chr(10) + '> ')}

> [!quote] Response
> {response.replace(chr(10), chr(10) + '> ')}
"""
    session_file.write_text(content)
    return session_file


# ── Step 5: Daily entry (index line) ──


def build_daily_entry(qa: dict) -> str:
    """Build a one-line index entry linking to the session note."""
    qwen = qa.get("qwen_result", {})
    tags = qwen.get("tags", [])
    category = qwen.get("category", "other")

    ts = qa.get("timestamp", "")
    try:
        time_str = datetime.fromisoformat(ts).strftime("%H:%M")
    except (ValueError, TypeError):
        time_str = "00:00"

    fname = session_filename(qa)
    tags_preview = " ".join(f"#{t}" for t in tags[:3]) if tags else ""

    return f"- [{time_str}] [[{fname}]] — {category}, {tags_preview}"


# ── Step 6: Daily note with "Today's Concepts" ──


def write_daily_note(
    date_str: str, entries: list[str], day_concepts: list[str] | None = None
) -> Path:
    """Write or append to a daily note (index format)."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily_file = DAILY_DIR / f"{date_str}.md"

    entries_block = "\n".join(entries)

    concepts_section = ""
    if day_concepts:
        unique = sorted(set(day_concepts))
        concepts_links = ", ".join(f"[[{c}]]" for c in unique)
        concepts_section = f"\n## Today's Concepts\n\n{concepts_links}\n"

    if daily_file.exists():
        existing = daily_file.read_text()
        # Append new entries before Today's Concepts or at the end
        if "## Today's Concepts" in existing:
            existing = re.sub(
                r"## Today's Concepts\n\n.*",
                "",
                existing,
                flags=re.DOTALL,
            )
        new_content = existing.rstrip() + "\n" + entries_block + "\n" + concepts_section
        daily_file.write_text(new_content)
    else:
        frontmatter = f"""---
title: {date_str} AI Conversation Log
date: {date_str}
tags:
  - ai-log
  - knowledge-graph
type: daily-ai-log
---

# {date_str} AI Conversation Log

"""
        daily_file.write_text(frontmatter + entries_block + "\n" + concepts_section)

    return daily_file


# ── Step 7: Concept relations with metadata ──


def build_concept_relations(
    concept_refs: dict[str, list[dict]],
) -> dict[str, dict[str, dict]]:
    """Build concept-to-concept relations with co-occurrence counts and shared tags.

    Returns {concept: {related_concept: {"co_occurred": int, "shared_tags": set}}}.
    """
    cooccurrence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # Co-occurrence: concepts appearing in the same Q&A pair
    seen_pairs: dict[str, list[str]] = defaultdict(list)
    for concept, refs in concept_refs.items():
        for ref in refs:
            pair_id = f"{ref.get('session_id', '')}_{ref.get('timestamp', '')}"
            seen_pairs[pair_id].append(concept)

    for pair_id, concepts in seen_pairs.items():
        for i, c1 in enumerate(concepts):
            for c2 in concepts[i + 1 :]:
                cooccurrence[c1][c2] += 1
                cooccurrence[c2][c1] += 1

    # Collect tags per concept
    concept_tags: dict[str, set[str]] = {}
    for concept, refs in concept_refs.items():
        tags: set[str] = set()
        for ref in refs:
            qwen = ref.get("qwen_result", {})
            tags.update(qwen.get("tags", []))
        concept_tags[concept] = tags

    # Build final relations dict with metadata
    all_concepts = list(concept_refs.keys())
    relations: dict[str, dict[str, dict]] = defaultdict(dict)

    for i, c1 in enumerate(all_concepts):
        for c2 in all_concepts[i + 1 :]:
            co_count = cooccurrence.get(c1, {}).get(c2, 0)
            shared = concept_tags.get(c1, set()) & concept_tags.get(c2, set())

            # Include if co-occurred or share 2+ tags
            if co_count > 0 or len(shared) >= 2:
                meta = {"co_occurred": co_count, "shared_tags": shared}
                relations[c1][c2] = meta
                relations[c2][c1] = meta

    return dict(relations)


# ── Step 8: Concept note with grouped refs and annotated relations ──


def write_concept_note(
    concept: str,
    references: list[dict],
    related_concepts: dict[str, dict] | None = None,
) -> Path:
    """Create or update a concept note with category-grouped refs and annotated relations."""
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(concept)
    concept_file = CONCEPTS_DIR / f"{safe_name}.md"

    # Group references by category, link to session notes
    by_category: dict[str, list[str]] = defaultdict(list)
    for ref in references:
        qwen = ref.get("qwen_result", {})
        cat = qwen.get("category", "other")
        fname = session_filename(ref)
        link = f"- [[{fname}]]"
        by_category[cat].append(link)

    ref_section_parts = []
    for cat in sorted(by_category.keys()):
        ref_section_parts.append(f"### {cat}\n\n" + "\n".join(by_category[cat]))
    ref_section = "\n\n".join(ref_section_parts)

    # Build annotated related concepts section with relationship types
    related_lines = ""
    if related_concepts:
        sorted_related = sorted(
            ((c, meta) for c, meta in related_concepts.items() if c != concept),
            key=lambda x: x[0],
        )
        if sorted_related:
            lines = []
            for c, meta in sorted_related:
                parts = []
                rel_type = meta.get("relationship_type", "co-occurrence")
                if rel_type != "co-occurrence":
                    parts.append(rel_type)
                co = meta.get("co_occurred", 0)
                if co > 0:
                    parts.append(f"co-occurred {co} time{'s' if co != 1 else ''}")
                shared = meta.get("shared_tags", set())
                if shared:
                    parts.append(f"shared tags: {', '.join(sorted(shared))}")
                annotation = " — " + ", ".join(parts) if parts else ""
                lines.append(f"- [[{c}]]{annotation}")
            related_lines = "\n## Related Concepts\n\n" + "\n".join(lines) + "\n"

    # Build version history section
    version_history_lines = ""
    versions = build_version_history(concept, references)
    if versions:
        vh_lines = []
        for v in versions:
            fname = session_filename(v["qa"])
            label = "latest" if v["is_latest"] else ""
            summary_text = truncate(v["summary"], 80)
            if label:
                vh_lines.append(f"- v{v['version']} (latest): [[{fname}]] — \"{summary_text}\"")
            else:
                vh_lines.append(f"- v{v['version']}: [[{fname}]] — \"{summary_text}\"")
        version_history_lines = "\n## Version History\n\n" + "\n".join(vh_lines) + "\n"

    ref_count = sum(len(v) for v in by_category.values())

    if concept_file.exists():
        existing = concept_file.read_text()
        created_match = re.search(r"created: (\S+)", existing)
        created = created_match.group(1) if created_match else datetime.now().strftime("%Y-%m-%d")
    else:
        created = datetime.now().strftime("%Y-%m-%d")

    all_tags: set[str] = set()
    for ref in references:
        qwen = ref.get("qwen_result", {})
        all_tags.update(qwen.get("tags", []))

    tags_yaml = "\n".join(f"  - {t}" for t in sorted(all_tags)) if all_tags else "  - concept"

    # Determine aggregate memory type (majority vote) and average importance
    static_count = sum(1 for r in references if r.get("qwen_result", {}).get("memory_type") == "static")
    dynamic_count = len(references) - static_count
    concept_memory_type = "static" if static_count >= dynamic_count else "dynamic"
    importances = [r.get("qwen_result", {}).get("importance", 3) for r in references]
    avg_importance = round(sum(importances) / len(importances)) if importances else 3

    # Version info
    is_latest = True
    version = versions[0]["version"] if versions else 1
    version_frontmatter = f"\nis_latest: {str(is_latest).lower()}\nversion: {version}"

    content = f"""---
title: {concept}
tags:
{tags_yaml}
  - concept
type: concept
memory_type: {concept_memory_type}
importance: {avg_importance}{version_frontmatter}
created: {created}
references: {ref_count}
---

# {concept}
{version_history_lines}
## Referenced Conversations

{ref_section}
{related_lines}"""
    concept_file.write_text(content)
    return concept_file


# ── Step 10: MOC with Recent Sessions ──


def update_moc(
    daily_files: list[str],
    concept_files: list[str],
    session_files: list[str] | None = None,
) -> None:
    """Update the Map of Content note with daily, concept, and session links."""
    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    existing_dailies: set[str] = set()
    existing_concepts: set[str] = set()
    existing_sessions: set[str] = set()

    if MOC_PATH.exists():
        existing = MOC_PATH.read_text()
        for match in re.finditer(r"\[\[daily/([^\]]+)\]\]", existing):
            existing_dailies.add(match.group(1))
        for match in re.finditer(r"\[\[concepts/([^\]]+)\]\]", existing):
            existing_concepts.add(match.group(1))
        for match in re.finditer(r"\[\[sessions/([^\]]+)\]\]", existing):
            existing_sessions.add(match.group(1))

    for d in daily_files:
        existing_dailies.add(d)
    for c in concept_files:
        existing_concepts.add(c)
    if session_files:
        for s in session_files:
            existing_sessions.add(s)

    sorted_dailies = sorted(existing_dailies, reverse=True)
    sorted_concepts = sorted(existing_concepts)
    sorted_sessions = sorted(existing_sessions, reverse=True)[:20]

    daily_links = "\n".join(f"- [[daily/{d}]]" for d in sorted_dailies)
    concept_links = "\n".join(f"- [[concepts/{c}]]" for c in sorted_concepts)
    session_links = "\n".join(f"- [[sessions/{s}]]" for s in sorted_sessions)

    content = f"""---
title: Knowledge Graph - Map of Content
updated: {today}
tags:
  - MOC
  - knowledge-graph
type: moc
---

# Knowledge Graph

Map of Content for the AI conversation knowledge graph.

## Recent Sessions

{session_links}

## Daily Logs

{daily_links}

## Concepts

{concept_links}
"""
    MOC_PATH.write_text(content)


# ── User Profile ──


def write_user_profile(all_qa: list[dict], concept_refs: dict[str, list[dict]]) -> Path | None:
    """Generate _Profile.md from accumulated knowledge.

    Aggregates static memories (importance >= 4) for core skills,
    recent dynamic memories for current activity, and top concepts by frequency.
    """
    if not PROFILE_PATH or PROFILE_PATH == Path(""):
        return None

    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    # Collect static high-importance items
    static_items: list[dict] = []
    dynamic_recent: list[dict] = []
    now = datetime.now()

    for qa in all_qa:
        qwen = qa.get("qwen_result", {})
        memory_type = qwen.get("memory_type", "dynamic")
        importance = qwen.get("importance", 3)

        if memory_type == "static" and importance >= 4:
            static_items.append(qa)

        if memory_type == "dynamic":
            ts = qa.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                if (now - dt).days <= 7:
                    dynamic_recent.append(qa)
            except (ValueError, TypeError):
                pass

    # Also load previously written QAs for a fuller profile
    for filepath in PROCESSED_DIR.glob("*.json"):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception):
            continue
        if qa.get("status") != "written":
            continue
        qwen = qa.get("qwen_result", {})
        memory_type = qwen.get("memory_type", "dynamic")
        importance = qwen.get("importance", 3)

        if memory_type == "static" and importance >= 4:
            # Avoid duplicates by session_id
            if not any(s.get("session_id") == qa.get("session_id") for s in static_items):
                static_items.append(qa)

        if memory_type == "dynamic":
            ts = qa.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                if (now - dt).days <= 7:
                    if not any(d.get("session_id") == qa.get("session_id") for d in dynamic_recent):
                        dynamic_recent.append(qa)
            except (ValueError, TypeError):
                pass

    # Extract tags and concepts from static items for skill inference
    static_tags: dict[str, int] = defaultdict(int)
    static_concepts: dict[str, int] = defaultdict(int)
    cwds: dict[str, int] = defaultdict(int)

    for qa in static_items:
        qwen = qa.get("qwen_result", {})
        for tag in qwen.get("tags", []):
            static_tags[tag] += 1
        for concept in qwen.get("key_concepts", []):
            static_concepts[concept] += 1

    # Static profile section
    top_tags = sorted(static_tags.items(), key=lambda x: -x[1])[:15]
    static_section = ""
    if top_tags:
        tag_lines = "\n".join(f"- `{tag}` ({count})" for tag, count in top_tags)
        static_section = f"## Core Skills & Preferences (Static)\n\n{tag_lines}\n"
    else:
        static_section = "## Core Skills & Preferences (Static)\n\n_Not enough static memories yet._\n"

    # Dynamic recent activity section
    dynamic_section = ""
    if dynamic_recent:
        # Group by project
        for qa in dynamic_recent:
            cwd = qa.get("cwd", "").replace("\\", "/")
            if cwd:
                cwds[cwd] += 1

        recent_projects = sorted(cwds.items(), key=lambda x: -x[1])[:5]
        project_lines = "\n".join(f"- `{p}` ({c} sessions)" for p, c in recent_projects) if recent_projects else ""

        recent_topics = []
        for qa in sorted(dynamic_recent, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]:
            qwen = qa.get("qwen_result", {})
            title = qwen.get("title", "Untitled")
            fname = session_filename(qa)
            recent_topics.append(f"- [[{fname}|{title}]]")
        topics_str = "\n".join(recent_topics)

        dynamic_section = f"## Recent Activity (Dynamic, last 7 days)\n\n"
        if project_lines:
            dynamic_section += f"### Active Projects\n\n{project_lines}\n\n"
        dynamic_section += f"### Recent Sessions\n\n{topics_str}\n"
    else:
        dynamic_section = "## Recent Activity (Dynamic)\n\n_No recent activity._\n"

    # Top concepts by frequency (across all data)
    all_concept_counts: dict[str, int] = {}
    for concept, refs in concept_refs.items():
        all_concept_counts[concept] = len(refs)
    # Also count from previously written
    for filepath in PROCESSED_DIR.glob("*.json"):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception):
            continue
        for concept in qa.get("qwen_result", {}).get("key_concepts", []):
            if concept not in all_concept_counts:
                all_concept_counts[concept] = 0
            # Don't double-count; concept_refs already has current batch

    top_concepts = sorted(all_concept_counts.items(), key=lambda x: -x[1])[:15]
    concepts_lines = "\n".join(
        f"{i+1}. [[{c}]] ({count} session{'s' if count != 1 else ''})"
        for i, (c, count) in enumerate(top_concepts)
    )
    concepts_section = f"## Top Concepts (by frequency)\n\n{concepts_lines}\n" if top_concepts else ""

    content = f"""---
title: Developer Profile
type: profile
updated: {today}
static_memories: {len(static_items)}
recent_sessions: {len(dynamic_recent)}
---

# Developer Profile

{static_section}
{dynamic_section}
{concepts_section}"""

    PROFILE_PATH.write_text(content)
    return PROFILE_PATH


# ── Step 9: Main orchestration ──


def main() -> None:
    if not KNOWLEDGE_GRAPH_DIR or KNOWLEDGE_GRAPH_DIR == Path(""):
        print("Error: Vault directory not configured. Run 'ckg init --vault-dir <path>' first.")
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log("=" * 50)
    log("Obsidian Writer started")

    files = get_processed_files()
    if not files:
        log("No processed files to write, exiting")
        return

    log(f"Found {len(files)} processed file(s)")

    daily_entries: dict[str, list[str]] = {}
    daily_concepts: dict[str, list[str]] = {}
    concept_refs: dict[str, list[dict]] = {}
    written_dailies: list[str] = []
    written_concepts: list[str] = []
    written_sessions: list[str] = []
    all_qa: list[dict] = []

    for filepath in files:
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception) as e:
            log(f"Failed to read {filepath.name}: {e}")
            continue

        if qa.get("status") != "processed":
            continue

        all_qa.append(qa)

        ts = qa.get("timestamp", "")
        try:
            date_str = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_str = datetime.now().strftime("%Y-%m-%d")

        entry = build_daily_entry(qa)
        daily_entries.setdefault(date_str, []).append(entry)

        qwen = qa.get("qwen_result", {})
        for concept in qwen.get("key_concepts", []):
            concept_refs.setdefault(concept, []).append(qa)
            daily_concepts.setdefault(date_str, []).append(concept)

        qa["status"] = "written"
        qa["written_at"] = datetime.now().isoformat()
        filepath.write_text(json.dumps(qa, ensure_ascii=False, indent=2))

    # Build session-to-session similarity relations
    session_rels = build_session_relations(all_qa)
    total_see_also = sum(len(v) for v in session_rels.values())
    log(f"Built session relations: {total_see_also} 'See Also' links")

    # Write individual session notes
    for qa in all_qa:
        fname = session_filename(qa)
        see_also = session_rels.get(fname, [])
        session_path = write_session_note(qa, see_also)
        written_sessions.append(fname)
        log(f"Wrote session note: {session_path.name}")

    # Write daily notes (index format)
    for date_str, entries in daily_entries.items():
        day_concepts = daily_concepts.get(date_str)
        daily_path = write_daily_note(date_str, entries, day_concepts)
        written_dailies.append(date_str)
        log(f"Wrote daily note: {daily_path.name}")

    # Build concept relations with typed relationships
    relations = classify_concept_relations(concept_refs)
    total_relations = sum(len(v) for v in relations.values()) // 2
    log(f"Built concept relations: {total_relations} unique pairs")

    # Write concept notes
    for concept, refs in concept_refs.items():
        related = relations.get(concept)
        concept_path = write_concept_note(concept, refs, related)
        written_concepts.append(sanitize_filename(concept))
        log(f"Wrote concept note: {concept_path.name}")

    # Update MOC
    if written_dailies or written_concepts or written_sessions:
        update_moc(written_dailies, written_concepts, written_sessions)
        log("Updated _MOC.md")

    # Generate user profile
    try:
        profile_path = write_user_profile(all_qa, concept_refs)
        if profile_path:
            log(f"Updated profile: {profile_path.name}")
    except Exception as e:
        log(f"Profile generation failed: {e}")

    log(
        f"Done: {len(written_sessions)} session notes, "
        f"{len(written_dailies)} daily notes, "
        f"{len(written_concepts)} concept notes"
    )
    log("Obsidian Writer finished")


if __name__ == "__main__":
    main()
