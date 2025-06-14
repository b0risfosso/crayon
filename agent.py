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

    obj1 = "What are the limits that you can push?"
    # Example: Gene cloning and DNA Analysis -> What are the artifacts? What are the limits of those artifacts? 
    #   What are the limits that can you can push? What are the artifacts once you push those limits? 
    obj2 = "What are the artifacts that have a relationship to the lake? to housing?"
    obj3 = "Mutate the following artifacts to get closer to ____ objective."
    # I want a house on the lake. I have genomics. How can I create a house on the lake using hte tools of genomics? 
    # Mutate the following artifacts to enable the engineering of mermaids, of friendship. 
    # build with the following set of artifacts to enable the building of friendship. To enable the engineering of water animals. 
    # change my perception of temperature in the water. 
    obj4 = "What is the history of the artifacts of genomics (the set of artifacts) and the {the perception of temperature}."
    obj5 = "Mutate the set of artifacts towards an objective of manipulating {the perception of temperature}."
    # breathable water, headphones, a boat, body heating system. 
    obj6 = "Use the following artifacts to build a {boat}."
    obj7 = "Which artifact should be mutated to better suit the following objective {breathing in water}".  
    obj8 = "Build a list of artifacts that engineer {breathing in water} / {breathing} / {water}."
    # learn to swim. walk on water. flight. cross species transformation. water gun. water defense. water bending, water engineering. 
    # explore the entirety of lake Michigan.
    obj9 = "What are the artifacts from this piece of information. / What are the artifacts that deviate radically from the norm."
    obj10 = "Do a meta-analysis on the trajectory of the artifact production and innovation in the following field. What are the conditions in \
        These artifacts are created? what are alternative missed trajectories of artifacts?"
    obj11 = "what are radically different objectives for the following artifacts?"
    # water bending, magic wands. 
    obj12 = "What ist he collective set of all possible artifacts stemming from this book? What is the decomposition of artifact space?\
        What are the conditions of exploring artifact space? mutating subspace towards objectives."
    obj13 = "What are the artifacts of {good breath, good hygenie, smell good}? How can you mutate this set of artifacts to serve this objective?"
    obj14 = "what are the artifacts of {...}? How do the artifacts of this text compete with this artifact?"
    obj15 = "what is the relationship between these this artifact and {....}? Mutate this artifact to change this relationship."
    obj16 = "How can you get from zero to one for {...}, using this artifact. "
    obj17 = "what are the artifacts of the following text? What are the limits of these artifacts? How can you push these limits?"
    obj18 = "what are teh foundational artifacts for this text?"
    obj19 = "What are the subartifacts for this artifact? perform recursive artifact search."
    obj20 = "In what context is the following artifact used?"
    obj21 = "What are the stories that this artifact is involved in? How would these stories change as this artifact is changed?"
    obj22 = "what is foundational theory which this artifact is based on? What are the technologies that this artifact is based on? If you mutate the theories \
        or technologies, how can the artifacts evolve?"
    obj23 = "what is the architecture of this artifact?"
    obj24 = "Which artifacts have died in the evolution of this field/domain? How can you revive and organize the following artifacts?"
    obj25 = "How do you design and build the following artifact?"
    obj26 = "What is the decision-making behind the use of the following artifact?"
    obj27 = "Who are the opponents of the following artifacts? What are the proposed alternatives?"
    obj28 = "What is the developmental process of the following artifact?"
    obj29 = "what artifacts can you create from the following information?"
    obj30 = "What are the different domains in which you can mutate the following artifact towards objective {...}? / How can the following artifact mutate?"
    obj31 = "How can this following become an artifact of {...}?"
    obj32 = "Using the following artifact as a building block for a larger system, what can be built?"
    # hierarchical analysis of artifacts. there is a sequential ordering of questioning/investigation which provides the greatest
    #   creative freedom. Find that order. 
    obj33 = "How can you use the following artifact to get from zero to one in {...}?"
    obj34 = "what problems does this following set of artifacts solve?"
    obj35 = "How does the following artifact evolve?"
    obj36 = "What are the test of feasibility of a hypothesized artifact?"
    obj37 = "What are the inputs/outputs of the following artifact? How can you use the inputs/outputs? How can you mutate the inputs/outputs?"
    obj38 = "what is the design space of this artifact? How is design space explored? discovered? utilized?"
    obj39 = "what is the philosophy of this following artifact?"
    obj40 = "Which systems make use of the artifacts of the following system?"
    obj41 = "What are the science fiction variations of the following artifact?"
    obj42 = "How is this artifact monetizable? What are the many layers of this artifact that derive a monetary relationship?"
    obj43 = "what is the functional space of this artifact? What does mastery of this artifact allow functionally?"
    obj44 = "what are the constraints of the following artifact?"
    obj45 = "What are the business ventures that exist based on the following artifact? What is the foundation for these business ventures?"
    obj46 = "What is a trillion dollar business venture based on this artifact?"
    obj47 = "What is the effectiveness of this artifact in solving {...}?"
    obj48 = "What is the family lineage of this artifact? What are related family lineages? How does this family evovle?"
    obj49 = "What artifact does this artifact work best with? What is the best artifact pairing?"
    obj50 = "Design another artifact that performing the..."
    obj51 = "What are the actions one can take with this artifact?"
    obj52 = "What is the problem space that this artifact lives in?"
    obj53 = "What are the workflows that this artifact is a part of?"
    obj54 = "How do artifacts vary between between {...}?" # meta-analysis question? 
    obj55 = "How do you mutate this artifact in favor of increasing the compatibility with the following {system/character/problm/objective}?"
    obj56 = "what is the series of artifacts that this artifact is embedded within?"
    obj57 = "What are the software-based artifacts of the following text?"
    obj58 = "what are the artifacts in storytelling? education? government? money?"


    
