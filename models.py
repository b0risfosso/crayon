from pydantic import BaseModel, Field
from datetime import datetime, UTC
from typing import Literal

    
class Source(BaseModel):
    id: str
    topic: str
    type: str = Field(default="idea")    # ← default provided!
    url: str | None = None                # ← should be Optional[str]
    year: int | None = None
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
    # ── identity (immutable) ───────────────────────────
    id: str                     # deterministic hash
    topic: str                  # e.g. "Drosophila hindgut morphogenesis"
    name: str                   # human-friendly label
    created_at: int             # epoch-ms
    created_from: list[str]     # [source_id, objective_id] for provenance

    # ── core concept (rarely changes) ─────────────────
    rationale: str              # 1-2 lines: “why this drives the objective”
    principle_chain: list[str]  # mechanism IDs (optional at birth)
    description: str

    # ── design status (updateable) ────────────────────
    trl: int                    # NASA 1-9
    maturity: Literal[
        "draft", "curated", "bench-tested", "validated", "retired"
    ] = "draft"

    novelty_score: float | None = None   # embedding distance 0-1
    speculative: bool = False

    # ── engineering details (iteratively enriched) ───
    tool_anchor: str | None = None       # “two-photon femtosecond laser”
    bill_of_materials: list[str] = []    # part numbers, FlyBase IDs, etc.
    cost_capex_usd: float | None = None
    running_cost_usd: float | None = None

    primary_metric: str | None = None    # link to Metric.id
    target_range: tuple[float, float] | None = None

    validation_steps: list[str] = []     # 3-5 bullet protocol

    workflow: list[dict] = []            # free-form steps:
    # [{step:"Laser ablate junction", tool:"two-photon", input:"UAS-GFP", output:"strain map"}, …]

    # ── dynamic fields filled by agents ───────────────
    supports: list[str] = []             # Claim IDs that SUPPORT
    refutes: list[str] = []              # Claim IDs that REFUTE
    measurement_ids: list[str] = []      # Measurement nodes
    expected_outcomes: list[str] = []    # what can you do with it? What are the expected outcoumes? what information/abilities does this tool/experiement provide? 

    # ── bookkeeping ───────────────────────────────────
    parent_ids: list[str] = []           # lineage (mutation or merge)
    last_updated: int                    # epoch-ms
    stale: bool = False



# ── Objective model --------------------------------------------------------
class Objective(BaseModel):
    id: str
    text: str
    topic: str
    created_at: int = Field(default_factory=lambda: int(datetime.now(UTC).timestamp() * 1000))

class Risk(BaseModel):
    id: str                 # deterministic
    artifact_id: str        # FK to Artifact
    topic: str
    description: str
    severity: str           # "low" | "moderate" | "high" | "critical"
    likelihood: str         # "rare" | "occasional" | "likely" | "frequent"

class Control(BaseModel):
    id: str
    risk_id: str            # FK to Risk
    artifact_id: str        # convenience link
    description: str
    effectiveness: str      # "low" | "medium" | "high"
    cost_level: str         # "$" | "$$" | "$$$"

class Metric(BaseModel):
    id: str                 # deterministic
    artifact_id: str        # FK → Artifact
    topic: str
    name: str               # e.g. "wall_thickness"
    unit: str               # "µm", "N cm⁻²", "kPa", …
    target_range: tuple[float, float] | None = None

class Measurement(BaseModel):
    id: str                 # deterministic
    metric_id: str          # FK → Metric
    artifact_id: str
    timestamp: int          # epoch-ms
    value: float


