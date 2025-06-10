"""mechanisms_agent.py ────────────────────────────────────────────────
Batch agent to create Mechanism nodes per Source with two modes:

* **add  N** – indiscriminately add *N* new mechanisms to every source
* **max  N** – for each source, ensure it has at least *N* mechanisms;
               add just enough to reach that count

The agent can be scoped to a single Topic or run across the whole graph.

Examples
--------
    # add 2 mechanisms to every source under a topic
    python mechanisms_agent.py --topic "heart morphogenesis" --mode add --n 2

    # ensure every source in the DB has ≥4 mechanisms
    python mechanisms_agent.py --mode max --n 4

Prerequisites
-------------
* `extract_mechanisms(src, k)` function available (Tier‑1 extractor)
* `load_mechanisms(mechs)` loader that merges Mechanism → Neo4j
"""

import argparse
from typing import List

from neo4j import GraphDatabase
from neo4j.graph import Node
from pydantic import ValidationError

from loader import driver, load_mechanisms
from mechanism_extractor import extract_mechanisms
from models import Source, Mechanism  # Pydantic models

# ── helpers ─────────────────────────────────────────────────────────────

def node_to_source(node: Node) -> Source:
    return Source(**dict(node))


def fetch_sources(topic: str | None) -> List[Source]:
    q = (
        "MATCH (s:Source) "
        "WHERE $topic IS NULL OR s.topic = $topic "
        "RETURN s"
    )
    with driver.session() as sess:
        nodes = [rec["s"] for rec in sess.run(q, topic=topic)]
    out: List[Source] = []
    for n in nodes:
        try:
            out.append(node_to_source(n))
        except ValidationError as e:
            print("⚠️  Skipping malformed Source:", e)
    return out


def count_mechanisms(sid: str) -> int:
    cypher = "MATCH (:Source {id:$sid})-[:DESCRIBES]->(m:Mechanism) RETURN count(m) AS c"
    with driver.session() as sess:
        return sess.run(cypher, sid=sid).single()["c"]

# ── main routine ───────────────────────────────────────────────────────–

def run(topic: str | None, mode: str, n: int):
    sources = fetch_sources(topic)
    if not sources:
        print("No sources found.")
        return

    total_new = 0
    for src in sources:
        existing = count_mechanisms(src.id)
        if mode == "add":
            k = n
        elif mode == "max":
            k = max(0, n - existing)
        else:
            raise ValueError("mode must be 'add' or 'max'")

        if k == 0:
            print(f"↷ '{src.title[:60]}…' already has ≥{n} mechanisms")
            continue

        new_mechs: List[Mechanism] = extract_mechanisms(src, max_mechs=k)
        load_mechanisms(new_mechs)
        total_new += len(new_mechs)
        print(f"✅ Added {len(new_mechs)} mechanism(s) to '{src.title[:40]}…'")

    print(f"🎉 Done. {total_new} new mechanisms written to graph.")

# ── CLI ─────────────────────────────────────────────────────────────—––

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Add/top‑up Mechanisms per Source")
    ap.add_argument("--topic", default=None, help="Restrict to topic name")
    ap.add_argument("--mode", choices=["add", "max"], required=True)
    ap.add_argument("--n", type=int, required=True, help="Number of mechanisms")
    args = ap.parse_args()

    run(args.topic, args.mode, args.n)
