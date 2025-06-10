# agent.py
from datetime import timedelta, datetime
from typing import List
from scout import arxiv_search
from source_extractor import extract_source
from claim_extractor import extract_claims
from mechanism_extractor import extract_mechanisms
from artifact_generator import generate_artifacts
from risk_control_extractor import extract_risks_controls
from metric_extractor import extract_metrics

from loader import load_sources, load_claims, driver, load_mechanisms, load_artifacts, load_risks_controls, load_metrics     # driver = Neo4j driver



# ── configuration ────────────────────────────────────────────────────────────
MAX_PAPERS      = 5          # per topic per run
MAX_CLAIMS_PER  = 10           # per paper
MAX_MECHS       = 10
MAX_ARTS        = 10
MAX_RISKS       = 10
STALE_DAYS      = 7           # mark stale if unseen for this many days
GRAPH_NAME      = "engineering_knowledge_graph"

# ── main agent entrypoint ────────────────────────────────────────────────────
def agent_run(topic: str) -> None:
    """Scout → Extractor → Loader → Housekeeping for one topic."""
    # 1. Scout
    raw_papers = arxiv_search(topic, max_results=MAX_PAPERS)

    # 2. Source extraction + load
    sources = [extract_source(p) for p in raw_papers]
    load_sources(sources)

    # 3. Claim extraction + load
    all_claims: List = []
    for src in sources:
        claims = extract_claims(src, k=MAX_CLAIMS_PER)
        load_claims(claims)
        all_claims.extend(claims)

    # Mechanism extraction + load
    mechanisms = []
    artifacts_all = []
    for src in sources:
        mechs = extract_mechanisms(src, max_mechs=MAX_MECHS)
        load_mechanisms(mechs)
        mechanisms.extend(mechs)
        mechanisms_by_topic = mechs
        artifacts = generate_artifacts(src.topic, mechanisms_by_topic, max_artifacts=MAX_ARTS)
        load_artifacts(artifacts)
        artifacts_all.extend(artifacts)

    for art in artifacts_all:
        # gather mechanisms for this artifact (IDs stored in principle_chain)
        mechs = [m for m in mechanisms if m.id in art.principle_chain]
        risks, ctrls = extract_risks_controls(art, mechs, max_risks=MAX_RISKS)
        load_risks_controls(risks, ctrls)

    for art in artifacts_all:
        mets = extract_metrics(art)
        load_metrics(mets)

    

    # 4. Mark topic nodes 'last_seen'
    _touch_topic(topic)

    # 5. Housekeeping: mark stale nodes older than STALE_DAYS
    _mark_stale_nodes(STALE_DAYS)

    # 6. Bump GraphMeta timestamp
    _update_graphmeta()

    print(f"✅ {len(sources)} sources, {len(claims)} claims, {len(mechanisms)} mechanisms, {len(artifacts_all)} artifacts processed")
    print(f"✅ {len(risks)} risks & {len(ctrls)} controls loaded for '{topic}'")

# ── helper Cypher operations ─────────────────────────────────────────────────
def _touch_topic(topic: str):
    with driver.session() as sess:
        sess.run("""
            MERGE (t:Topic {name:$topic})
            SET   t.last_seen = timestamp()
        """, topic=topic)

def _mark_stale_nodes(stale_days: int):
    cutoff = int((datetime.utcnow() - timedelta(days=stale_days)).timestamp() * 1000)
    with driver.session() as sess:
        sess.run("""
            MATCH (n) WHERE n.last_seen IS NOT NULL
              AND n.last_seen < $cutoff
              AND (n.stale IS NULL OR n.stale = false)
            SET n.stale = true
        """, cutoff=cutoff)

def _update_graphmeta():
    with driver.session() as sess:
        sess.run("""
            MERGE (g:GraphMeta {name:$name})
            SET   g.last_updated = timestamp()
        """, name=GRAPH_NAME)

# ── CLI ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys, os
    if len(sys.argv) < 2:
        print("Usage: python agent.py \"topic string\"")
        sys.exit(1)

    topic_in = " ".join(sys.argv[1:])
    agent_run(topic_in)