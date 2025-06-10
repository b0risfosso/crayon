"""artifact_agent.py ──────────────────────────────────────────────────────
Generate `Artifact` nodes from existing `Mechanism` nodes.
Supports two modes analogous to claims/mechanisms agents:

* **add  N** – create *N* new artifacts for each topic (or for a single topic)
* **max  N** – ensure each topic has at least *N* artifacts; only add as many as needed

Examples
--------
    # add 2 artifacts for every topic
    python artifact_agent.py --mode add --n 2

    # top‑up the topic so it has 5 total artifacts
    python artifact_agent.py --topic "heart morphogenesis" --mode max --n 5
"""

import argparse
from collections import defaultdict
from typing import List

from neo4j import GraphDatabase
from neo4j.graph import Node
from pydantic import ValidationError

from loader import driver, load_artifacts
from artifact_generator import generate_artifacts
from models import Mechanism, Artifact  # Pydantic classes

# ── helpers ────────────────────────────────────────────────────────────

def node_to_mech(node: Node) -> Mechanism:
    return Mechanism(**dict(node))


def fetch_mechanisms(topic: str | None) -> List[Mechanism]:
    q = (
        "MATCH (m:Mechanism) "
        "WHERE $topic IS NULL OR m.topic = $topic "
        "RETURN m"
    )
    with driver.session() as sess:
        nodes = [rec["m"] for rec in sess.run(q, topic=topic)]
    out: List[Mechanism] = []
    for n in nodes:
        try:
            out.append(node_to_mech(n))
        except ValidationError as e:
            print("⚠️  Skipping malformed Mechanism:", e)
    return out


def count_artifacts(topic: str) -> int:
    cypher = "MATCH (a:Artifact {topic:$t}) RETURN count(a) AS c"
    with driver.session() as sess:
        return sess.run(cypher, t=topic).single()["c"]

def chunked(iterable, size):
    """Yield successive chunks from iterable of given size."""
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]

def artifact_key(a):
    # ignore order of mechanism_ids
    return (a.topic, a.name.lower().strip(),
            tuple(sorted(a.principle_chain)))

def dedup_by_key(artifacts):
    seen = set()
    unique = []
    for art in artifacts:
        k = artifact_key(art)
        if k not in seen:
            seen.add(k)
            unique.append(art)
    return unique



# ── main routine ───────────────────────────────────────────────────────

def run(topic: str | None, mode: str, n: int):
    if n < 15: 
        mechs = fetch_mechanisms(topic)
        if not mechs:
            print("No mechanisms found.")
            return

        # bucket mechanisms per topic
        buckets = defaultdict(list)
        for m in mechs:
            buckets[m.topic].append(m)

        total_new = 0
        for t, bucket in buckets.items():
            existing = count_artifacts(t)
            if mode == "add":
                k = n
            elif mode == "max":
                k = max(0, n - existing)
            else:
                raise ValueError("mode must be 'add' or 'max'")

            if k == 0:
                print(f"↷ Topic '{t}' already has ≥{n} artifacts")
                continue

            arts: List[Artifact] = generate_artifacts(t, bucket, max_artifacts=k)
            load_artifacts(arts)
            total_new += len(arts)
            print(f"✅ Added {len(arts)} artifact(s) for topic '{t}'")
    else:
        batch_size   = 15          # mechanisms per call
        arts_per_run = 4           # up-to N artifacts per call
        target_total = n
        all_new = []
        mechs = fetch_mechanisms(topic)

        for chunk in chunked(mechs, batch_size):
            arts = generate_artifacts(topic, chunk, max_artifacts=arts_per_run)
            all_new.extend(arts)
            if len(all_new) >= target_total:
                break

        all_unique = dedup_by_key(all_new)
        ranked     = sorted(all_unique, key=lambda a: a.trl, reverse=True)[:target_total]
        load_artifacts(ranked)

        total_new = len(all_new)


    print(f"🎉 Done. {total_new} new artifacts written to graph.")

# ── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Add/top-up Artifact nodes from Mechanisms")
    ap.add_argument("--topic", default=None, help="Restrict to one topic")
    ap.add_argument("--mode", choices=["add", "max"], required=True)
    ap.add_argument("--n", type=int, required=True, help="Number of artifacts per topic")
    args = ap.parse_args()

    run(args.topic, args.mode, args.n)
