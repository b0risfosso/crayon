"""
Streamlit dashboard MVP for the Engineering Knowledge‑Graph
-----------------------------------------------------------
Run with:
    streamlit run app.py

Required env variables (add to .env):
    NEO4J_URI=bolt://localhost:7687
    NEO4J_USER=neo4j
    NEO4J_PWD=test

Install deps:
    pip install streamlit neo4j python-dotenv pandas networkx pyvis
"""

import os
import tempfile
from typing import Any

import networkx as nx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from neo4j import GraphDatabase
from pyvis.network import Network

# -------------------------------------------------------------
# 1. Neo4j connection helper (cached so it only opens once)
# -------------------------------------------------------------
load_dotenv()

@st.cache_resource(show_spinner=False)
def get_driver():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    pwd = os.getenv("NEO4J_PWD", "testtesttest")
    return GraphDatabase.driver(uri, auth=(user, pwd))

driver = get_driver()

# -------------------------------------------------------------
# 2. Cypher convenience
# -------------------------------------------------------------

def run_cypher(query: str, **kwargs):
    """Return list[dict] where values can be Neo4j Node objects or plain dicts."""
    with driver.session() as sess:
        return [rec.data() for rec in sess.run(query, **kwargs)]

# -------------------------------------------------------------
# 3. Helpers for mixed Node/dict representations
# -------------------------------------------------------------

def node_to_dict(node: Any) -> dict:
    """Return a uniform dict of node properties (works for neo4j.graph.Node or plain dict)."""
    if node is None:
        return {}
    if hasattr(node, "_properties"):
        # neo4j Node
        return dict(node)
    if isinstance(node, dict):
        return node
    return {}


def node_uid(node: Any) -> str:
    """Return stable unique id for graph node, falling back to property hash."""
    if node is None:
        return "unknown"
    if hasattr(node, "id"):
        return str(node.id)
    props = node_to_dict(node)
    # Try common keys, else hash of props
    for k in ("id", "name", "title", "principle", "description"):
        if k in props:
            return props[k]
    return str(hash(frozenset(props.items())))


def node_label(node: Any, alias: str) -> str:
    props = node_to_dict(node)
    return props.get("name") or props.get("title") or props.get("principle") or props.get("description") or alias.upper()

# -------------------------------------------------------------
# 4. UI helpers
# -------------------------------------------------------------

def fetch_topics():
    rows = run_cypher("MATCH (t:Topic) RETURN t.name AS topic ORDER BY topic")
    return [r["topic"] for r in rows]


def fetch_graph_neighbourhood(topic: str):
    query = """
    MATCH (t:Topic {name:$topic})<-[:ABOUT]-(s:Source)
    OPTIONAL MATCH (s)-[:DESCRIBES]->(m:Mechanism)
    OPTIONAL MATCH (m)-[:ENABLES]->(a:Artifact)
    OPTIONAL MATCH (a)-[:HAS_RISK]->(r:Risk)-[:MITIGATED_BY]->(c:Control)
    RETURN t, s, m, a, r, c
    """
    return run_cypher(query, topic=topic)


def build_networkx(records):
    G = nx.DiGraph()
    for rec in records:
        for alias in ("t", "s", "m", "a", "r", "c"):
            node = rec.get(alias)
            if node:
                nid = node_uid(node)
                G.add_node(nid, label=alias.upper(), title=node_label(node, alias))

        # Add directed edges if both endpoints exist
        pairs = [("s", "m"), ("m", "a"), ("a", "r"), ("r", "c"), ("s", "t")]
        for u_alias, v_alias in pairs:
            u = rec.get(u_alias)
            v = rec.get(v_alias)
            if u and v:
                G.add_edge(node_uid(u), node_uid(v))
    return G


def render_pyvis(G: nx.DiGraph):
    """Render PyVis graph to a temporary HTML file and return its path.
    Using `write_html` avoids the Jinja template bug that sometimes
    appears when `net.show()` tries to auto‑render in notebook mode.
    """
    net = Network(height="500px", width="100%", directed=True, notebook=False)
    for nid, data in G.nodes(data=True):
        net.add_node(
            nid,
            label=data.get("title", nid),
            title=f"{data.get('label','NODE')}: {data.get('title','')}",
        )
    for u, v in G.edges():
        net.add_edge(u, v)

    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    net.write_html(tmp_file.name)
    return tmp_file.name

# -------------------------------------------------------------
# 5. Streamlit layout
# -------------------------------------------------------------

st.set_page_config(page_title="Engineering KG Dashboard", layout="wide")

st.sidebar.title("Knowledge‑Graph Explorer")

topic_list = fetch_topics()
if not topic_list:
    st.sidebar.warning("No topics in graph yet – run agent first.")
    st.stop()

sel_topic = st.sidebar.selectbox("Choose a topic", topic_list)

if st.sidebar.button("🔄 Refresh agent for topic"):
    with st.spinner("Running agent, this may take a minute …"):
        os.system(f"python agent.py '{sel_topic}'")
    st.experimental_rerun()

st.header(f"Topic: {sel_topic}")


if st.button("Plot KPI history"):
    data = run_cypher(
        """MATCH (:Artifact {id:$aid})-[:MEASURED_BY]->(k:Metric)<-[:DATA_OF]-(m:Measurement)
            RETURN k.name AS metric, m.timestamp AS ts, m.value AS val
        """, aid=selected_artifact_id)
    df = pd.DataFrame(data)
    for met in df.metric.unique():
        sub = df[df.metric == met].sort_values("ts")
        plt.figure()
        plt.plot(pd.to_datetime(sub.ts, unit='ms'), sub.val)
        plt.title(met)
        st.pyplot(plt.gcf())


# --- Fetch & visualise graph ---
records = fetch_graph_neighbourhood(sel_topic)
if records:
    G = build_networkx(records)
    html_path = render_pyvis(G)
    with open(html_path, "r", encoding="utf-8") as f:
        st.components.v1.html(f.read(), height=520, scrolling=True)
else:
    st.info("No data for this topic yet. Run the agent!")

# --- Data tables (convert all nodes to dict first) ---

def serialize_val(v):
    """Return hashable / display‑friendly value (str for lists, dicts)."""
    if isinstance(v, (list, dict, set)):
        return repr(v)
    return v

def props_dict(node):
    d = node_to_dict(node)
    return {k: serialize_val(v) for k, v in d.items()}

sources_df = pd.DataFrame([
    {"Source ID": node_uid(s := rec.get("s")), **props_dict(s)}
    for rec in records if rec.get("s")
]).drop_duplicates(subset=["Source ID"])

mech_df = pd.DataFrame([
    {"Mech ID": node_uid(m := rec.get("m")), **props_dict(m)}
    for rec in records if rec.get("m")
]).drop_duplicates(subset=["Mech ID"])

art_df = pd.DataFrame([
    {"Artifact ID": node_uid(a := rec.get("a")), **props_dict(a)}
    for rec in records if rec.get("a")
]).drop_duplicates(subset=["Artifact ID"])

with st.expander("Sources"):
    st.dataframe(sources_df, use_container_width=True)

with st.expander("Mechanisms"):
    st.dataframe(mech_df, use_container_width=True)

with st.expander("Artifacts"):
    st.dataframe(art_df, use_container_width=True)


# Optional: filter by stale flag
st.sidebar.markdown("---")
show_stale = st.sidebar.checkbox("Show stale nodes", value=False, key="stale_chk")
if show_stale:
    st.sidebar.write("Nodes marked stale are still in graph; they may be outdated.")
