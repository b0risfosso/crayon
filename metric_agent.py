"""
Metric agent: create metrics for each artifact (if missing)
Usage:
    python metric_agent.py                    # all artifacts
    python metric_agent.py "artifact_id_xyz"  # single artifact
"""

import sys
from loader import driver, load_metrics
from metric_extractor import extract_metrics
from neo4j.graph import Node
from models import Artifact         # your Pydantic class

def fetch_artifacts(artifact_id=None):
    cypher = """
    MATCH (a:Artifact)
    WHERE $aid IS NULL OR a.id = $aid
    RETURN a
    """
    with driver.session() as sess:
        return [rec["a"] for rec in sess.run(cypher, aid=artifact_id)]

def metric_exists(aid):
    cypher = "MATCH (a:Artifact {id:$aid})-[:MEASURED_BY]->(:Metric) RETURN count(*)>0 AS has"
    with driver.session() as sess:
        return sess.run(cypher, aid=aid).single()["has"]

def node_to_art(node: Node) -> Artifact:
    return Artifact(**dict(node))   # raises ValidationError if props missing

if __name__ == "__main__":
    aid_arg = sys.argv[1] if len(sys.argv) > 1 else None
    arts = fetch_artifacts(aid_arg)
    if not arts:
        print("No artifacts found.")
        sys.exit(0)

    created = 0
    for node in arts:                   # arts is list[Node]
        art = node_to_art(node)         # convert
        if metric_exists(art.id):
            if getattr(art, 'name', art.id):
                print(f"↷ metrics already present for {art.name}")
            else:
                print(f"metric present for {art.id}, name not found")
            continue
        if not art.expected_outputs: 
            continue
        else:
            mets = extract_metrics(art)     # now passes a real Artifact object
            load_metrics(mets)
            created += len(mets)
            print(f"✅ {len(mets)} metrics added for '{art.name}'")

    print(f"🎉 Done. {created} metric nodes written.")
