# agent.py
from datetime import timedelta, datetime
from typing import List
from scout import arxiv_search
from source_extractor import extract_source
from claim_extractor import extract_claims
from mechanism_extractor import extract_mechanisms
from loader import load_sources, load_claims, driver, load_mechanisms     # driver = Neo4j driver


# ── configuration ────────────────────────────────────────────────────────────
MAX_PAPERS      = 2          # per topic per run
MAX_CLAIMS_PER  = 2           # per paper
MAX_MECHS       = 2
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
    for src in sources:
        mechs = extract_mechanisms(src, max_mechs=MAX_MECHS)
        load_mechanisms(mechs)
        mechanisms.extend(mechs)

    # 4. Mark topic nodes 'last_seen'
    _touch_topic(topic)

    # 5. Housekeeping: mark stale nodes older than STALE_DAYS
    _mark_stale_nodes(STALE_DAYS)

    # 6. Bump GraphMeta timestamp
    _update_graphmeta()

    print(f"✅ {len(sources)} sources, {len(claims)} claims, {len(mechanisms)} mechanisms processed")

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