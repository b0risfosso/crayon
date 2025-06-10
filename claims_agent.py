"""claims_agent.py ────────────────────────────────────────────────────────
Batch agent to (a) create new Claim nodes for every Source in a topic
or (b) top‑up each Source to a desired maximum count of claims.

Usage
-----
    # add 3 new claims to *every* source under a topic
    python claims_agent.py --topic "heart morphogenesis" --mode add --n 3

    # ensure every source in the graph has *at least* 5 claims
    python claims_agent.py --mode max --n 5

Dependencies
------------
• Relies on existing `claim_extractor.extract_claims()`
• Uses `loader.load_claims()` and the shared Neo4j `driver`
"""

import argparse
from typing import List

from neo4j import GraphDatabase
from neo4j.graph import Node
from pydantic import ValidationError

from loader import driver, load_claims
from claim_extractor import extract_claims
from models import Source, Claim  # your Pydantic classes

# ── helpers ───────────────────────────────────────────────────────────────

def node_to_source(node: Node) -> Source:
    return Source(**dict(node))


def fetch_sources(topic: str | None) -> List[Source]:
    cypher = (
        "MATCH (s:Source) "
        "WHERE $topic IS NULL OR s.topic = $topic "
        "RETURN s"
    )
    with driver.session() as sess:
        nodes = [rec["s"] for rec in sess.run(cypher, topic=topic)]
    out: List[Source] = []
    for n in nodes:
        try:
            out.append(node_to_source(n))
        except ValidationError as e:
            print("⚠️  Skipping malformed Source:", e)
    return out


def count_claims(source_id: str) -> int:
    cypher = "MATCH (:Source {id:$sid})-[:SUPPORTS]->(c:Claim) RETURN count(c) AS cnt"
    with driver.session() as sess:
        return sess.run(cypher, sid=source_id).single()["cnt"]

# ── main routine ──────────────────────────────────────────────────────────

def run(topic: str | None, mode: str, n: int):
    sources = fetch_sources(topic)
    if not sources:
        print("No sources found.")
        return

    total_new = 0
    for src in sources:
        existing = count_claims(src.id)

        if mode == "add":
            k = n
        elif mode == "max":
            k = max(0, n - existing)
        else:
            raise ValueError("mode must be 'add' or 'max'")

        if k == 0:
            print(f"↷ {src.title[:60]}… already has ≥{n} claims")
            continue

        new_claims: List[Claim] = extract_claims(src, k=k)
        load_claims(new_claims)
        total_new += len(new_claims)
        print(f"✅ Added {len(new_claims)} claim(s) to '{src.title[:40]}…'")

    print(f"🎉 Done. {total_new} new claims written to graph.")

# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Add or top-up Claim nodes across Sources")
    ap.add_argument("--topic", help="Topic name to restrict to", default=None)
    ap.add_argument("--mode", choices=["add", "max"], required=True,
                    help="'add' = always add N claims; 'max' = top-up to N claims")
    ap.add_argument("--n", type=int, required=True, help="Number of claims to add / reach")
    args = ap.parse_args()

    run(args.topic, args.mode, args.n)
