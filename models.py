from pydantic import BaseModel, Field

    
class Source(BaseModel):
    id: str
    topic: str
    type: str
    url: str
    year: int
    credibility: float
    summary: str
    title: str


class Claim(BaseModel):
    id: str               # deterministic; e.g. f"{source_id}_c{n}"
    source_id: str        # FK to Source.id
    topic: str
    text: str
    confidence: float     # 0-1


class Mechanism(BaseModel):
    id: str               # deterministic
    source_id: str        # FK to Source
    topic: str
    inputs: list[str]
    outputs: list[str]
    principle: str        # e.g. "mechanical stretch", "Wnt/BMP gradient"
    confidence: float     # 0–1 LLM self-estimate


class Artifact(BaseModel):
    id: str                 # deterministic
    topic: str
    name: str
    description: str
    principle_chain: list[str]   # ordered list of mechanism IDs
    expected_outputs: list[str]
    trl: int                 # Technology Readiness Level 1–9

