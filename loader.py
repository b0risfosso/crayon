# loader.py
import os
from typing import List
from dotenv import load_dotenv
from neo4j import GraphDatabase, Session
from pydantic import BaseModel
from models import Claim, Source, Mechanism
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
SET c.text       = $text,
    c.confidence = $confidence,
    c.topic      = $topic,
    c.last_seen  = timestamp(),
    c.stale      = false
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
      m.last_seen   = timestamp(),
      m.stale       = false
"""

LINK_SRC_MECH = """
MATCH (s:Source {id: $source_id}), (m:Mechanism {id: $id})
MERGE (s)-[:DESCRIBES]->(m)
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