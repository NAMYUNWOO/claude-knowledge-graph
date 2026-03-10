#!/usr/bin/env python3
"""Generate a knowledge graph visualization image.

Requires: pip install claude-knowledge-graph[graph]
"""

import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import networkx as nx

from claude_knowledge_graph.config import KNOWLEDGE_GRAPH_DIR

DAILY_DIR = KNOWLEDGE_GRAPH_DIR / "daily"
CONCEPTS_DIR = KNOWLEDGE_GRAPH_DIR / "concepts"
SESSIONS_DIR = KNOWLEDGE_GRAPH_DIR / "sessions"
OUTPUT = Path(__file__).parent.parent / "docs" / "knowledge-graph-example.png"

CATEGORY_COLORS = {
    "session": "#6ECB63",
    "daily": "#4A90D9",
    "concept": "#E8A838",
    "moc": "#D94A4A",
}


def find_korean_font():
    """Find a Korean-capable font on the system."""
    for f in fm.fontManager.ttflist:
        if "Nanum" in f.name or "CJK" in f.name:
            return f.name
    return None


def extract_wikilinks(text: str) -> list[str]:
    """Extract [[wikilink]] targets from text."""
    return re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]", text)


def build_graph():
    G = nx.Graph()

    G.add_node("_MOC", type="moc", label="Knowledge\nGraph MOC")

    # Session notes
    if SESSIONS_DIR.exists():
        for f in sorted(SESSIONS_DIR.glob("*.md")):
            name = f.stem
            node_id = f"sessions/{name}"
            G.add_node(node_id, type="session", label=name)

            content = f.read_text()

            # Link to concepts via wikilinks in Key Concepts line
            for line in content.split("\n"):
                if line.startswith("**Key Concepts**:"):
                    for link in extract_wikilinks(line):
                        concept_id = f"concepts/{link}"
                        if not G.has_node(concept_id):
                            G.add_node(concept_id, type="concept", label=link)
                        G.add_edge(node_id, concept_id)
                    break

            # See Also links (session-to-session)
            in_see_also = False
            for line in content.split("\n"):
                if line.startswith("## See Also"):
                    in_see_also = True
                    continue
                if in_see_also and line.startswith("## "):
                    break
                if in_see_also and line.startswith("- [["):
                    linked = re.search(r"\[\[([^\]]+)\]\]", line)
                    if linked:
                        target = f"sessions/{linked.group(1)}"
                        if not G.has_node(target):
                            G.add_node(target, type="session", label=linked.group(1))
                        G.add_edge(node_id, target, relation="see_also")

    # Daily notes
    if DAILY_DIR.exists():
        for f in sorted(DAILY_DIR.glob("*.md")):
            name = f.stem
            node_id = f"daily/{name}"
            G.add_node(node_id, type="daily", label=name)
            G.add_edge("_MOC", node_id)

            content = f.read_text()
            for link in extract_wikilinks(content):
                # Link to session notes
                session_id = f"sessions/{link}"
                if G.has_node(session_id):
                    G.add_edge(node_id, session_id)

    # Concept notes and their cross-links
    if CONCEPTS_DIR.exists():
        for f in sorted(CONCEPTS_DIR.glob("*.md")):
            name = f.stem
            node_id = f"concepts/{name}"
            if not G.has_node(node_id):
                G.add_node(node_id, type="concept", label=name)
            G.add_edge("_MOC", node_id)

            content = f.read_text()
            in_related = False
            for line in content.split("\n"):
                if line.startswith("## Related Concepts"):
                    in_related = True
                    continue
                if in_related and line.startswith("## "):
                    break
                if in_related and line.startswith("- [["):
                    linked = re.search(r"\[\[([^\]]+)\]\]", line)
                    if linked:
                        target = f"concepts/{linked.group(1)}"
                        if not G.has_node(target):
                            G.add_node(target, type="concept", label=linked.group(1))
                        G.add_edge(node_id, target, relation="related")

    return G


def draw_graph(G):
    korean_font = find_korean_font()
    if korean_font:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [korean_font, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(1, 1, figsize=(20, 14), facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    moc_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "moc"]
    session_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "session"]
    daily_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "daily"]
    concept_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "concept"]

    pos = nx.spring_layout(G, k=2.5, iterations=80, seed=42)

    # Edge categories
    see_also_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("relation") == "see_also"]
    related_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("relation") == "related"]
    hub_edges = [(u, v) for u, v, d in G.edges(data=True)
                 if d.get("relation") not in ("see_also", "related")]

    nx.draw_networkx_edges(G, pos, edgelist=hub_edges, alpha=0.08,
                           edge_color="#555577", width=0.3, ax=ax)
    nx.draw_networkx_edges(G, pos, edgelist=related_edges, alpha=0.25,
                           edge_color="#E8A838", width=0.6, ax=ax)
    nx.draw_networkx_edges(G, pos, edgelist=see_also_edges, alpha=0.4,
                           edge_color="#6ECB63", width=1.0, ax=ax)

    nx.draw_networkx_nodes(G, pos, nodelist=concept_nodes,
                           node_color=CATEGORY_COLORS["concept"],
                           node_size=120, alpha=0.8, ax=ax)
    nx.draw_networkx_nodes(G, pos, nodelist=session_nodes,
                           node_color=CATEGORY_COLORS["session"],
                           node_size=200, alpha=0.85, ax=ax)
    nx.draw_networkx_nodes(G, pos, nodelist=daily_nodes,
                           node_color=CATEGORY_COLORS["daily"],
                           node_size=500, alpha=0.9, ax=ax)
    nx.draw_networkx_nodes(G, pos, nodelist=moc_nodes,
                           node_color=CATEGORY_COLORS["moc"],
                           node_size=800, alpha=1.0, ax=ax)

    # Labels for important nodes
    important_labels = {}
    for n in moc_nodes + daily_nodes:
        important_labels[n] = G.nodes[n].get("label", n)

    # Top connected sessions
    session_degrees = [(n, G.degree(n)) for n in session_nodes]
    session_degrees.sort(key=lambda x: x[1], reverse=True)
    for n, deg in session_degrees[:10]:
        important_labels[n] = G.nodes[n].get("label", n)

    # Top connected concepts
    concept_degrees = [(n, G.degree(n)) for n in concept_nodes]
    concept_degrees.sort(key=lambda x: x[1], reverse=True)
    for n, deg in concept_degrees[:20]:
        important_labels[n] = G.nodes[n].get("label", n)

    nx.draw_networkx_labels(G, pos, labels=important_labels,
                            font_size=7, font_color="white",
                            font_weight="bold", ax=ax)

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor=CATEGORY_COLORS["moc"],
               markersize=12, label=f"MOC ({len(moc_nodes)})"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=CATEGORY_COLORS["session"],
               markersize=9, label=f"Sessions ({len(session_nodes)})"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=CATEGORY_COLORS["daily"],
               markersize=10, label=f"Daily Notes ({len(daily_nodes)})"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=CATEGORY_COLORS["concept"],
               markersize=8, label=f"Concepts ({len(concept_nodes)})"),
    ]
    ax.legend(handles=legend_elements, loc="upper left",
              fontsize=11, facecolor="#16213e", edgecolor="#555577",
              labelcolor="white")

    ax.set_title("Knowledge Graph — Auto-captured from Claude Code Sessions",
                 fontsize=16, color="white", pad=20, fontweight="bold")
    ax.axis("off")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    print(f"Saved: {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    G = build_graph()
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    draw_graph(G)
