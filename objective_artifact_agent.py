"""objective_artifact_agent.py  (v0.2)
Generate Artifact nodes directly from *(Objective text + Source abstract)*
using the **expanded, updatable Artifact schema**.

Key updates
-----------
• Uses new fields: `tool_anchor`, `primary_metric`, `validation_steps`,
  `created_from`, `created_at`, `maturity`.
• Embeds provenance IDs (`source_id`, `objective_id`) directly in the node.
• Keeps `principle_chain` optional (empty at birth).
"""

from __future__ import annotations
import argparse, hashlib, json, os, sys
from datetime import datetime, UTC
from typing import List

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.graph import Node
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from models import Source, Artifact, Objective
from loader import driver, load_artifacts

load_dotenv()
client = OpenAI()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# ── Cypher templates ──────────────────────────────────────────────────────
MERGE_OBJ = """
MERGE (o:Objective {id:$id})
SET   o.text=$text, o.topic=$topic, o.created_at=$created_at
"""
LINK_OBJ_SRC = "MATCH (o:Objective {id:$oid}), (s:Source {id:$sid}) MERGE (s)-[:RELEVANT_TO]->(o)"
LINK_ART_PROV = """
MATCH (s:Source{id:$sid}), (o:Objective{id:$oid}), (a:Artifact{id:$aid})
MERGE (s)-[:INSPIRES]->(a)
MERGE (a)-[:ADVANCES]->(o)"""

# ── LLM function spec (updated) ─────────────────────────────────────────--
ART_FUNC = {
    "type": "function",
    "function": {
        "name": "emit_artifacts_from_objective",
        "description": "Generate artifacts that advance the objective; include tool, metric, protocol.",
        "parameters": {
            "type": "object",
            "properties": {
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "name", "description", "expected_outcomes", "trl",
                            "tool_anchor", "primary_metric", "validation_steps"
                        ],
                        "properties": {
                            "name":  {"type": "string"},
                            "description": {"type": "string"},
                            "expected_outcomes": {"type": "array", "items": {"type": "string"}},
                            "trl": {"type": "integer", "minimum":1, "maximum":9},
                            "tool_anchor": {"type":"string"},
                            "primary_metric": {"type":"string"},
                            "validation_steps": {"type":"array", "items":{"type":"string"}}
                        }
                    }
                }
            },
            "required": ["artifacts"]
        }
    }
}

SYS_PROMPT = (
    "You are a design engineer. Using ONLY the source abstract and the objective, "
    "propose up to {k} concrete artifacts. Each artifact must include:\n"
    "• tool_anchor (real rig, genetic line, software)\n"
    "• primary_metric (one measurable KPI)\n"
    "• 2–4 validation_steps\n"
    "Return JSON via function schema." )

# ── helper functions ──────────────────────────────────────────────────────

def node_to_source(node: Node) -> Source:
    return Source(**dict(node))


def fetch_sources_for_topic(topic: str) -> List[Source]:
    q = "MATCH (s:Source {topic:$t}) RETURN s"
    with driver.session() as sess:
        return [node_to_source(r["s"]) for r in sess.run(q, t=topic)]


def fetch_source_by_id(sid: str) -> Source | None:
    q = "MATCH (s:Source {id:$sid}) RETURN s LIMIT 1"
    with driver.session() as sess:
        rec = sess.run(q, sid=sid).single()
        return node_to_source(rec["s"]) if rec else None

# ── Artifact generation ───────────────────────────────────────────────────

def generate_artifacts(obj: Objective, src: Source, k: int) -> List[Artifact]:
    sys_msg = SYS_PROMPT.format(k=k)
    user_msg = f"OBJECTIVE:\n{obj.text}\n\nTITLE: {src.title}\nABSTRACT:\n{src.summary}"

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role":"system","content":sys_msg}, {"role":"user","content":user_msg}],
        tools=[ART_FUNC], tool_choice="auto", temperature=0.2)

    args_raw = resp.choices[0].message.tool_calls[0].function.arguments
    raws = json.loads(args_raw)["artifacts"][:k]

    artifacts: List[Artifact] = []
    for art in raws:
        ts = int(datetime.now(UTC).timestamp() * 1000)
        hid = hashlib.md5((obj.id+src.id+art["name"]).encode()).hexdigest()[:10]
        artifacts.append(
            Artifact(
                id             = f"{obj.topic.replace(' ','_')}_a{hid}",
                topic          = obj.topic,
                name           = art["name"],
                description    = art["description"],          # keep if you still store
                rationale      = f"Advances objective: {obj.text[:60]}…",
                principle_chain = [],
                trl            = art["trl"],
                maturity       = "draft",
                created_at     = ts,
                last_updated   = ts,
                created_from   = [src.id, obj.id],

                tool_anchor       = art["tool_anchor"],
                primary_metric    = art["primary_metric"],
                validation_steps  = art["validation_steps"],
                expected_outcomes = art["expected_outcomes"],   # new
            )
        )
    return artifacts

# ── loaders ----------------------------------------------------------------

def load_objective(obj: Objective):
    with driver.session() as sess:
        sess.run(MERGE_OBJ, **obj.model_dump())


def write_provenance(obj: Objective, src: Source, arts: List[Artifact]):
    with driver.session() as sess:
        for art in arts:
            sess.run(LINK_OBJ_SRC, oid=obj.id, sid=src.id)
            sess.run(LINK_ART_PROV, sid=src.id, oid=obj.id, aid=art.id)

# ── CLI -------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Objective→Artifact generator with new schema")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--source", default=None)
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args()

    if not args.source and not args.topic:
        sys.exit("Provide --source ID or --topic name.")

    obj_id = hashlib.md5((args.objective + (args.topic or "global")).encode()).hexdigest()[:10]
    objective = Objective(id=f"obj_{obj_id}", text=args.objective, topic=args.topic or "global")
    load_objective(objective)

    # select sources
    if args.source:
        srcs = [fetch_source_by_id(args.source)]
        if srcs[0] is None: sys.exit("Source ID not found.")
    else:
        srcs = fetch_sources_for_topic(args.topic)
        if not srcs: sys.exit("No sources for topic.")

    total = 0
    for src in srcs:
        arts = generate_artifacts(objective, src, k=args.k)
        load_artifacts(arts)
        write_provenance(objective, src, arts)
        total += len(arts)
        print(f"✅ {len(arts)} artifact(s) from '{src.title[:45]}…'")

    print(f"🎉 Done. {total} artifacts linked to objective '{objective.text[:50]}…'")
