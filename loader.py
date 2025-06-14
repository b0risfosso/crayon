# loader.py
import os
from typing import List
from dotenv import load_dotenv
from neo4j import GraphDatabase, Session
from pydantic import BaseModel
from models import Claim, Source, Mechanism, Artifact, Risk, Control, Metric, Measurement
from utils import assert_loader_fields

# ------------- 1. Ontology models (or import your existing models) ----------
# ...

# ------------- 2. Neo4j driver bootstrap -----------------------------------
load_dotenv()
NEO4J_URI  = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PWD  = os.getenv("NEO4J_PWD")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PWD))

# ------------- 3. Cypher templates -----------------------------------------
CREATE_SOURCE = """
MERGE (s:Source {id: $id})
SET s.topic       = $topic,
    s.type        = $type,
    s.url         = $url,
    s.year        = $year,
    s.credibility = $credibility,
    s.summary     = $summary,
    s.title       = $title,
    s.last_seen   = timestamp(),
    s.stale       = false
"""

MERGE_TOPIC = """
MERGE (t:Topic {name: $topic})
"""

LINK_TOPIC_SOURCE = """
MATCH (t:Topic {name: $topic}), (s:Source {id: $id})
MERGE (t)<-[:ABOUT]-(s)
"""

CREATE_CLAIM = """
MERGE (c:Claim {id: $id})
SET c.text          = $text,
    c.confidence    = $confidence,
    c.topic         = $topic,
    c.source_id     = $source_id,
    c.last_seen     = timestamp(),
    c.stale         = false
"""

LINK_SOURCE_CLAIM = """
MATCH (s:Source {id: $source_id}), (c:Claim {id: $id})
MERGE (s)-[:SUPPORTS]->(c)
"""


# ── create + link mechanism ──
CREATE_MECH = """
MERGE (m:Mechanism {id: $id})
SET   m.inputs      = $inputs,
      m.outputs     = $outputs,
      m.principle   = $principle,
      m.topic       = $topic,
      m.confidence  = $confidence,
      m.source_id   = $source_id,
      m.last_seen   = timestamp(),
      m.stale       = false
"""

LINK_SRC_MECH = """
MATCH (s:Source {id: $source_id}), (m:Mechanism {id: $id})
MERGE (s)-[:DESCRIBES]->(m)
"""

# ---- create artifact mechanisms

# ---- Artifact Cypher templates -----------------------------------------
# ---- Artifact Cypher templates -------------------------------------------
# ---- Artifact Cypher template (v2) ---------------------------------------
CREATE_ART = """
MERGE (a:Artifact {id:$id})
SET  a.topic              = $topic,
     a.name               = $name,
     a.created_at         = $created_at,
     a.created_from       = $created_from,

     /* core concept */
     a.rationale          = $rationale,
     a.principle_chain    = $principle_chain,
     a.description        = $description,

     /* design status */
     a.trl                = $trl,
     a.maturity           = $maturity,
     a.novelty_score      = $novelty_score,
     a.speculative        = $speculative,

     /* engineering detail */
     a.tool_anchor        = $tool_anchor,
     a.bill_of_materials  = $bill_of_materials,
     a.cost_capex_usd     = $cost_capex_usd,
     a.running_cost_usd   = $running_cost_usd,
     a.primary_metric     = $primary_metric,
     a.target_range       = $target_range,
     a.validation_steps   = $validation_steps,
     a.workflow           = $workflow,
     a.expected_outcomes  = $expected_outcomes,

     /* dynamic */
     a.supports           = $supports,
     a.refutes            = $refutes,
     a.measurement_ids    = $measurement_ids,

     /* lineage + bookkeeping */
     a.parent_ids         = $parent_ids,
     a.last_updated       = $last_updated,
     a.stale              = false
"""

LINK_MECH_ART = """
MATCH (m:Mechanism {id:$mid}), (a:Artifact {id:$aid})
MERGE (m)-[:ENABLES]->(a)
"""


# ---- Risk & Control Cypher templates -----------------------------------
CREATE_RISK = """
MERGE (r:Risk {id: $id})
SET   r.description = $description,
      r.severity    = $severity,
      r.likelihood  = $likelihood,
      r.topic       = $topic,
      r.last_seen   = timestamp(),
      r.stale       = false
"""

CREATE_CONTROL = """
MERGE (c:Control {id: $id})
SET   c.description   = $description,
      c.effectiveness = $effectiveness,
      c.cost_level    = $cost_level,
      c.last_seen     = timestamp(),
      c.stale         = false
"""

LINK_ART_RISK = """
MATCH (a:Artifact {id:$aid}), (r:Risk {id:$rid})
MERGE (a)-[:HAS_RISK]->(r)
"""

LINK_RISK_CONTROL = """
MATCH (r:Risk {id:$rid}), (c:Control {id:$cid})
MERGE (r)-[:MITIGATED_BY]->(c)
"""

# metric templates

CREATE_METRIC = """
MERGE (k:Metric {id:$id})
SET   k.name         = $name,
      k.unit         = $unit,
      k.target_range = $target_range,
      k.topic        = $topic,
      k.last_seen    = timestamp(),
      k.stale        = false
"""
LINK_ART_METRIC = """
MATCH (a:Artifact {id:$aid}), (k:Metric {id:$kid})
MERGE (a)-[:MEASURED_BY]->(k)
"""



# for tests ----------------------
SOURCE_KEYS = {
    "id", "topic", "type", "url", "year", "credibility", "summary", "title"
}
CLAIM_KEYS = {"id", "source_id", "topic", "text", "confidence"}
MECH_KEYS = {
    "id", "source_id", "topic", "inputs", "outputs", "principle", "confidence"
}





# ------------- 4. Loader functions -----------------------------------------
def _run(tx: Session, src: Source):
    assert_loader_fields(src, SOURCE_KEYS, "Source")

    tx.run(MERGE_TOPIC, topic=src.topic)
    tx.run(CREATE_SOURCE, **src.model_dump())
    tx.run(LINK_TOPIC_SOURCE, topic=src.topic, id=src.id)

def load_sources(sources: List[Source]) -> None:
    with driver.session() as session:
        for src in sources:
            session.execute_write(_run, src)


# ------------ 5. Claim functions --------------------------------------------
def _run_claim(tx, cl: Claim):
    assert_loader_fields(cl, CLAIM_KEYS, "Claim")

    tx.run(CREATE_CLAIM, **cl.model_dump())
    tx.run(LINK_SOURCE_CLAIM, source_id=cl.source_id, id=cl.id)

def load_claims(claims: List[Claim]) -> None:
    with driver.session() as session:
        for cl in claims:
            session.execute_write(_run_claim, cl)

# -------------- Mechanism functions ----------------------------------------
def _run_mech(tx, mech: Mechanism):
    assert_loader_fields(mech, MECH_KEYS, "Mechanism")
    tx.run(CREATE_MECH, **mech.model_dump())
    tx.run(LINK_SRC_MECH, source_id=mech.source_id, id=mech.id)

def load_mechanisms(mechs: List[Mechanism]):
    with driver.session() as sess:
        for mech in mechs:
            sess.execute_write(_run_mech, mech)


# -------------- Artifact loader -------------------------------------------
def _art_props(art: Artifact) -> dict:
    """Convert Pydantic model to dict and drop None values."""
    props = art.model_dump()
    return {k: v for k, v in props.items() if v is not None}

# ---- Loader helper -------------------------------------------------------
def _prune_none(d: dict) -> dict:
    """Remove keys whose value is None to avoid storing nulls in Neo4j."""
    return {k: v for k, v in d.items() if v is not None}

def load_artifacts(arts: list[Artifact]) -> None:
    with driver.session() as sess:
        for art in arts:
            sess.run(CREATE_ART, **art.model_dump())

            for mid in art.principle_chain:
                sess.run(LINK_MECH_ART, mid=mid, aid=art.id)

# _________________ risk mechanisms ________________________________________
def load_risks_controls(risks: list[Risk], controls: list[Control]):
    with driver.session() as sess:
        for r in risks:
            sess.run(CREATE_RISK, **r.model_dump())
            sess.run(LINK_ART_RISK, aid=r.artifact_id, rid=r.id)
        for c in controls:
            sess.run(CREATE_CONTROL, **c.model_dump())
            sess.run(LINK_RISK_CONTROL, rid=c.risk_id, cid=c.id)

# -------------- metric mechanisms -----------------------------
def load_metrics(metrics: list[Metric]):
    with driver.session() as sess:
        for m in metrics:
            sess.run(CREATE_METRIC, **m.model_dump())
            sess.run(LINK_ART_METRIC, aid=m.artifact_id, kid=m.id)


# ------------- 6. CLI test --------------------------------------------------
test = """
if __name__ == "__main__":
    from scout import arxiv_search
    from sourc_extractor import extract_source
    from claim_extractor import extract_claims

    raw = arxiv_search("heart morphogenesis", max_results=1)[0]
    src  = extract_source(raw)
    claims = extract_claims(src, k=5)

    load_sources([src])
    load_claims(claims)
    print("✅ loaded claims:", len(claims))

"""