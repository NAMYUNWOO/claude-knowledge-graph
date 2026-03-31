---
name: vault-search
description: Search the Claude knowledge graph vault for past Q&A sessions, concepts, and developer knowledge. Use when you need to find information from previous conversations, recall how something was done before, or look up stored technical knowledge. Supports Korean and English queries.
argument-hint: [search query]
allowed-tools: Bash(ckg query *)
user-invocable: true
---

# Vault Search

Search your knowledge graph vault using semantic embeddings.

## Search Query: $ARGUMENTS

!`ckg query --context "$ARGUMENTS" --top-k 5`

## Instructions

Based on the search results above:

1. **Summarize the most relevant findings** — highlight the top matches and explain how they relate to the query
2. **Key concepts** — list the wikilinked concepts that appear across results
3. **Actionable insights** — if the results contain solutions, patterns, or decisions, surface them clearly
4. **Suggest follow-ups** — if the results suggest related topics worth exploring, mention them

If no results are found, suggest alternative search terms or broader queries.
