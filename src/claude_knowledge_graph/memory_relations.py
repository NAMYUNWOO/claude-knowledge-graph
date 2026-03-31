#!/usr/bin/env python3
"""Rule-based relationship detection for knowledge graph concepts.

Detects three relationship types (no extra LLM calls):
- Updates: new info supersedes old (same concept, contradictory/newer info)
- Extends: new info enriches existing (same concept, additive info)
- Derives: inferred connection from co-occurrence patterns
"""

import re
from collections import defaultdict

# Keywords that indicate supersession/update
UPDATE_KEYWORDS_EN = {
    "now", "instead", "changed to", "migrated from", "migrated to",
    "replaced", "switched to", "upgraded", "deprecated", "no longer",
    "previously", "used to", "was using", "moved to", "converted to",
}
UPDATE_KEYWORDS_KO = {
    "업데이트", "변경", "대체", "전환", "마이그레이션", "더 이상",
    "이전에", "바꿨", "바꾸", "교체",
}
ALL_UPDATE_KEYWORDS = UPDATE_KEYWORDS_EN | UPDATE_KEYWORDS_KO


def _text_of(qa: dict) -> str:
    """Get combined text from a Q&A pair for keyword analysis."""
    prompt = qa.get("prompt", "")
    response = qa.get("response", "")
    summary = qa.get("qwen_result", {}).get("summary", "")
    return f"{prompt} {response} {summary}".lower()


def _shared_concepts(qa1: dict, qa2: dict) -> set[str]:
    c1 = set(qa1.get("qwen_result", {}).get("key_concepts", []))
    c2 = set(qa2.get("qwen_result", {}).get("key_concepts", []))
    return c1 & c2


def _shared_tags(qa1: dict, qa2: dict) -> set[str]:
    t1 = set(qa1.get("qwen_result", {}).get("tags", []))
    t2 = set(qa2.get("qwen_result", {}).get("tags", []))
    return t1 & t2


def _has_update_keywords(qa: dict) -> bool:
    text = _text_of(qa)
    return any(kw in text for kw in ALL_UPDATE_KEYWORDS)


def _get_timestamp(qa: dict) -> str:
    return qa.get("timestamp", "")


def detect_updates(
    new_qa: dict, existing_qas: list[dict]
) -> list[dict]:
    """Detect 'Updates' relationships: new Q&A supersedes older ones.

    Criteria:
    - Share 1+ key concepts
    - Share 3+ tags AND same category
    - New Q&A contains update keywords
    - New Q&A is chronologically later

    Returns list of {target_qa, shared_concepts, reason}.
    """
    results = []
    new_qwen = new_qa.get("qwen_result", {})
    new_cat = new_qwen.get("category", "")
    new_ts = _get_timestamp(new_qa)

    if not _has_update_keywords(new_qa):
        return results

    for existing in existing_qas:
        existing_qwen = existing.get("qwen_result", {})
        existing_cat = existing_qwen.get("category", "")
        existing_ts = _get_timestamp(existing)

        # Must be chronologically later
        if new_ts <= existing_ts:
            continue

        shared_c = _shared_concepts(new_qa, existing)
        shared_t = _shared_tags(new_qa, existing)

        if shared_c and len(shared_t) >= 3 and new_cat == existing_cat:
            results.append({
                "target_qa": existing,
                "shared_concepts": shared_c,
                "shared_tags": shared_t,
                "reason": "updates",
            })

    return results


def detect_extends(
    new_qa: dict, existing_qas: list[dict]
) -> list[dict]:
    """Detect 'Extends' relationships: new Q&A enriches existing concepts.

    Criteria:
    - Share 1+ key concepts
    - Share 2+ tags
    - NOT an update (no update keywords)
    - New Q&A is chronologically later

    Returns list of {target_qa, shared_concepts, reason}.
    """
    results = []
    new_ts = _get_timestamp(new_qa)

    # If it has update keywords, it's an update, not an extension
    if _has_update_keywords(new_qa):
        return results

    for existing in existing_qas:
        existing_ts = _get_timestamp(existing)

        if new_ts <= existing_ts:
            continue

        shared_c = _shared_concepts(new_qa, existing)
        shared_t = _shared_tags(new_qa, existing)

        if shared_c and len(shared_t) >= 2:
            results.append({
                "target_qa": existing,
                "shared_concepts": shared_c,
                "shared_tags": shared_t,
                "reason": "extends",
            })

    return results


def detect_derives(
    concept_refs: dict[str, list[dict]],
    min_cooccurrence: int = 3,
) -> dict[str, dict[str, dict]]:
    """Detect 'Derives' relationships from co-occurrence patterns.

    If two concepts always appear together (>= min_cooccurrence times),
    they have a derived relationship — an inferred connection.

    Returns {concept: {related_concept: {"co_occurred": N, "reason": "derives"}}}.
    """
    # Count co-occurrences per session
    session_concepts: dict[str, list[str]] = defaultdict(list)
    for concept, refs in concept_refs.items():
        for ref in refs:
            pair_id = f"{ref.get('session_id', '')}_{ref.get('timestamp', '')}"
            session_concepts[pair_id].append(concept)

    cooccurrence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for pair_id, concepts in session_concepts.items():
        for i, c1 in enumerate(concepts):
            for c2 in concepts[i + 1:]:
                cooccurrence[c1][c2] += 1
                cooccurrence[c2][c1] += 1

    # Filter by minimum co-occurrence threshold
    derives: dict[str, dict[str, dict]] = defaultdict(dict)
    all_concepts = list(concept_refs.keys())
    for i, c1 in enumerate(all_concepts):
        for c2 in all_concepts[i + 1:]:
            count = cooccurrence.get(c1, {}).get(c2, 0)
            if count >= min_cooccurrence:
                meta = {"co_occurred": count, "reason": "derives"}
                derives[c1][c2] = meta
                derives[c2][c1] = meta

    return dict(derives)


def classify_concept_relations(
    concept_refs: dict[str, list[dict]],
) -> dict[str, dict[str, dict]]:
    """Build enriched concept relations with typed relationships.

    Combines co-occurrence, shared tags, and relationship type detection.
    Returns {concept: {related_concept: {co_occurred, shared_tags, relationship_type}}}.
    """
    # First, detect derives (pattern-based)
    derives = detect_derives(concept_refs)

    # Build co-occurrence and shared tag data
    session_concepts: dict[str, list[str]] = defaultdict(list)
    for concept, refs in concept_refs.items():
        for ref in refs:
            pair_id = f"{ref.get('session_id', '')}_{ref.get('timestamp', '')}"
            session_concepts[pair_id].append(concept)

    cooccurrence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for pair_id, concepts in session_concepts.items():
        for i, c1 in enumerate(concepts):
            for c2 in concepts[i + 1:]:
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

    # Detect updates/extends between Q&A pairs sharing concepts
    concept_pair_relations: dict[tuple[str, str], str] = {}
    all_concepts = list(concept_refs.keys())

    for concept, refs in concept_refs.items():
        # Sort refs by timestamp
        sorted_refs = sorted(refs, key=lambda r: r.get("timestamp", ""))
        for i, ref in enumerate(sorted_refs):
            earlier = sorted_refs[:i]
            if not earlier:
                continue

            updates = detect_updates(ref, earlier)
            extends = detect_extends(ref, earlier)

            # Map updates/extends to concept pairs
            for upd in updates:
                target_concepts = set(upd["target_qa"].get("qwen_result", {}).get("key_concepts", []))
                for tc in target_concepts:
                    if tc != concept and tc in concept_refs:
                        key = tuple(sorted([concept, tc]))
                        concept_pair_relations[key] = "updates"

            for ext in extends:
                target_concepts = set(ext["target_qa"].get("qwen_result", {}).get("key_concepts", []))
                for tc in target_concepts:
                    if tc != concept and tc in concept_refs:
                        key = tuple(sorted([concept, tc]))
                        if key not in concept_pair_relations:
                            concept_pair_relations[key] = "extends"

    # Build final relations
    relations: dict[str, dict[str, dict]] = defaultdict(dict)
    for i, c1 in enumerate(all_concepts):
        for c2 in all_concepts[i + 1:]:
            co_count = cooccurrence.get(c1, {}).get(c2, 0)
            shared = concept_tags.get(c1, set()) & concept_tags.get(c2, set())

            if co_count > 0 or len(shared) >= 2:
                # Determine relationship type
                key = tuple(sorted([c1, c2]))
                if key in concept_pair_relations:
                    rel_type = concept_pair_relations[key]
                elif c1 in derives and c2 in derives.get(c1, {}):
                    rel_type = "derives"
                else:
                    rel_type = "co-occurrence"

                meta = {
                    "co_occurred": co_count,
                    "shared_tags": shared,
                    "relationship_type": rel_type,
                }
                relations[c1][c2] = meta
                relations[c2][c1] = meta

    return dict(relations)


def build_version_history(
    concept: str,
    references: list[dict],
) -> list[dict]:
    """Build a version history chain for a concept.

    Looks at references sorted by time, detects updates between consecutive entries.
    Returns a list of version entries (newest first):
    [{version: N, qa: dict, summary: str, is_latest: bool}]
    """
    if len(references) < 2:
        return []

    sorted_refs = sorted(references, key=lambda r: r.get("timestamp", ""))

    versions = []
    version_num = 1

    for i, ref in enumerate(sorted_refs):
        is_update = False
        if i > 0:
            updates = detect_updates(ref, sorted_refs[:i])
            if updates:
                is_update = True
                version_num += 1

        if is_update or i == 0:
            versions.append({
                "version": version_num,
                "qa": ref,
                "summary": ref.get("qwen_result", {}).get("summary", ""),
                "is_latest": False,
            })

    if versions:
        versions[-1]["is_latest"] = True
        versions.reverse()  # Newest first

    return versions
