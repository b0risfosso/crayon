import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request, Response
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from openai import OpenAI, OpenAIError
# app.py (top-level, after Flask app creation)
import sqlite3, json, os
from contextlib import closing
import hashlib 
import re
import json
from pathlib import Path
from datetime import datetime, timezone
from xai_sdk import Client as XAIClient
from xai_sdk.chat import system as xai_system, user as xai_user
from google import genai
import uuid
import threading
import time
from prompts import (
    SYSTEM_INSTRUCTIONS,
    HTML_BOILERPLATE,
    BOX_OF_DIRT_ARTIFACTS_SYSTEM,
    BOX_OF_DIRT_PROMPT,
    PROTOTYPE_SYS_MSG,
    DIM_SYS_MSG,
    SEED_SYS_MSG,
    VALIDATION_SYS_MSG,
    STAKEHOLDERS_SYS_MSG,
    EMBODIED_SYS_MSG,
    ROADMAP_SYS_MSG,
    ARCHETYPE_PLAYBOOK_SYS_MSG,
)

app = Flask(__name__)

DB_PATH = "/var/www/site/data/narratives_data.db"  # keep consistent with your setup

PANELS_ROOT = Path("/var/www/site/data/assets/panels")  # target on disk
ASSETS_ROOT = Path("/var/www/site/data/assets/panels")   # << per your ask
WEB_PREFIX  = "/assets/panels"                            # serve as: /assets/seed_{id}/...

BULK_JOBS = {}  # job_id -> { created_at, n, done, ok, fail, items: [ {seed_id, status, error, version} ], started_at, finished_at }
BULK_JOBS_LOCK = threading.Lock()

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_text(s: str) -> str:
    if not s:
        return ""
    m = _JSON_FENCE_RE.search(s)
    if m:
        return m.group(1).strip()
    return s.strip()

def _pydantic_from_json(model_cls, text: str):
    # Try exact JSON → model; if failure, raise to caller
    return model_cls.model_validate_json(text)

def _call_internal(method: str, path: str, *, json=None, accept='application/json'):
    """
    Call our own Flask routes in-process using test_client.
    Returns (status_code, response_text_or_json).
    - If Accept is application/json, we parse JSON; otherwise we return text.
    """
    with app.test_client() as c:
        headers = {'Accept': accept}
        if method.upper() == 'POST':
            rv = c.post(path, json=json, headers=headers)
        elif method.upper() == 'GET':
            rv = c.get(path, headers=headers)
        else:
            raise RuntimeError(f'Unsupported method {method}')

        if accept == 'application/json':
            try:
                return rv.status_code, rv.get_json()
            except Exception:
                return rv.status_code, None
        else:
            return rv.status_code, rv.get_data(as_text=True)



def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def bootstrap_schema():
    with closing(connect()) as con, con:
        # ---- narratives
        con.execute("""
        CREATE TABLE IF NOT EXISTS narratives (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            webpage TEXT
        );
        """)

        # ---- narrative_dimensions (+ targets_json check + provider)
        con.execute("""
        CREATE TABLE IF NOT EXISTS narrative_dimensions (
            id INTEGER PRIMARY KEY,
            narrative_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (narrative_id) REFERENCES narratives(id) ON DELETE CASCADE
        );
        """)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(narrative_dimensions)")]
        if "targets_json" not in cols:
            con.execute("ALTER TABLE narrative_dimensions ADD COLUMN targets_json TEXT")
        if "provider" not in cols:
            # store provenance of which LLM/provider generated/last updated this dim
            con.execute("ALTER TABLE narrative_dimensions ADD COLUMN provider TEXT")

        # helpful lookups
        con.execute("""CREATE INDEX IF NOT EXISTS idx_dimensions_provider
                       ON narrative_dimensions(provider);""")

        # ---- narrative_seeds (+ provider)
        con.execute("""
        CREATE TABLE IF NOT EXISTS narrative_seeds (
            id INTEGER PRIMARY KEY,
            dimension_id INTEGER NOT NULL,
            problem TEXT NOT NULL,
            objective TEXT NOT NULL,
            solution TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (dimension_id) REFERENCES narrative_dimensions(id) ON DELETE CASCADE
        );
        """)
        seed_cols = [r["name"] for r in con.execute("PRAGMA table_info(narrative_seeds)")]
        if "provider" not in seed_cols:
            con.execute("ALTER TABLE narrative_seeds ADD COLUMN provider TEXT")

        # helpful lookups
        con.execute("""CREATE INDEX IF NOT EXISTS idx_seeds_provider
                       ON narrative_seeds(provider);""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_seeds_dim_provider
                       ON narrative_seeds(dimension_id, provider);""")

        # ---- seed_artifacts
        con.execute("""
        CREATE TABLE IF NOT EXISTS seed_artifacts (
            id INTEGER PRIMARY KEY,
            seed_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'box_of_dirt',
            title TEXT,
            html TEXT NOT NULL,
            doc_format TEXT NOT NULL DEFAULT 'full' CHECK (doc_format IN ('full','body')),
            version INTEGER NOT NULL DEFAULT 1,
            is_published INTEGER NOT NULL DEFAULT 0,
            checksum TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (seed_id) REFERENCES narrative_seeds(id) ON DELETE CASCADE
        );
        """)
        con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_seed_artifacts_unique
                       ON seed_artifacts(seed_id, kind, version);""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_seed_artifacts_lookup
                       ON seed_artifacts(seed_id, kind, is_published, version);""")

        # ---- manifests
        con.execute("""
        CREATE TABLE IF NOT EXISTS seed_manifests (
            id INTEGER PRIMARY KEY,
            seed_id INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            updated TEXT NOT NULL,
            panels_json TEXT NOT NULL,
            is_published INTEGER NOT NULL DEFAULT 0,
            checksum TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (seed_id) REFERENCES narrative_seeds(id) ON DELETE CASCADE
        );
        """)
        con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_manifest_unique
                       ON seed_manifests(seed_id, version);""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_manifest_lookup
                       ON seed_manifests(seed_id, is_published, version);""")

        # ---- helpful uniqueness (unchanged)
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_narratives_title ON narratives(title);")
        con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_unique
                       ON narrative_dimensions(narrative_id, title);""")
        # NOTE: we keep seed dedup independent of provider so exact duplicates don't multiply.
        con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_seed_dedup
                       ON narrative_seeds(dimension_id, problem, objective, solution);""")



bootstrap_schema()

_gemini_client = None
def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        if genai is None:
            raise RuntimeError("google-genai not installed. `pip install google-genai`")
        # The SDK reads GOOGLE_API_KEY from the environment.
        if not os.getenv("GOOGLE_API_KEY"):
            raise RuntimeError("GOOGLE_API_KEY not set")
        _gemini_client = genai.Client()
    return _gemini_client

# --- DeepSeek client (same API as OpenAI) ---
_deepseek_client = None
def get_deepseek_client():
    global _deepseek_client
    if _deepseek_client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")
        _deepseek_client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )
    return _deepseek_client

def _deepseek_model():
    return os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def _get_llm():
    if OpenAI is None:
        raise RuntimeError("openai client not installed")
    api_key  = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("LLM API key missing")
    client = OpenAI(api_key=api_key)
    return client

_xai_client = None
def get_xai_client():
    global _xai_client
    if _xai_client is None:
        if XAIClient is None:
            raise RuntimeError("xai_sdk is not installed. `pip install xai-sdk`")
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise RuntimeError("XAI_API_KEY not set")
        _xai_client = XAIClient(api_key=api_key)
    return _xai_client

class NarrativeDimension(BaseModel):
    name: str = Field(..., description="Short title for the dimension")
    thesis: str = Field(..., description="1–2 sentence distilled description")
    targets: List[str] = Field(..., description="3–6 concrete narrative targets")

class NarrativeDimensions(BaseModel):
    dimensions: List[NarrativeDimension]

class NarrativeSeed(BaseModel):
    problem: str = Field(..., description="A (Problem)")
    objective: str = Field(..., description="B (Objective)")
    solution: str = Field(..., description="Solution (Link)")

class NarrativeSeeds(BaseModel):
    seeds: List[NarrativeSeed]

class NarrativePrototype(BaseModel):
    core_intent: str = Field(..., description="Smallest truth or principle tested")
    minimal_build: str = Field(..., description="Tiny computational/physical/narrative sketch")
    load_bearing_test: str = Field(..., description="What to show and the validating reaction")
    first_eyes: List[str] = Field(..., description="Who sees it first")
    why_box_of_dirt: str = Field(..., description="Why it's minimal, disposable, growth-inviting")

class BoxOfDirtRequest(BaseModel):
    domain: str
    dimension: str
    seed: str         # raw multiline text containing A/B/Solution lines
    prototype: str    # raw multiline text (Core Intent, Minimal Build, Panels, etc.)
    thesis: Optional[str] = None  # optional dimension thesis

class RenderSeedRequest(BaseModel):
    artifact_yaml: str
    model: Optional[str] = None
    temperature: Optional[float] = 0.1

class RenderSeedResponse(BaseModel):
    html: str

# app.py (near other Pydantic models)

class DatasetRef(BaseModel):
    name: str = Field(..., description="Dataset/report/test standard name")
    organization: str = Field(..., description="Publisher or steward (e.g., UN, WHO, World Bank, DHS, OECD, ACLED)")
    url: Optional[str] = Field(None, description="Direct link to dataset or canonical landing page")
    notes: Optional[str] = Field(None, description="Brief note: coverage, cadence, caveats")

class MetricItem(BaseModel):
    metric: str = Field(..., description="Metric or indicator name")
    description: Optional[str] = Field(None, description="What it measures and why it matters")
    datasets: List[DatasetRef] = Field(..., description="Relevant datasets/sources for this metric")

class ValidationBundle(BaseModel):
    problem_validation: List[MetricItem] = Field(..., description="Metrics + datasets validating the problem")
    objective_measurement: List[MetricItem] = Field(..., description="Metrics + datasets measuring progress to objective")
    solution_justification: List[MetricItem] = Field(..., description="Metrics + datasets justifying solution efficacy")

class StakeholderExample(BaseModel):
    name: str = Field(..., description="Entity or role (e.g., Ministry of Health, ACLED, Local Chiefs, Airport Ops)")
    category: Optional[str] = Field(None, description="Type: government, NGO, private, academic, community, etc.")
    role: Optional[str] = Field(None, description="Their function or leverage in the narrative")
    why: str = Field(..., description="Brief reason this stakeholder matters here")

class StakeholderMap(BaseModel):
    primary: List[StakeholderExample] = Field(..., description="Directly responsible/affected")
    secondary: List[StakeholderExample] = Field(..., description="Indirect/influencers")
    end_users_beneficiaries: List[StakeholderExample] = Field(..., description="Problem-bearers & solution beneficiaries")
    external_contextual: List[StakeholderExample] = Field(..., description="International/funders/oversight/market forces")

class SensoryItem(BaseModel):
    cue: str = Field(..., description="Concrete, narrative-specific item (e.g., 'dust plumes over unpaved road', 'bleach odor in triage tent')")
    why: Optional[str] = Field(None, description="Short reason/context for relevance")

class EmbodiedMap(BaseModel):
    eyes:  List[SensoryItem] = Field(..., description="Visible patterns/objects/scenes")
    ears:  List[SensoryItem] = Field(..., description="Sounds/voices/silences")
    hands: List[SensoryItem] = Field(..., description="Build/Touch — artifacts/tools/actions")
    nose:  List[SensoryItem] = Field(..., description="Scents")
    mouth: List[SensoryItem] = Field(..., description="Tastes (literal or metaphorical)")
    skin:  List[SensoryItem] = Field(..., description="Tactile sensations/pressures/surfaces")
    forces: List[SensoryItem] = Field(..., description="Dynamic forces: push/pull, heat/cold, emotional/political/biological")

class PhaseItem(BaseModel):
    name: str = Field(..., description="Phase label, e.g., 'Phase 0: Seed'")
    horizon: str = Field(..., description="Time window, e.g., '0–3 months'")
    goal: Optional[str] = Field(None, description="Headline outcome for this phase")
    milestones: List[str] = Field(..., description="What must be achieved to progress")
    outputs: List[str] = Field(..., description="Artifacts/deliverables produced in this phase")
    indicators: List[str] = Field(..., description="Measurable indicators of progress")
    decision_gates: List[str] = Field(..., description="Go/No-Go criteria")

class RoadmapPlaybook(BaseModel):
    phases: List[PhaseItem] = Field(..., description="Ordered list from concept to full adoption")

class NarrativeClassification(BaseModel):
    archetype: Literal[
        "Technology/Product",
        "Science/Knowledge",
        "Cultural/Artistic",
        "Political/Governance"
    ] = Field(..., description="Selected narrative archetype")
    why: Optional[str] = Field(None, description="Short rationale for this classification")

class PhaseStep(BaseModel):
    name: str = Field(..., description="Phase label, e.g., 'Phase 0: Seed'")
    horizon: str = Field(..., description="Time window, e.g., '0–3 months'")
    goal: Optional[str] = Field(None, description="Headline outcome for this phase")
    milestones: List[str] = Field(..., description="What must be achieved to progress")
    outputs: List[str] = Field(..., description="Artifacts/deliverables produced in this phase")
    indicators: List[str] = Field(..., description="Measurable indicators of progress")
    decision_gates: List[str] = Field(..., description="Go/No-Go criteria")

class ArchetypePlaybook(BaseModel):
    classification: NarrativeClassification
    phases: List[PhaseStep] = Field(..., description="Ordered list (target: 6 phases)")
    north_star: str = Field(..., description="Definition of 100% reality (fully realized & embedded)")

class MetricWatch(BaseModel):
    metric: str = Field(..., description="Name of the metric to monitor")
    rationale: Optional[str] = Field(None, description="Why this metric matters")
    yellow: str = Field(..., description="Caution threshold (human-readable)")
    red: str = Field(..., description="Critical threshold (human-readable)")

class TriggeredAction(BaseModel):
    action: str = Field(..., description="Immediate step when threshold breaches")
    owner: Optional[str] = Field(None, description="Role/team responsible")
    notes: Optional[str] = Field(None, description="Any brief implementation notes")

class MonitoringSystem(BaseModel):
    metrics: List[MetricWatch] = Field(..., description="Metrics with thresholds")
    triggered_actions: List[TriggeredAction] = Field(..., description="What to do on breach")

class FailurePoint(BaseModel):
    name: str = Field(..., description="Risk name")
    symptoms: List[str] = Field(..., description="How the failure manifests")
    impact: str = Field(..., description="Why it matters")
    countermeasures: List[str] = Field(..., description="Prevent/mitigate steps")
    monitoring: MonitoringSystem = Field(..., description="Early-warning monitoring system")

class RiskAssessment(BaseModel):
    failures: List[FailurePoint] = Field(..., description="List of failure points with monitoring")




BODY_WRAPPER_STYLE = """
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 1000px; }
  h1 { font-size: 1.6rem; margin-bottom: .5rem; }
  h2 { font-size: 1.2rem; margin: 1.2rem 0 .6rem; }
  .grid { display: grid; gap: .8rem; }
  .cols-2 { grid-template-columns: 1fr 1fr; }
  .cols-3 { grid-template-columns: 1fr 1fr 1fr; }
  @media (max-width: 800px){ .cols-2, .cols-3 { grid-template-columns: 1fr; } }
  .placeholder { height: 120px; border: 1px dashed #c8c8c8; border-radius: .5rem; display: grid; place-items: center; color: #666; background: #fff; }
  .card { border: 1px solid #d0d0d0; border-radius: .6rem; padding: .9rem 1rem; background: #fff; }
  .card h3 { margin: .2rem 0 .5rem; font-size: 1rem; }
  .thesis { margin: .4rem 0 .3rem; font-style: italic; }
  .muted { opacity: .7; font-size: .9rem; }
  .list-compact li { margin:.25rem 0; }
  .badge { display:inline-block; padding:.2rem .45rem; border:1px solid #d0d0d0; border-radius:.4rem; background:#fff; font-size:.8rem; }
</style>
"""

def compose_input_block(domain: str, dimension: str, seed: str, prototype: str, thesis: Optional[str]) -> str:
    parts = [
        "INPUT (paste your case here; the model will parse it)",
        "Domain",
        domain.strip(),
        "Dimension",
        dimension.strip()
    ]
    if thesis and thesis.strip():
        parts.append(thesis.strip())
    parts.extend([
        "Seed",
        seed.strip(),
        "Prototype",
        prototype.strip()
    ])
    return "\n".join(parts)

def _compose_three_line_seed(seed_str, problem, objective, solution):
    seed_str = (seed_str or "").strip()
    if seed_str:
        # assume caller already formatted the 3 lines
        return seed_str
    # build from fields (tolerates empties)
    parts = []
    if problem:   parts.append(f"A (Problem): {problem.strip()}")
    if objective: parts.append(f"B (Objective): {objective.strip()}")
    if solution:  parts.append(f"Solution (Link): {solution.strip()}")
    return "\n".join(parts)

def _make_artifacts_user_msg(domain, dimension, seed_three_line):
    instruction = (
        "Produce (A) and (B) as described by the System prompt. Focus on practical deployment artifacts first; "
        "then list immediate \"box-of-dirt\" prototypes I can build today (documents, schemas, mock UI, safe simulators). "
        "Follow the System rules about safety and artifact structure. End with exactly 3 next steps the requester can do in 48–72 hours."
    )
    output_format = (
        "OUTPUT FORMAT (how to structure the assistant's reply)\n"
        "Use this structure exactly. Each artifact entry should be short and uniform.\n"
        "(A) Real, deployable artifacts\n"
        "Artifact name — 1–2 sentence description (what it is and why required).\n"
        "Owner: Role/team\n"
        "Notes: 1–2 short constraints/standards/regulatory anchors (if relevant)\n"
        "(Repeat for 8–12 items.)\n"
        "(B) Box-of-dirt artifacts you can build right now\n"
        "Prototype name — 1–2 sentence description (what it produces and who it’s for).\n"
        "Immediate prototypes:\n"
        "file_or_artifact_name.ext — short description (what you'll produce in that file)\n"
        "another_file.ext — short description\n"
        "Owner: Role/team\n"
        "(Repeat for 8–12 items.)\n"
        "Safety guardrails\n"
        "One short paragraph if the domain is safety-sensitive, otherwise one line: \"No special safety issues.\"\n"
        "3 Next steps (48–72 hours)\n"
        "Short, concrete action (e.g., \"Create feedstock_schema.json with fields X,Y,Z.\")\n"
        "Short, concrete action\n"
        "Short, concrete action"
    )
    return (
        f"USER (prompt template)\n"
        f"Domain: {domain}\n"
        f"Dimension: {dimension}\n"
        f"Seed: {seed_three_line}\n"
        f"Instruction to assistant (paste below exactly after the 3 fields above):\n"
        f"{instruction}\n"
        f"{output_format}"
    )

def _provider_from(data: dict) -> str:
    p = (data.get("provider") or "").strip().lower()
    return p if p in {"openai", "openai_web", "xai", "gemini", "deepseek"} else "openai"


def build_user_prompt(artifact_yaml: str, artifacts_md: str | None = None) -> str:
    md_note = ""
    if artifacts_md and artifacts_md.strip():
        md_note = (
            "\n\n----------------------------------------\n"
            "ARTIFACTS (MARKDOWN)\n"
            "(A) Real artifacts and (B) Box-of-dirt prototypes are provided below in Markdown.\n"
            "Parse them into the YAML fields real_artifacts[], box_of_dirt[], and next_steps[].\n"
            "Each real_artifacts item: {title, owner, description, notes?}\n"
            "Each box_of_dirt item: {title, owner, bullets[]}\n"
            "Infer 3 next steps from the final section if present; otherwise omit gracefully.\n\n"
            + artifacts_md.strip()
        )
    return f"""----------------------------------------
ARTIFACT (YAML)
# Replace everything under this line with your content.
{artifact_yaml}
{md_note}

----------------------------------------
RENDERING REQUIREMENTS:
- Map Artifact fields to the layout:
  - Header: header_title, thesis; toolbar with Print and header_pill.
  - Meta: Domain (label, id) and Dimension (label, id, thesis).
  - Seed: problem, objective, solution_link, scope_note.
  - “Real, deployable artifacts”: iterate real_artifacts; each becomes a <div class="card"> with <h3>{{{{title}}}}</h3> and body with Owner, description, notes.
  - “Box-of-dirt”: iterate box_of_dirt; each becomes a <div class="card"> with bullets rendered as chip-styled list items.
  - Safety guardrails: paragraph(s) from safety_guardrails emphasized in a warning/info panel.
  - Next steps: next_steps_title and a list of checkbox tasks from next_steps.
- Use the exact boilerplate CSS + JS below. You may only change text nodes and repeated sections; keep class names and behavior.

----------------------------------------
HTML BOILERPLATE TO USE:
(Embed this structure and fill with the Artifact’s data. Keep styles/scripts the same; only inject content. No expand/collapse controls.)

{HTML_BOILERPLATE}

----------------------------------------
WHEN YOU’RE READY:
1) Replace the sample YAML under ARTIFACT with your content.
2) Generate exactly one ```html code block with the final page.
"""

def extract_html_codeblock(text: str) -> Optional[str]:
    """
    Extracts content inside the first ```html ... ``` fenced block.
    Returns the inner HTML or None if not found.
    """
    m = re.search(r"```html\s*(.+?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else None

def generate_seed_html(artifact_yaml: str, model: Optional[str] = None, temperature: float = 0.1) -> str:
    """
    Calls the LLM with the Fantasiagenesis HTML generator system prompt and returns raw HTML.
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    chosen_model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
        {"role": "user", "content": build_user_prompt(artifact_yaml)},
    ]
    try:
        resp = client.chat.completions.create(
            model="gpt-5-mini-2025-08-07",
            messages=messages,
        )
        txt = resp.choices[0].message.content or ""
        html = extract_html_codeblock(txt) or txt  # fall back if model didn't fence (shouldn't happen)
        if not html.strip().startswith("<!DOCTYPE html"):
            # Guard against accidental non-HTML responses
            raise ValueError("Model did not return an HTML document.")
        return html
    except Exception as e:
        raise RuntimeError(f"LLM generation failed: {e}") from e


def get_or_create_narrative(con, domain_title: str) -> int:
    row = con.execute("SELECT id FROM narratives WHERE title = ?", (domain_title,)).fetchone()
    if row:
        return row["id"]
    cur = con.execute("INSERT INTO narratives (title) VALUES (?)", (domain_title,))
    return cur.lastrowid

def upsert_dimension(con, narrative_id: int, name: str, thesis: str, targets: list, provider: str | None = None) -> int:
    row = con.execute(
        "SELECT id FROM narrative_dimensions WHERE narrative_id=? AND title=?",
        (narrative_id, name)
    ).fetchone()
    targets_json = json.dumps(targets or [])
    if row:
        # When updating, also refresh provider if supplied
        if provider:
            con.execute(
                "UPDATE narrative_dimensions SET description=?, targets_json=?, provider=? WHERE id=?",
                (thesis, targets_json, provider, row["id"])
            )
        else:
            con.execute(
                "UPDATE narrative_dimensions SET description=?, targets_json=? WHERE id=?",
                (thesis, targets_json, row["id"])
            )
        return row["id"]
    cur = con.execute(
        "INSERT INTO narrative_dimensions (narrative_id, title, description, targets_json, provider) VALUES (?,?,?,?,?)",
        (narrative_id, name, thesis, targets_json, provider)
    )
    return cur.lastrowid


def insert_seed(con, dimension_id: int, problem: str, objective: str, solution: str, provider: str | None = None):
    # dedup via unique index; ignore if exact duplicate (provider is not part of the unique key)
    con.execute("""
        INSERT OR IGNORE INTO narrative_seeds (dimension_id, problem, objective, solution, provider)
        VALUES (?,?,?,?,?)
    """, (dimension_id, problem, objective, solution, provider))


def find_dimension_id(con, narrative_id: int, dim_name: str):
    row = con.execute(
        "SELECT id FROM narrative_dimensions WHERE narrative_id=? AND title=?",
        (narrative_id, dim_name)
    ).fetchone()
    return row["id"] if row else None

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _prototype_user_msg(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    return (
        f"Narrative Domain: {domain}\n"
        f"Narrative Dimension: {dimension}\n"
        f"Narrative Seed:\n"
        f"A (Problem): {problem}\n"
        f"B (Objective): {objective}\n"
        f"Solution (Link): {solution}\n\n"
        "Construct a narrative prototype sketch following the format defined in the system message."
    )

def _artifact_next_version(con, seed_id: int, kind: str = "box_of_dirt") -> int:
    row = con.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM seed_artifacts WHERE seed_id=? AND kind=?",
        (seed_id, kind)
    ).fetchone()
    return int(row["v"]) + 1

def _seed_exists(con, seed_id: int) -> bool:
    r = con.execute("SELECT 1 FROM narrative_seeds WHERE id=? LIMIT 1", (seed_id,)).fetchone()
    return bool(r)

def _safe_filename(name: str) -> str:
    # strip path, normalize, and collapse bad chars to '-'
    base = os.path.basename(name).strip()
    base = SAFE_NAME.sub("-", base)
    # enforce .svg
    if not base.lower().endswith(".svg"):
        base = base + ".svg"
    return base

def _suffix_if_exists(p: Path) -> Path:
    if not p.exists():
        return p
    stem, ext = p.stem, p.suffix
    i = 1
    while True:
        candidate = p.with_name(f"{stem}.{i}{ext}")
        if not candidate.exists():
            return candidate
        i += 1

def _validation_user_message(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    from prompts import VALIDATION_USER_TEMPLATE
    return VALIDATION_USER_TEMPLATE.format(
        domain=domain, dimension=dimension, problem=problem, objective=objective, solution=solution
    )

def _stakeholders_user_message(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    from prompts import STAKEHOLDERS_USER_TEMPLATE
    return STAKEHOLDERS_USER_TEMPLATE.format(
        domain=domain, dimension=dimension, problem=problem, objective=objective, solution=solution
    )

def _embodied_user_message(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    from prompts import EMBODIED_USER_TEMPLATE
    return EMBODIED_USER_TEMPLATE.format(
        domain=domain, dimension=dimension, problem=problem, objective=objective, solution=solution
    )

def _roadmap_user_message(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    from prompts import ROADMAP_USER_TEMPLATE
    return ROADMAP_USER_TEMPLATE.format(
        domain=domain, dimension=dimension, problem=problem, objective=objective, solution=solution
    )

def _archetype_playbook_user_message(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    from prompts import ARCHETYPE_PLAYBOOK_USER_TEMPLATE
    return ARCHETYPE_PLAYBOOK_USER_TEMPLATE.format(
        domain=domain, dimension=dimension, problem=problem, objective=objective, solution=solution
    )

def _risks_user_message(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    from prompts import RISKS_USER_TEMPLATE
    return RISKS_USER_TEMPLATE.format(
        domain=domain, dimension=dimension, problem=problem, objective=objective, solution=solution
    )




# ---------- NEW: LLM adapters (OpenAI + xAI + Gemini + Deepseek + OpenAI websearch) ----------
# ---------- NEW: OpenAI with web_search tool ----------
def llm_generate_dimensions_openai_web(domain: str, n: int | None):
    """
    Use OpenAI 'responses.create' with the web_search tool to generate NarrativeDimensions JSON.
    """
    client = _get_llm()
    count_hint = f" Generate exactly {int(n)} items." if isinstance(n, int) and 1 <= n <= 12 else ""
    usr_msg = f"Create narrative dimensions for the domain of {domain}.{count_hint}"

    # Ask for strict JSON that matches the Pydantic schema
    sys_prompt = (
        "You are a structured generator. Use web search as needed to ground the space. "
        "Return ONLY a JSON document that strictly matches the provided schema. No prose, no markdown fences.\n\n"
        "Schema name: NarrativeDimensions\n"
        "fields:\n"
        "- dimensions: list of objects with fields {name:str, thesis:str, targets:list[str]}\n"
        "Constraints: 5–8 items unless explicitly overridden."
    )

    # Include your existing guidance for what a 'dimension' looks like
    user_prompt = f"{DIM_SYS_MSG}\n\n{usr_msg}\n\nReturn JSON only."

    resp = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5"),
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw = resp.output_text or ""
    json_text = _extract_json_text(raw)
    try:
        parsed = _pydantic_from_json(NarrativeDimensions, json_text)
    except Exception:
        class _Shim: pass
        shim = _Shim()
        shim.output_parsed = None
        shim.output_text = raw
        return shim

    class _ParsedResp:
        def __init__(self, parsed_obj, raw_text):
            self.output_parsed = parsed_obj
            self.output_text = raw_text
    return _ParsedResp(parsed, raw)


def llm_generate_seeds_openai_web(domain: str, dimension: str, description: str, targets: List[str]):
    """
    Use OpenAI 'responses.create' with the web_search tool to generate NarrativeSeeds JSON.
    """
    client = _get_llm()
    usr_msg = (
        f"Domain: {domain}\n"
        f"Dimension: {dimension}\n"
        f"Description: {description}\n"
        f"Narrative Targets: {targets if targets else 'None provided'}\n\n"
        "Create A→B narrative seeds in this dimension."
    )

    sys_prompt = (
        "You are a structured generator. Use web search as needed to ground claims and examples. "
        "Return ONLY a JSON document that strictly matches the provided schema. No prose, no markdown fences.\n\n"
        "Schema name: NarrativeSeeds\n"
        "fields:\n"
        "- seeds: list of objects with fields {problem:str, objective:str, solution:str}\n"
        "Constraints: 3–5 seeds."
    )

    user_prompt = f"{SEED_SYS_MSG}\n\n{usr_msg}\n\nReturn JSON only."

    resp = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5"),
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw = resp.output_text or ""
    json_text = _extract_json_text(raw)
    try:
        parsed = _pydantic_from_json(NarrativeSeeds, json_text)
    except Exception:
        class _Shim: pass
        shim = _Shim()
        shim.output_parsed = None
        shim.output_text = raw
        return shim

    class _ParsedResp:
        def __init__(self, parsed_obj, raw_text):
            self.output_parsed = parsed_obj
            self.output_text = raw_text
    return _ParsedResp(parsed, raw)

def llm_generate_dimensions_deepseek(domain: str, n: int | None):
    client = get_deepseek_client()
    count_hint = f" Generate exactly {int(n)} items." if isinstance(n, int) and 1 <= n <= 12 else ""
    usr_msg = f"Create narrative dimensions for the domain of {domain}.{count_hint}"

    # Build a strict system message asking for JSON only
    sys_prompt = (
        "You are a structured generator. "
        "Return ONLY a JSON document that strictly matches the provided schema. "
        "No prose, no markdown fences unless asked; just JSON.\n\n"
        "Schema name: NarrativeDimensions\n"
        "fields:\n"
        "- dimensions: list of objects with fields {name:str, thesis:str, targets:list[str]}\n"
        "Constraints: 5–8 items unless explicitly overridden."
    )

    user_prompt = f"{DIM_SYS_MSG}\n\n{usr_msg}\n\nReturn JSON only."

    completion = client.chat.completions.create(
        model=_deepseek_model(),
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        # If DeepSeek supports response_format, you can try:
        # response_format={"type": "json_object"},
    )
    content = completion.choices[0].message.content or ""
    json_text = _extract_json_text(content)

    try:
        parsed = _pydantic_from_json(NarrativeDimensions, json_text)
    except Exception as e:
        # surface raw text for debugging by caller (routes)
        class _Shim: pass
        shim = _Shim()
        shim.output_parsed = None
        shim.output_text = content
        return shim  # emulate "parsed_resp" object with fields used by routes

    # Return an object that mimics OpenAI .responses.parse shape used in routes
    class _ParsedResp:
        def __init__(self, parsed_obj, raw_text):
            self.output_parsed = parsed_obj
            self.output_text = raw_text
    return _ParsedResp(parsed, json_text)


def llm_generate_seeds_deepseek(domain: str, dimension: str, description: str, targets: list[str]):
    client = get_deepseek_client()
    usr_msg = (
        f"Domain: {domain}\n"
        f"Dimension: {dimension}\n"
        f"Description: {description}\n"
        f"Narrative Targets: {targets if targets else 'None provided'}\n\n"
        "Create A→B narrative seeds in this dimension."
    )

    sys_prompt = (
        "You are a structured generator. "
        "Return ONLY a JSON document that strictly matches the provided schema. "
        "No prose, no markdown fences; just JSON.\n\n"
        "Schema name: NarrativeSeeds\n"
        "fields:\n"
        "- seeds: list of objects with fields {problem:str, objective:str, solution:str}\n"
        "Constraints: 3–5 seeds."
    )
    user_prompt = f"{SEED_SYS_MSG}\n\n{usr_msg}\n\nReturn JSON only."

    completion = client.chat.completions.create(
        model=_deepseek_model(),
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        # response_format={"type": "json_object"},  # optional if supported
    )
    content = completion.choices[0].message.content or ""
    json_text = _extract_json_text(content)

    try:
        parsed = _pydantic_from_json(NarrativeSeeds, json_text)
    except Exception:
        class _Shim: pass
        shim = _Shim()
        shim.output_parsed = None
        shim.output_text = content
        return shim

    class _ParsedResp:
        def __init__(self, parsed_obj, raw_text):
            self.output_parsed = parsed_obj
            self.output_text = raw_text
    return _ParsedResp(parsed, json_text)


def _gemini_model():
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

def llm_generate_dimensions_gemini(domain: str, n: int | None):
    client = get_gemini_client()
    count_hint = f" Generate exactly {int(n)} items." if isinstance(n, int) and 1 <= n <= 12 else ""
    usr_msg = f"Create narrative dimensions for the domain of {domain}.{count_hint}"
    # For Gemini, we pass a single prompt string; include your DIM_SYS_MSG
    prompt = f"{DIM_SYS_MSG}\n\n{usr_msg}"
    resp = client.models.generate_content(
        model=_gemini_model(),
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": NarrativeDimensions,  # Pydantic schema
        },
    )
    # resp.parsed -> NarrativeDimensions | list[NarrativeDimensions] depending on schema
    parsed = resp.parsed
    # If the SDK returns a list (rare when schema is singular), normalize:
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], NarrativeDimensions):
        parsed = parsed[0]
    return resp, parsed


def llm_generate_seeds_gemini(domain: str, dimension: str, description: str, targets: List[str]):
    client = get_gemini_client()
    usr_msg = (
        f"Domain: {domain}\n"
        f"Dimension: {dimension}\n"
        f"Description: {description}\n"
        f"Narrative Targets: {targets if targets else 'None provided'}\n\n"
        "Create A→B narrative seeds in this dimension."
    )
    prompt = f"{SEED_SYS_MSG}\n\n{usr_msg}"
    resp = client.models.generate_content(
        model=_gemini_model(),
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": NarrativeSeeds,  # Pydantic schema
        },
    )
    parsed = resp.parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], NarrativeSeeds):
        parsed = parsed[0]
    return resp, parsed



def llm_generate_dimensions_openai(domain: str, n: int | None):
    count_hint = f" Generate exactly {int(n)} items." if isinstance(n, int) and 1 <= n <= 12 else ""
    usr_msg = f"Create narrative dimensions for the domain of {domain}.{count_hint}"
    parsed_resp = client.responses.parse(
        model=os.getenv("OPENAI_MODEL", "gpt-5"),
        input=[
            {"role": "system", "content": DIM_SYS_MSG},
            {"role": "user", "content": usr_msg},
        ],
        text_format=NarrativeDimensions,
    )
    return parsed_resp  # keep raw + parsed for uniform handling

def llm_generate_dimensions_xai(domain: str, n: int | None):
    xai = get_xai_client()
    model = os.getenv("XAI_MODEL", "grok-4")
    chat = xai.chat.create(model=model)
    count_hint = f" Generate exactly {int(n)} items." if isinstance(n, int) and 1 <= n <= 12 else ""
    usr_msg = f"Create narrative dimensions for the domain of {domain}.{count_hint}"
    chat.append(xai_system(DIM_SYS_MSG))
    chat.append(xai_user(usr_msg))
    # xAI returns: (response, parsed_object or None)
    response, parsed = chat.parse(NarrativeDimensions)
    return response, parsed

def llm_generate_seeds_openai(domain: str, dimension: str, description: str, targets: List[str]):
    usr_msg = (
        f"Domain: {domain}\n"
        f"Dimension: {dimension}\n"
        f"Description: {description}\n"
        f"Narrative Targets: {targets if targets else 'None provided'}\n\n"
        "Create A→B narrative seeds in this dimension."
    )
    parsed_resp = client.responses.parse(
        model=os.getenv("OPENAI_MODEL", "gpt-5"),
        input=[
            {"role": "system", "content": SEED_SYS_MSG},
            {"role": "user", "content": usr_msg},
        ],
        text_format=NarrativeSeeds,
    )
    return parsed_resp

def llm_generate_seeds_xai(domain: str, dimension: str, description: str, targets: List[str]):
    xai = get_xai_client()
    model = os.getenv("XAI_MODEL", "grok-4")
    chat = xai.chat.create(model=model)
    usr_msg = (
        f"Domain: {domain}\n"
        f"Dimension: {dimension}\n"
        f"Description: {description}\n"
        f"Narrative Targets: {targets if targets else 'None provided'}\n\n"
        "Create A→B narrative seeds in this dimension."
    )
    chat.append(xai_system(SEED_SYS_MSG))
    chat.append(xai_user(usr_msg))
    response, parsed = chat.parse(NarrativeSeeds)
    return response, parsed



# --- routes ---
@app.post("/api/narrative-dimensions")
def generate_narrative_dimensions():
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or "").strip()
    n = data.get("n")
    provider = _provider_from(data)  # "openai" | "xai" (your helper)

    if not domain:
        return jsonify({"error": "Missing 'domain'"}), 400

    try:
        if provider == "xai":
            resp, parsed = llm_generate_dimensions_xai(domain, n)
            model_name = os.getenv("XAI_MODEL", "grok-4")
            raw_text = getattr(resp, "content", None)
        elif provider == "gemini":
            resp, parsed = llm_generate_dimensions_gemini(domain, n)
            model_name = _gemini_model()
            raw_text = getattr(resp, "text", None)
        elif provider == "deepseek":
            parsed_resp = llm_generate_dimensions_deepseek(domain, n)
            parsed = parsed_resp.output_parsed
            model_name = _deepseek_model()
            raw_text = parsed_resp.output_text
        elif provider == "openai_web":
            parsed_resp = llm_generate_dimensions_openai_web(domain, n)
            parsed = parsed_resp.output_parsed
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            raw_text = parsed_resp.output_text
        else:
            parsed_resp = llm_generate_dimensions_openai(domain, n)
            parsed = parsed_resp.output_parsed
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            raw_text = parsed_resp.output_text

        if parsed is not None:
            dims = parsed.model_dump()["dimensions"]
            with closing(connect()) as con, con:
                narrative_id = get_or_create_narrative(con, domain)
                saved = []
                for d in dims:
                    dim_id = upsert_dimension(
                        con,
                        narrative_id=narrative_id,
                        name=d["name"],
                        thesis=d["thesis"],
                        targets=d.get("targets") or [],
                        provider=provider
                    )
                    saved.append({**d, "id": dim_id, "provider": provider})

            return jsonify({
                "domain": domain,
                "provider": provider,
                "model": model_name,
                "dimensions": saved
            }), 200

        return jsonify({
            "domain": domain,
            "provider": provider,
            "model": model_name,
            "raw": raw_text,
            "note": "Parsing returned None; inspect 'raw'.",
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


# ---------- UPDATED: /api/narrative-seeds with provider switch ----------
@app.post("/api/narrative-seeds")
def generate_narrative_seeds():
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    description = (data.get("description") or "").strip()
    targets = data.get("targets") or []
    provider = _provider_from(data)

    if not (domain and dimension and description):
        return jsonify({"error": "Missing required fields: domain, dimension, description"}), 400

    try:
        if provider == "xai":
            resp, parsed = llm_generate_seeds_xai(domain, dimension, description, targets)
            model_name = os.getenv("XAI_MODEL", "grok-4")
            raw_text = getattr(resp, "content", None)
        elif provider == "gemini":
            resp, parsed = llm_generate_seeds_gemini(domain, dimension, description, targets)
            model_name = _gemini_model()
            raw_text = getattr(resp, "text", None)
        elif provider == "deepseek":
            parsed_resp = llm_generate_seeds_deepseek(domain, dimension, description, targets)
            parsed = parsed_resp.output_parsed
            model_name = _deepseek_model()
            raw_text = parsed_resp.output_text
        elif provider == "openai_web":
            parsed_resp = llm_generate_seeds_openai_web(domain, dimension, description, targets)
            parsed = parsed_resp.output_parsed
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            raw_text = parsed_resp.output_text
        else:
            parsed_resp = llm_generate_seeds_openai(domain, dimension, description, targets)
            parsed = parsed_resp.output_parsed
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            raw_text = parsed_resp.output_text

        if not parsed:
            return jsonify({
                "domain": domain,
                "dimension": dimension,
                "provider": provider,
                "model": model_name,
                "raw": raw_text,
                "note": "Parsing failed, see raw output."
            }), 200

        seeds = parsed.model_dump()["seeds"]

        with closing(connect()) as con, con:
            narrative_id = get_or_create_narrative(con, domain)
            dim_id = upsert_dimension(
                con,
                narrative_id=narrative_id,
                name=dimension,
                thesis=description,
                targets=targets,
                provider=provider
            )
            for s in seeds:
                insert_seed(
                    con,
                    dimension_id=dim_id,
                    problem=(s.get("problem") or "").strip(),
                    objective=(s.get("objective") or "").strip(),
                    solution=(s.get("solution") or "").strip(),
                    provider=provider
                )

            rows = con.execute("""
                SELECT id, problem, objective, solution, provider, created_at
                FROM narrative_seeds
                WHERE dimension_id=?
                ORDER BY id DESC
                LIMIT 50
            """, (dim_id,)).fetchall()

        seeds_out = [
            {
                "id": r["id"],
                "problem": r["problem"],
                "objective": r["objective"],
                "solution": r["solution"],
                "provider": r["provider"],
                "created_at": r["created_at"]
            } for r in rows
        ]

        return jsonify({
            "domain": domain,
            "dimension": dimension,
            "dimension_id": dim_id,
            "provider": provider,
            "model": model_name,
            "seeds": seeds_out
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500




def _query_all(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON;")
    try:
        cur = con.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        con.close()


def _pick_random_seed_ids(n: int):
    rows = _query_all("SELECT id FROM narrative_seeds ORDER BY RANDOM() LIMIT ?", (n,))
    # adapt to your row factory
    return [r['id'] if isinstance(r, dict) else r[0] for r in rows]


def _seed_context(seed_id: int):
    """
    Fetch domain/dimension + seed fields and compose the canonical A/B/Link block.
    Uses _query_all to avoid relying on a missing _query_one helper.
    """
    rows = _query_all("""
        SELECT s.id AS seed_id, s.problem, s.objective, s.solution,
               d.id AS dim_id, d.title AS dim_title, d.description AS dim_desc,
               n.id AS narrative_id, n.title AS narrative_title
        FROM narrative_seeds s
        JOIN narrative_dimensions d ON d.id = s.dimension_id
        JOIN narratives n ON n.id = d.narrative_id
        WHERE s.id = ?
        LIMIT 1
    """, (seed_id,))
    if not rows:
        return None
    row = rows[0]

    # row may be dict-like or tuple-like; normalize
    if isinstance(row, dict):
        problem   = (row.get("problem") or "").strip()
        objective = (row.get("objective") or "").strip()
        solution  = (row.get("solution") or "").strip()
        ctx = {
            "seed_id": row["seed_id"],
            "domain": row["narrative_title"],
            "dimension": row["dim_title"],
            "thesis": row.get("dim_desc") or "",
        }
    else:
        # tuple fallback (index order must match SELECT)
        problem   = (row[1] or "").strip()
        objective = (row[2] or "").strip()
        solution  = (row[3] or "").strip()
        ctx = {
            "seed_id": row[0],
            "domain": row[8],
            "dimension": row[5],
            "thesis": row[6] or "",
        }

    ctx["seed_three"] = "\n".join([
        f"A (Problem): {problem}",
        f"B (Objective): {objective}",
        f"Solution (Link): {solution}",
    ]).strip()
    return ctx


def _bulk_worker(job_id: str, seed_ids: list[int]):
    with BULK_JOBS_LOCK:
        job = BULK_JOBS.get(job_id)
        if not job:
            return
        job["started_at"] = datetime.utcnow().isoformat()

    ok = fail = 0
    items = []

    for sid in seed_ids:
        status = "started"
        error = None
        version = None

        try:
            ctx = _seed_context(sid)
            if not ctx:
                raise RuntimeError(f"Seed {sid} not found")

            # 1) Generate artifacts (Markdown) from your backend
            #    POST /api/box-of-dirt/artifacts  (Accept: text/markdown)
            art_payload = {
                "domain": ctx["domain"],
                "dimension": ctx["dimension"],
                "seed": ctx["seed_three"],
            }
            sc_md, md = _call_internal('POST', '/api/box-of-dirt/artifacts',
                                       json=art_payload, accept='text/markdown')
            if sc_md != 200 or not md or not md.strip():
                raise RuntimeError(f"artifacts failed ({sc_md})")

            # 2) Render full HTML page from artifacts Markdown
            #    POST /api/render-artifact
            #    We feed ONLY the markdown as requested; no hand-built YAML.
            sc_html, out = _call_internal('POST', '/api/render-artifact',
                                          json={"artifacts_markdown": md},
                                          accept='application/json')
            if sc_html != 200 or not isinstance(out, dict) or not (out.get("html") or "").strip():
                raise RuntimeError(f"render-artifact failed ({sc_html})")
            html = out["html"]

            # 3) Publish to this seed’s box as a full page
            pub_payload = {
                "html": html,
                "doc_format": "full",
                "title": None,
                "publish": True
            }
            sc_pub, pub_out = _call_internal('POST', f"/api/seeds/{sid}/box",
                                             json=pub_payload, accept='application/json')
            if sc_pub != 200 or not isinstance(pub_out, dict):
                raise RuntimeError(f"publish failed ({sc_pub})")
            version = pub_out.get("version")

            status = "ok"
            ok += 1

        except Exception as e:
            status = "error"
            error = str(e)
            fail += 1

        items.append({"seed_id": sid, "status": status, "error": error, "version": version})

        with BULK_JOBS_LOCK:
            j = BULK_JOBS.get(job_id)
            if j:
                j["items"] = items
                j["ok"] = ok
                j["fail"] = fail

    with BULK_JOBS_LOCK:
        j = BULK_JOBS.get(job_id)
        if j:
            j["done"] = True
            j["finished_at"] = datetime.utcnow().isoformat()


@app.get("/api/narratives")
def api_narratives():
    rows = _query_all("""
        SELECT id, title, created_at
        FROM narratives
        WHERE COALESCE(title,'') <> ''
        ORDER BY COALESCE(created_at, '') DESC, id DESC
    """)
    return jsonify(rows)


@app.get("/api/narratives/<int:narrative_id>/dimensions")
def api_narrative_dimensions(narrative_id: int):
    exists = _query_all("SELECT 1 AS ok FROM narratives WHERE id = ? LIMIT 1", (narrative_id,))
    if not exists:
        abort(404, description="Narrative not found")

    dims = _query_all("""
        SELECT id, narrative_id, title, description, provider, created_at
        FROM narrative_dimensions
        WHERE narrative_id = ?
        ORDER BY id ASC
    """, (narrative_id,))
    return jsonify(dims)


@app.get("/api/dimensions/<int:dimension_id>/seeds")
def api_dimension_seeds(dimension_id: int):
    rows = _query_all("""
        SELECT id, problem, objective, solution, provider, created_at
        FROM narrative_seeds
        WHERE dimension_id = ?
        ORDER BY id DESC
        LIMIT 200
    """, (dimension_id,))
    return jsonify({"ok": True, "dimension_id": dimension_id, "seeds": rows})



@app.post("/api/narrative-prototype")
def api_narrative_prototype():
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()

    # Accept either flat fields or a nested seed object
    seed = data.get("seed") or {}
    problem = (data.get("problem") or seed.get("problem") or "").strip()
    objective = (data.get("objective") or seed.get("objective") or "").strip()
    solution = (data.get("solution") or seed.get("solution") or "").strip()

    # Validation
    missing = []
    if not domain: missing.append("domain")
    if not dimension: missing.append("dimension")
    if not problem: missing.append("problem")
    if not objective: missing.append("objective")
    if not solution: missing.append("solution")
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    usr_msg = _prototype_user_msg(domain, dimension, problem, objective, solution)

    try:
        parsed_resp = client.responses.parse(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            input=[
                {"role": "system", "content": PROTOTYPE_SYS_MSG},
                {"role": "user", "content": usr_msg},
            ],
            text_format=NarrativePrototype,
        )

        parsed = parsed_resp.output_parsed  # -> NarrativePrototype | None
        if not parsed:
            # Helpful debug path if parsing fails
            return jsonify({
                "domain": domain,
                "dimension": dimension,
                "raw": parsed_resp.output_text,
                "note": "Parsing failed; 'raw' contains the unparsed model output."
            }), 200

        proto = parsed.model_dump()
        return jsonify({
            "ok": True,
            "domain": domain,
            "dimension": dimension,
            "prototype": proto
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.get("/api/seeds/<int:seed_id>/box")
def api_get_seed_box(seed_id: int):
    """
    Returns the latest published artifact for this seed (kind='box_of_dirt').
    Optional query params:
      - kind: override artifact kind (default box_of_dirt)
      - version: fetch a specific version (int)
      - draft=1: if set, prefer latest version even if not published
    """
    kind = (request.args.get("kind") or "box_of_dirt").strip()
    version = request.args.get("version")
    draft = request.args.get("draft") in ("1", "true", "yes")

    with closing(connect()) as con:
        if not _seed_exists(con, seed_id):
            abort(404, description="Seed not found")

        params = [seed_id, kind]
        if version is not None:
            row = con.execute("""
                SELECT id, seed_id, kind, title, html, doc_format, version, is_published, checksum,
                       created_at, updated_at
                FROM seed_artifacts
                WHERE seed_id=? AND kind=? AND version=?
                LIMIT 1
            """, (seed_id, kind, int(version))).fetchone()
        else:
            if draft:
                row = con.execute("""
                    SELECT id, seed_id, kind, title, html, doc_format, version, is_published, checksum,
                           created_at, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=?
                    ORDER BY version DESC
                    LIMIT 1
                """, params).fetchone()
            else:
                row = con.execute("""
                    SELECT id, seed_id, kind, title, html, doc_format, version, is_published, checksum,
                           created_at, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=? AND is_published=1
                    ORDER BY version DESC
                    LIMIT 1
                """, params).fetchone()

        if not row:
            abort(404, description="No artifact found for this seed/kind")

        payload = dict(row)

        # Simple ETag support for cache friendliness
        etag = payload.get("checksum") or ""
        if etag and request.headers.get("If-None-Match") == etag:
            return ("", 304, {"ETag": etag})

        resp = jsonify(payload)
        if etag:
            resp.headers["ETag"] = etag
        return resp


@app.post("/api/seeds/<int:seed_id>/box")
def api_create_seed_box(seed_id: int):
    """
    Create a new artifact version for a seed.
    Body JSON:
      - html (str, required)
      - title (str, optional)
      - kind (str, default 'box_of_dirt')
      - doc_format ('full'|'body', default matches table default)
      - publish (bool, default false)  # set is_published=1 on insert
    """
    data = request.get_json(silent=True) or {}
    html = (data.get("html") or "").strip()
    if not html:
        return jsonify({"error": "html is required"}), 400

    kind = (data.get("kind") or "box_of_dirt").strip()
    title = (data.get("title") or "").strip() or None
    doc_format = (data.get("doc_format") or "full").strip()
    if doc_format not in ("full", "body"):
        return jsonify({"error": "doc_format must be 'full' or 'body'"}), 400
    publish = bool(data.get("publish"))

    checksum = hashlib.sha256(html.encode("utf-8")).hexdigest()

    with closing(connect()) as con, con:
        if not _seed_exists(con, seed_id):
            return jsonify({"error": "Seed not found"}), 404

        version = _artifact_next_version(con, seed_id, kind)
        con.execute("""
            INSERT INTO seed_artifacts (seed_id, kind, title, html, doc_format, version, is_published, checksum, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (seed_id, kind, title, html, doc_format, version, 1 if publish else 0, checksum))

        new_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return jsonify({
        "ok": True,
        "artifact_id": new_id,
        "seed_id": seed_id,
        "kind": kind,
        "version": version,
        "is_published": bool(publish),
        "checksum": checksum
    }), 201

@app.post("/api/seeds/<int:seed_id>/box/<int:version>/publish")
def api_publish_seed_box(seed_id: int, version: int):
    data = request.get_json(silent=True) or {}
    publish = bool(data.get("publish", True))  # default: publish
    kind = (data.get("kind") or "box_of_dirt").strip()

    with closing(connect()) as con, con:
        # Ensure the artifact exists
        row = con.execute("""
            SELECT id FROM seed_artifacts WHERE seed_id=? AND kind=? AND version=? LIMIT 1
        """, (seed_id, kind, version)).fetchone()
        if not row:
            return jsonify({"error": "Artifact version not found"}), 404

        con.execute("""
            UPDATE seed_artifacts
            SET is_published=?, updated_at=datetime('now')
            WHERE seed_id=? AND kind=? AND version=?
        """, (1 if publish else 0, seed_id, kind, version))

    return jsonify({"ok": True, "seed_id": seed_id, "version": version, "is_published": publish})

@app.get("/api/seeds/<int:seed_id>/boxes")
def api_list_seed_boxes(seed_id: int):
    kind = (request.query_string.decode() and request.args.get("kind")) or "box_of_dirt"
    rows = _query_all("""
        SELECT id, seed_id, kind, title, version, is_published, doc_format, checksum, created_at, updated_at
        FROM seed_artifacts
        WHERE seed_id = ? AND kind = ?
        ORDER BY version DESC
    """, (seed_id, kind))
    return jsonify(rows)


@app.get("/boxes/<int:seed_id>")
def public_box(seed_id: int):
    """
    Public, isolated viewer for a seed's Box of Dirt.
    Query params:
      - draft=1  -> show latest version regardless of publish state
      - version=INT -> fetch specific version
      - kind=... -> artifact kind (default 'box_of_dirt')
    """
    want_draft = request.args.get("draft") in ("1", "true", "yes")
    version = request.args.get("version")
    kind = (request.args.get("kind") or "box_of_dirt").strip()

    with closing(connect()) as con:
        # sanity: seed exists
        s = con.execute("SELECT 1 FROM narrative_seeds WHERE id=? LIMIT 1", (seed_id,)).fetchone()
        if not s:
            abort(404, description="Seed not found.")

        if version:
            row = con.execute("""
                SELECT title, html, doc_format, version, is_published, checksum, updated_at
                FROM seed_artifacts
                WHERE seed_id=? AND kind=? AND version=?
                LIMIT 1
            """, (seed_id, kind, int(version))).fetchone()
        else:
            if want_draft:
                row = con.execute("""
                    SELECT title, html, doc_format, version, is_published, checksum, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=?
                    ORDER BY version DESC
                    LIMIT 1
                """, (seed_id, kind)).fetchone()
            else:
                row = con.execute("""
                    SELECT title, html, doc_format, version, is_published, checksum, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=? AND is_published=1
                    ORDER BY version DESC
                    LIMIT 1
                """, (seed_id, kind)).fetchone()

    if not row:
        msg = "No published artifact found for this seed." if not want_draft else "No artifact found for this seed."
        abort(404, description=msg)

    html = (row["html"] or "")
    fmt = (row["doc_format"] or "full").lower()
    looks_full = bool(re.search(r"<!DOCTYPE|<html[^>]*>", html, re.I) or fmt == "full")

    # ETag for caching
    etag = row["checksum"] or None
    if etag and request.headers.get("If-None-Match") == etag:
        return ("", 304, {"ETag": etag})

    if looks_full:
        resp = Response(html, mimetype="text/html; charset=utf-8")
    else:
        wrapped = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{row['title'] or 'Box of Dirt'}</title>
  {BODY_WRAPPER_STYLE}
</head>
<body>
{html}
</body>
</html>"""
        resp = Response(wrapped, mimetype="text/html; charset=utf-8")

    if etag:
        resp.headers["ETag"] = etag
    return resp


@app.route("/api/seeds/<int:seed_id>/manifests", methods=["GET"])
def list_or_get_manifest(seed_id: int):
    """
    GET /api/seeds/<seed_id>/manifests
      ?version=N            -> exact version
      ?published=1          -> latest published
      (no params)           -> latest by (is_published DESC, version DESC)
    """
    version = request.args.get("version", type=int)
    published = request.args.get("published", type=int)

    with closing(connect()) as con:
        if version is not None:
            row = con.execute("""
                SELECT * FROM seed_manifests
                WHERE seed_id = ? AND version = ?
                LIMIT 1
            """, (seed_id, version)).fetchone()
        elif published:
            row = con.execute("""
                SELECT * FROM seed_manifests
                WHERE seed_id = ? AND is_published = 1
                ORDER BY version DESC
                LIMIT 1
            """, (seed_id,)).fetchone()
        else:
            row = con.execute("""
                SELECT * FROM seed_manifests
                WHERE seed_id = ?
                ORDER BY is_published DESC, version DESC
                LIMIT 1
            """, (seed_id,)).fetchone()

        if not row:
            return jsonify({"error":"manifest_not_found","seed_id":seed_id}), 404

        out = dict(row)
        # parse panels_json to object for convenience
        out["panels"] = json.loads(out["panels_json"]).get("panels", [])
        return jsonify({
            "seed_id": out["seed_id"],
            "version": out["version"],
            "updated": out["updated"],
            "is_published": bool(out["is_published"]),
            "checksum": out["checksum"],
            "panels": out["panels"]
        })

@app.route("/api/seeds/<int:seed_id>/manifests", methods=["POST"])
def upsert_manifest(seed_id: int):
    """
    POST body (example):
    {
      "version": 1,
      "updated": "2025-09-20T17:21:00Z",
      "panels": [ {...}, ... ],
      "publish": false   # optional
    }
    Upserts by (seed_id, version). If exists, updates fields; else inserts new.
    """
    try:
        payload = request.get_json(force=True)
    except Exception:
        abort(400, description="Invalid JSON")

    if not isinstance(payload, dict):
        abort(400, description="Body must be a JSON object")

    version = payload.get("version")
    updated_iso = payload.get("updated")
    panels = payload.get("panels")
    publish = bool(payload.get("publish", False))

    if not isinstance(version, int) or version < 1:
        abort(400, description="'version' must be a positive integer")
    if not isinstance(updated_iso, str):
        abort(400, description="'updated' must be an ISO8601 string")
    if not isinstance(panels, list):
        abort(400, description="'panels' must be an array")

    # Serialize panels_json in a stable order for checksum
    panels_json = json.dumps({"panels": panels}, separators=(",", ":"), sort_keys=True)
    checksum = _sha256(panels_json)

    with closing(connect()) as con, con:
        # Ensure seed exists (foreign key would catch on insert, but nicer message)
        has_seed = con.execute("SELECT 1 FROM narrative_seeds WHERE id = ?", (seed_id,)).fetchone()
        if not has_seed:
            abort(404, description=f"seed {seed_id} not found")

        # Upsert (requires SQLite 3.24+)
        con.execute("""
            INSERT INTO seed_manifests (seed_id, version, updated, panels_json, is_published, checksum)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(seed_id, version) DO UPDATE SET
                updated = excluded.updated,
                panels_json = excluded.panels_json,
                checksum = excluded.checksum,
                is_published = CASE
                    WHEN excluded.is_published = 1 THEN 1
                    ELSE seed_manifests.is_published
                END,
                updated_at = datetime('now')
        """, (seed_id, version, updated_iso, panels_json, 1 if publish else 0, checksum))

        # If publish=true, we keep other versions as-is (only latest flag on this row)

    return jsonify({"ok": True, "seed_id": seed_id, "version": version, "published": publish, "checksum": checksum})

@app.route("/api/seeds/<int:seed_id>/manifests/versions", methods=["GET"])
def list_manifest_versions(seed_id: int):
    """
    List available versions for a seed (lightweight index).
    """
    with closing(connect()) as con:
        rows = con.execute("""
            SELECT version, is_published, updated, checksum
            FROM seed_manifests
            WHERE seed_id = ?
            ORDER BY version DESC
        """, (seed_id,)).fetchall()
    return jsonify([{
        "version": r["version"],
        "is_published": bool(r["is_published"]),
        "updated": r["updated"],
        "checksum": r["checksum"]
    } for r in rows])

@app.route("/api/seeds/<int:seed_id>/manifests/publish", methods=["POST"])
def publish_manifest(seed_id: int):
    """
    Body: { "version": N }
    Marks that specific version as published (does not unpublish others).
    """
    data = request.get_json(force=True)
    version = data.get("version")
    if not isinstance(version, int):
        abort(400, description="'version' must be int")

    with closing(connect()) as con, con:
        cur = con.execute("""
            UPDATE seed_manifests
            SET is_published = 1, updated_at = datetime('now')
            WHERE seed_id = ? AND version = ?
        """, (seed_id, version))
        if cur.rowcount == 0:
            abort(404, description="manifest version not found")

    return jsonify({"ok": True, "seed_id": seed_id, "version": version, "published": True})

@app.post("/api/uploads/panels")
def upload_panels():
    """
    Multipart form:
      file: the .svg
      seed_id: int (required for subfolder toggle)
      use_seed_folder: '1'/'0' (optional)
      allow_overwrite: '1'/'0' (optional)
      checksum: sha256 hex of file (optional; verified if provided)
    Returns: { path: "/assets/panels[/seed_123]/name.svg" }
    """
    f = request.files.get("file")
    if not f:
        abort(400, description="missing file")
    filename = _safe_filename(f.filename or "panel.svg")
    if not filename.lower().endswith(".svg"):
        abort(400, description="only .svg allowed")

    # Light content sniff: reject huge or obviously wrong types
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > 20 * 1024 * 1024:  # 20 MB limit
        abort(413, description="file too large")

    head = f.stream.read(256).decode("utf-8", errors="ignore")
    f.stream.seek(0)
    if "<svg" not in head.lower():
        abort(400, description="not an svg payload")

    # Resolve destination
    use_seed = request.form.get("use_seed_folder") in ("1", "true", "True")
    allow_overwrite = request.form.get("allow_overwrite") in ("1", "true", "True")
    subdir = PANELS_ROOT
    if use_seed:
        try:
            sid = int(request.form.get("seed_id", "0"))
            if sid < 1:
                raise ValueError
        except ValueError:
            abort(400, description="invalid seed_id for seed subfolder")
        subdir = PANELS_ROOT / f"seed_{sid}"
    subdir.mkdir(parents=True, exist_ok=True)

    dest = subdir / filename
    if dest.exists() and not allow_overwrite:
        dest = _suffix_if_exists(dest)

    # Save to a temp then move (atomic-ish)
    tmp = dest.with_suffix(dest.suffix + ".uploading")
    f.save(tmp)
    os.replace(tmp, dest)

    # Optional checksum verify
    want = (request.form.get("checksum") or "").lower()
    if want:
        buf = dest.read_bytes()
        got = hashlib.sha256(buf).hexdigest()
        if got != want:
            # remove bad file to be safe
            try: dest.unlink()
            except Exception: pass
            abort(400, description="checksum mismatch")

    # Public web path (must be served by nginx location below)
    web_path = str(dest).replace(str(PANELS_ROOT), "/assets/panels").replace(os.sep, "/")
    return jsonify({"path": web_path})


@app.get("/api/assets/seed/<int:seed_id>/svgs")
def list_seed_svgs(seed_id: int):
    base = ASSETS_ROOT / f"seed_{seed_id}"
    if not base.exists():
        return jsonify({"items": []})

    items = []
    for p in sorted(base.glob("*.svg")):
        st = p.stat()
        web = f"{WEB_PREFIX}/{p.relative_to(ASSETS_ROOT).as_posix()}"
        mtime_utc = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)\
                            .isoformat(timespec="seconds")\
                            .replace("+00:00", "Z")
        items.append({
            "name": p.name,
            "url": web,
            "size": st.st_size,
            "mtime": mtime_utc,
        })
    return jsonify({"items": items})

@app.route("/api/box-of-dirt", methods=["POST"])
def box_of_dirt():
    data = request.get_json(silent=True) or {}
    domain     = (data.get("domain") or "").strip()
    dimension  = (data.get("dimension") or "").strip()
    seed       = (data.get("seed") or "").strip()
    prototype  = (data.get("prototype") or "").strip()
    thesis     = (data.get("thesis") or "").strip()

    if not (domain and dimension and seed and prototype):
        return jsonify({"error": "domain, dimension, seed, prototype are required"}), 400

    parts = [
        "INPUT (paste your case here; the model will parse it)",
        "Domain", domain,
        "Dimension", dimension
    ]
    if thesis:
        parts.append(thesis)
    parts += ["Seed", seed, "Prototype", prototype]
    full_prompt = f"{BOX_OF_DIRT_PROMPT}\n" + "\n".join(parts)

    try:
        client = _get_llm()
        res = client.chat.completions.create(
            model="gpt-5-mini-2025-08-07",
            messages=[{"role":"user","content": full_prompt}]
        )
        html = (res.choices[0].message.content or "").strip()
        if html.startswith("```"):
            # strip accidental markdown fences
            html = html.strip("`")
            html = html.split("\n", 1)[-1] if "\n" in html else html
        return Response(html, mimetype="text/html; charset=utf-8")
    except Exception as e:
        return jsonify({"error": f"generation failed: {e}"}), 500


@app.route("/api/box-of-dirt/artifacts", methods=["POST"])
def box_of_dirt_artifacts():
    data = request.get_json(silent=True) or {}
    domain    = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()

    # Accept either `seed` (3-line) or components:
    seed_three = _compose_three_line_seed(
        data.get("seed"),
        data.get("seed_problem"),
        data.get("seed_objective"),
        data.get("seed_solution"),
    )

    if not (domain and dimension and seed_three):
        return jsonify({"error":"domain, dimension, and seed (3-line or components) are required"}), 400

    user_msg = _make_artifacts_user_msg(domain, dimension, seed_three)

    try:
        client = _get_llm()
        res = client.chat.completions.create(
            model="gpt-5-mini-2025-08-07",
            messages=[
                {"role":"system", "content": BOX_OF_DIRT_ARTIFACTS_SYSTEM},
                {"role":"user",   "content": user_msg},
            ],
        )
        md = (res.choices[0].message.content or "").strip()
        # In case a model adds code fences, strip them to keep pure Markdown text
        if md.startswith("```"):
            md = md.strip("`")
            md = md.split("\n", 1)[-1] if "\n" in md else md
        return Response(md, mimetype="text/markdown; charset=utf-8")
    except Exception as e:
        return jsonify({"error": f"artifacts generation failed: {e}"}), 500

@app.route("/api/render-artifact", methods=["POST"], strict_slashes=False)
def render_seed():
    payload = request.get_json(silent=True) or {}
    artifact_yaml = payload.get("artifact_yaml", "")
    artifacts_md  = payload.get("artifacts_markdown", "")
    model = payload.get("model")
    temperature = float(payload.get("temperature", 0.1))

    if not artifact_yaml or "domain:" not in artifact_yaml:
        return jsonify({"error": "artifact_yaml is required and must include YAML content."}), 400
    try:
        # tell the model about the markdown so it can fill the arrays
        prompt = build_user_prompt(artifact_yaml, artifacts_md)
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model="gpt-5-mini-2025-08-07",
            messages=[{"role":"system","content": SYSTEM_INSTRUCTIONS},
                      {"role":"user","content": prompt}],
        )
        txt = (resp.choices[0].message.content or "")
        html = extract_html_codeblock(txt) or txt
        if not html.strip().startswith("<!DOCTYPE html"):
            raise ValueError("Model did not return an HTML document.")
        return jsonify({"html": html})
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500


@app.post("/api/box-of-dirt/bulk")
def api_bulk_start():
    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("n", 0))
    except Exception:
        n = 0
    if n <= 0:
        return jsonify({"ok": False, "error": "n must be a positive integer"}), 400

    seed_ids = _pick_random_seed_ids(n)
    if not seed_ids:
        return jsonify({"ok": False, "error": "no seeds available"}), 400

    job_id = uuid.uuid4().hex
    with BULK_JOBS_LOCK:
        BULK_JOBS[job_id] = {
            "job_id": job_id,
            "created_at": datetime.utcnow().isoformat(),
            "n": len(seed_ids),
            "done": False,
            "ok": 0,
            "fail": 0,
            "items": []
        }

    t = threading.Thread(target=_bulk_worker, args=(job_id, seed_ids), daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id, "n": len(seed_ids)}), 200


@app.get("/api/box-of-dirt/bulk/<job_id>")
def api_bulk_status(job_id):
    with BULK_JOBS_LOCK:
        job = BULK_JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({"ok": True, **job}), 200


@app.post("/api/narrative-validation")
def api_narrative_validation():
    data = request.get_json(silent=True) or {}

    domain    = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    problem   = (data.get("problem") or "").strip()
    objective = (data.get("objective") or "").strip()
    solution  = (data.get("solution") or "").strip()
    provider  = _provider_from(data)  # uses your existing helper: openai|openai_web|xai|gemini|deepseek

    missing = [k for k,v in {
        "domain":domain, "dimension":dimension, "problem":problem, "objective":objective, "solution":solution
    }.items() if not v]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    user_msg = _validation_user_message(domain, dimension, problem, objective, solution)

    # We keep your “single-file routes” constraint: provider branching mirrors your code above.
    try:
        model_name = None
        raw_text = None
        parsed = None

        if provider == "xai":
            xai = get_xai_client()
            model_name = os.getenv("XAI_MODEL", "grok-4")
            chat = xai.chat.create(model=model_name)
            # Wrap with a JSON-only instruction for reliability (doesn't change your human prompt content)
            chat.append(xai_system(VALIDATION_SYS_MSG + "\n\nReturn ONLY JSON that matches the provided schema fields. No prose."))
            # Provide a minimal schema reminder inline
            schema_hint = (
                "Schema:\n"
                "{\n"
                "  problem_validation: [ { metric, description?, datasets: [ { name, organization, url?, notes? } ] } ],\n"
                "  objective_measurement: [ ... ],\n"
                "  solution_justification: [ ... ]\n"
                "}"
            )
            chat.append(xai_user(user_msg + "\n\n" + schema_hint))
            response, parsed_obj = chat.parse(ValidationBundle)
            raw_text = getattr(response, "content", None)
            parsed = parsed_obj

        elif provider == "gemini":
            client = get_gemini_client()
            model_name = _gemini_model()
            prompt = VALIDATION_SYS_MSG + "\n\nReturn ONLY JSON matching the schema." + "\n\n" + user_msg
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": ValidationBundle,
                },
            )
            parsed = resp.parsed if not isinstance(resp.parsed, list) else resp.parsed[0]
            raw_text = getattr(resp, "text", None)

        elif provider == "deepseek":
            client = get_deepseek_client()
            model_name = _deepseek_model()
            sys_prompt = VALIDATION_SYS_MSG + "\n\nReturn ONLY JSON that matches the provided schema fields. No prose."
            user_prompt = (
                user_msg + "\n\n"
                "Schema keys: problem_validation, objective_measurement, solution_justification.\n"
                "Each is an array of {metric, description?, datasets:[{name, organization, url?, notes?}] }."
            )
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            raw_text = (completion.choices[0].message.content or "").strip()
            json_text = _extract_json_text(raw_text)
            try:
                parsed = ValidationBundle.model_validate_json(json_text)
            except Exception:
                parsed = None

        elif provider == "openai_web":
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5-mini-2025-08-07")
            # Use web_search tool to help the model ground datasets
            sys_prompt = VALIDATION_SYS_MSG + "\n\nReturn ONLY JSON matching the schema. Do not add commentary."
            user_prompt = (
                user_msg + "\n\n"
                "Return an object with keys: problem_validation, objective_measurement, solution_justification.\n"
                "Each value is an array of {metric, description?, datasets:[{name, organization, url?, notes?}] }."
            )
            resp = client.responses.create(
                model=model_name,
                tools=[{"type": "web_search"}],
                input=[
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": user_prompt},
                ],
            )
            raw_text = resp.output_text or ""
            json_text = _extract_json_text(raw_text)
            try:
                parsed = ValidationBundle.model_validate_json(json_text)
            except Exception:
                parsed = None

        else:  # provider == "openai"
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5-mini-2025-08-07")
            parsed_resp = client.responses.parse(
                model=model_name,
                input=[
                    {"role":"system","content": VALIDATION_SYS_MSG + "\n\nReturn ONLY JSON matching the schema. No prose."},
                    {"role":"user","content": (
                        user_msg + "\n\n"
                        "Return an object with keys: problem_validation, objective_measurement, solution_justification.\n"
                        "Each value is an array of {metric, description?, datasets:[{name, organization, url?, notes?}] }."
                    )},
                ],
                text_format=ValidationBundle,
            )
            parsed = parsed_resp.output_parsed
            raw_text = parsed_resp.output_text

        if parsed is None:
            return jsonify({
                "ok": False,
                "provider": provider,
                "model": model_name,
                "raw": raw_text,
                "note": "Parsing failed; 'raw' contains unparsed output."
            }), 200

        out = parsed.model_dump()
        return jsonify({
            "ok": True,
            "provider": provider,
            "model": model_name,
            "domain": domain,
            "dimension": dimension,
            "validation": out
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.post("/api/narrative-stakeholders")
def api_narrative_stakeholders():
    data = request.get_json(silent=True) or {}

    domain    = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    problem   = (data.get("problem") or "").strip()
    objective = (data.get("objective") or "").strip()
    solution  = (data.get("solution") or "").strip()
    provider  = _provider_from(data)  # existing helper

    missing = [k for k,v in {
        "domain":domain, "dimension":dimension, "problem":problem, "objective":objective, "solution":solution
    }.items() if not v]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    user_msg = _stakeholders_user_message(domain, dimension, problem, objective, solution)

    try:
        model_name = None
        raw_text = None
        parsed = None

        if provider == "xai":
            xai = get_xai_client()
            model_name = os.getenv("XAI_MODEL", "grok-4")
            chat = xai.chat.create(model=model_name)
            chat.append(xai_system(STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON with keys: primary, secondary, end_users_beneficiaries, external_contextual. No prose."))
            schema_hint = (
                "Schema:\n"
                "{\n"
                "  primary: [{name, category?, role?, why}],\n"
                "  secondary: [{...}],\n"
                "  end_users_beneficiaries: [{...}],\n"
                "  external_contextual: [{...}]\n"
                "}"
            )
            chat.append(xai_user(user_msg + "\n\n" + schema_hint))
            response, parsed_obj = chat.parse(StakeholderMap)
            raw_text = getattr(response, "content", None)
            parsed = parsed_obj

        elif provider == "gemini":
            client = get_gemini_client()
            model_name = _gemini_model()
            prompt = STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON matching the schema." + "\n\n" + user_msg
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": StakeholderMap,
                },
            )
            parsed = resp.parsed if not isinstance(resp.parsed, list) else resp.parsed[0]
            raw_text = getattr(resp, "text", None)

        elif provider == "deepseek":
            client = get_deepseek_client()
            model_name = _deepseek_model()
            sys_prompt = STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON with keys primary, secondary, end_users_beneficiaries, external_contextual. No prose."
            user_prompt = user_msg
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            raw_text = (completion.choices[0].message.content or "").strip()
            json_text = _extract_json_text(raw_text)  # your existing helper
            try:
                parsed = StakeholderMap.model_validate_json(json_text)
            except Exception:
                parsed = None

        elif provider == "openai_web":
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            sys_prompt = STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON matching the schema below. No commentary.\nSchema keys: primary, secondary, end_users_beneficiaries, external_contextual. Each item: {name, category?, role?, why}."
            resp = client.responses.create(
                model=model_name,
                tools=[{"type": "web_search"}],  # lets the model ground org names if it wants
                input=[
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": user_msg},
                ],
            )
            raw_text = resp.output_text or ""
            json_text = _extract_json_text(raw_text)
            try:
                parsed = StakeholderMap.model_validate_json(json_text)
            except Exception:
                parsed = None

        else:  # "openai"
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            parsed_resp = client.responses.parse(
                model=model_name,
                input=[
                    {"role":"system","content": STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON matching the schema. No prose."},
                    {"role":"user","content": (
                        user_msg + "\n\n"
                        "Return an object with keys: primary, secondary, end_users_beneficiaries, external_contextual.\n"
                        "Each value is an array of {name, category?, role?, why}."
                    )},
                ],
                text_format=StakeholderMap,
            )
            parsed = parsed_resp.output_parsed
            raw_text = parsed_resp.output_text

        if parsed is None:
            return jsonify({
                "ok": False,
                "provider": provider,
                "model": model_name,
                "raw": raw_text,
                "note": "Parsing failed; 'raw' contains unparsed output."
            }), 200

        return jsonify({
            "ok": True,
            "provider": provider,
            "model": model_name,
            "domain": domain,
            "dimension": dimension,
            "stakeholders": parsed.model_dump(),
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.post("/api/narrative-stakeholders")
def api_narrative_stakeholders():
    data = request.get_json(silent=True) or {}

    domain    = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    problem   = (data.get("problem") or "").strip()
    objective = (data.get("objective") or "").strip()
    solution  = (data.get("solution") or "").strip()
    provider  = _provider_from(data)  # existing helper

    missing = [k for k,v in {
        "domain":domain, "dimension":dimension, "problem":problem, "objective":objective, "solution":solution
    }.items() if not v]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    user_msg = _stakeholders_user_message(domain, dimension, problem, objective, solution)

    try:
        model_name = None
        raw_text = None
        parsed = None

        if provider == "xai":
            xai = get_xai_client()
            model_name = os.getenv("XAI_MODEL", "grok-4")
            chat = xai.chat.create(model=model_name)
            chat.append(xai_system(STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON with keys: primary, secondary, end_users_beneficiaries, external_contextual. No prose."))
            schema_hint = (
                "Schema:\n"
                "{\n"
                "  primary: [{name, category?, role?, why}],\n"
                "  secondary: [{...}],\n"
                "  end_users_beneficiaries: [{...}],\n"
                "  external_contextual: [{...}]\n"
                "}"
            )
            chat.append(xai_user(user_msg + "\n\n" + schema_hint))
            response, parsed_obj = chat.parse(StakeholderMap)
            raw_text = getattr(response, "content", None)
            parsed = parsed_obj

        elif provider == "gemini":
            client = get_gemini_client()
            model_name = _gemini_model()
            prompt = STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON matching the schema." + "\n\n" + user_msg
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": StakeholderMap,
                },
            )
            parsed = resp.parsed if not isinstance(resp.parsed, list) else resp.parsed[0]
            raw_text = getattr(resp, "text", None)

        elif provider == "deepseek":
            client = get_deepseek_client()
            model_name = _deepseek_model()
            sys_prompt = STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON with keys primary, secondary, end_users_beneficiaries, external_contextual. No prose."
            user_prompt = user_msg
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            raw_text = (completion.choices[0].message.content or "").strip()
            json_text = _extract_json_text(raw_text)  # your existing helper
            try:
                parsed = StakeholderMap.model_validate_json(json_text)
            except Exception:
                parsed = None

        elif provider == "openai_web":
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            sys_prompt = STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON matching the schema below. No commentary.\nSchema keys: primary, secondary, end_users_beneficiaries, external_contextual. Each item: {name, category?, role?, why}."
            resp = client.responses.create(
                model=model_name,
                tools=[{"type": "web_search"}],  # lets the model ground org names if it wants
                input=[
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": user_msg},
                ],
            )
            raw_text = resp.output_text or ""
            json_text = _extract_json_text(raw_text)
            try:
                parsed = StakeholderMap.model_validate_json(json_text)
            except Exception:
                parsed = None

        else:  # "openai"
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            parsed_resp = client.responses.parse(
                model=model_name,
                input=[
                    {"role":"system","content": STAKEHOLDERS_SYS_MSG + "\n\nReturn ONLY JSON matching the schema. No prose."},
                    {"role":"user","content": (
                        user_msg + "\n\n"
                        "Return an object with keys: primary, secondary, end_users_beneficiaries, external_contextual.\n"
                        "Each value is an array of {name, category?, role?, why}."
                    )},
                ],
                text_format=StakeholderMap,
            )
            parsed = parsed_resp.output_parsed
            raw_text = parsed_resp.output_text

        if parsed is None:
            return jsonify({
                "ok": False,
                "provider": provider,
                "model": model_name,
                "raw": raw_text,
                "note": "Parsing failed; 'raw' contains unparsed output."
            }), 200

        return jsonify({
            "ok": True,
            "provider": provider,
            "model": model_name,
            "domain": domain,
            "dimension": dimension,
            "stakeholders": parsed.model_dump(),
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.post("/api/narrative-embodied")
def api_narrative_embodied():
    data = request.get_json(silent=True) or {}

    domain    = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    problem   = (data.get("problem") or "").strip()
    objective = (data.get("objective") or "").strip()
    solution  = (data.get("solution") or "").strip()
    provider  = _provider_from(data)

    missing = [k for k,v in {
        "domain":domain, "dimension":dimension, "problem":problem, "objective":objective, "solution":solution
    }.items() if not v]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    user_msg = _embodied_user_message(domain, dimension, problem, objective, solution)

    try:
        model_name = None
        raw_text = None
        parsed = None

        if provider == "xai":
            xai = get_xai_client()
            model_name = os.getenv("XAI_MODEL", "grok-4")
            chat = xai.chat.create(model=model_name)
            chat.append(xai_system(EMBODIED_SYS_MSG + "\n\nReturn ONLY JSON with keys: eyes, ears, hands, nose, mouth, skin, forces. No prose."))
            schema_hint = (
                "Schema:\n"
                "{ eyes:[{cue, why?}], ears:[{cue, why?}], hands:[{cue, why?}], "
                "nose:[{cue, why?}], mouth:[{cue, why?}], skin:[{cue, why?}], forces:[{cue, why?}] }"
            )
            chat.append(xai_user(user_msg + "\n\n" + schema_hint))
            response, parsed_obj = chat.parse(EmbodiedMap)
            raw_text = getattr(response, "content", None)
            parsed = parsed_obj

        elif provider == "gemini":
            client = get_gemini_client()
            model_name = _gemini_model()
            prompt = EMBODIED_SYS_MSG + "\n\nReturn ONLY JSON matching the schema." + "\n\n" + user_msg
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": EmbodiedMap,
                },
            )
            parsed = resp.parsed if not isinstance(resp.parsed, list) else resp.parsed[0]
            raw_text = getattr(resp, "text", None)

        elif provider == "deepseek":
            client = get_deepseek_client()
            model_name = _deepseek_model()
            sys_prompt = EMBODIED_SYS_MSG + "\n\nReturn ONLY JSON with keys eyes, ears, hands, nose, mouth, skin, forces. No prose."
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
            )
            raw_text = (completion.choices[0].message.content or "").strip()
            json_text = _extract_json_text(raw_text)
            try:
                parsed = EmbodiedMap.model_validate_json(json_text)
            except Exception:
                parsed = None

        elif provider == "openai_web":
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            sys_prompt = EMBODIED_SYS_MSG + "\n\nReturn ONLY JSON matching the schema below. No commentary.\nSchema keys: eyes, ears, hands, nose, mouth, skin, forces. Each item: {cue, why?}."
            resp = client.responses.create(
                model=model_name,
                tools=[{"type": "web_search"}],  # optional; not required here but consistent with your pattern
                input=[
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": user_msg},
                ],
            )
            raw_text = resp.output_text or ""
            json_text = _extract_json_text(raw_text)
            try:
                parsed = EmbodiedMap.model_validate_json(json_text)
            except Exception:
                parsed = None

        else:  # "openai"
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            parsed_resp = client.responses.parse(
                model=model_name,
                input=[
                    {"role":"system","content": EMBODIED_SYS_MSG + "\n\nReturn ONLY JSON matching the schema. No prose."},
                    {"role":"user","content": (
                        user_msg + "\n\n"
                        "Return an object with keys: eyes, ears, hands, nose, mouth, skin, forces. "
                        "Each value is an array of {cue, why?}."
                    )},
                ],
                text_format=EmbodiedMap,
            )
            parsed = parsed_resp.output_parsed
            raw_text = parsed_resp.output_text

        if parsed is None:
            return jsonify({
                "ok": False,
                "provider": provider,
                "model": model_name,
                "raw": raw_text,
                "note": "Parsing failed; 'raw' contains unparsed output."
            }), 200

        return jsonify({
            "ok": True,
            "provider": provider,
            "model": model_name,
            "domain": domain,
            "dimension": dimension,
            "embodied": parsed.model_dump(),
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.post("/api/narrative-playbook")
def api_narrative_playbook():
    data = request.get_json(silent=True) or {}

    domain    = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    problem   = (data.get("problem") or "").strip()
    objective = (data.get("objective") or "").strip()
    solution  = (data.get("solution") or "").strip()
    provider  = _provider_from(data)  # openai | openai_web | xai | gemini | deepseek

    missing = [k for k,v in {
        "domain":domain, "dimension":dimension, "problem":problem, "objective":objective, "solution":solution
    }.items() if not v]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    user_msg = _roadmap_user_message(domain, dimension, problem, objective, solution)

    try:
        model_name = None
        raw_text = None
        parsed = None

        if provider == "xai":
            xai = get_xai_client()
            model_name = os.getenv("XAI_MODEL", "grok-4")
            chat = xai.chat.create(model=model_name)
            chat.append(xai_system(ROADMAP_SYS_MSG + "\n\nReturn ONLY JSON with key 'phases' (array). No prose."))
            schema_hint = (
                "Schema:\n"
                "{ phases: [ { name, horizon, goal?, milestones:[], outputs:[], indicators:[], decision_gates:[] } ] }"
            )
            chat.append(xai_user(user_msg + "\n\n" + schema_hint))
            response, parsed_obj = chat.parse(RoadmapPlaybook)
            raw_text = getattr(response, "content", None)
            parsed = parsed_obj

        elif provider == "gemini":
            client = get_gemini_client()
            model_name = _gemini_model()
            prompt = ROADMAP_SYS_MSG + "\n\nReturn ONLY JSON matching the schema." + "\n\n" + user_msg
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": RoadmapPlaybook,
                },
            )
            parsed = resp.parsed if not isinstance(resp.parsed, list) else resp.parsed[0]
            raw_text = getattr(resp, "text", None)

        elif provider == "deepseek":
            client = get_deepseek_client()
            model_name = _deepseek_model()
            sys_prompt = ROADMAP_SYS_MSG + "\n\nReturn ONLY JSON with key 'phases'. No prose."
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
            )
            raw_text = (completion.choices[0].message.content or "").strip()
            json_text = _extract_json_text(raw_text)  # your existing helper
            try:
                parsed = RoadmapPlaybook.model_validate_json(json_text)
            except Exception:
                parsed = None

        elif provider == "openai_web":
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            sys_prompt = ROADMAP_SYS_MSG + "\n\nReturn ONLY JSON matching the schema below. No commentary.\n" \
                        "Schema: { phases: [ { name, horizon, goal?, milestones:[], outputs:[], indicators:[], decision_gates:[] } ] }"
            resp = client.responses.create(
                model=model_name,
                tools=[{"type": "web_search"}],  # optional; consistent with your pattern
                input=[
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": user_msg},
                ],
            )
            raw_text = resp.output_text or ""
            json_text = _extract_json_text(raw_text)
            try:
                parsed = RoadmapPlaybook.model_validate_json(json_text)
            except Exception:
                parsed = None

        else:  # "openai"
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            parsed_resp = client.responses.parse(
                model=model_name,
                input=[
                    {"role":"system","content": ROADMAP_SYS_MSG + "\n\nReturn ONLY JSON matching the schema. No prose."},
                    {"role":"user","content": (
                        user_msg + "\n\n"
                        "Return an object with key 'phases' where each phase has: "
                        "name, horizon, goal?, milestones[], outputs[], indicators[], decision_gates[]."
                    )},
                ],
                text_format=RoadmapPlaybook,
            )
            parsed = parsed_resp.output_parsed
            raw_text = parsed_resp.output_text

        if parsed is None:
            return jsonify({
                "ok": False,
                "provider": provider,
                "model": model_name,
                "raw": raw_text,
                "note": "Parsing failed; 'raw' contains unparsed output."
            }), 200

        return jsonify({
            "ok": True,
            "provider": provider,
            "model": model_name,
            "domain": domain,
            "dimension": dimension,
            "playbook": parsed.model_dump(),
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.post("/api/narrative-archetype-playbook")
def api_narrative_archetype_playbook():
    data = request.get_json(silent=True) or {}

    domain    = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    problem   = (data.get("problem") or "").strip()
    objective = (data.get("objective") or "").strip()
    solution  = (data.get("solution") or "").strip()
    provider  = _provider_from(data)  # openai | openai_web | xai | gemini | deepseek

    missing = [k for k,v in {
        "domain":domain, "dimension":dimension, "problem":problem, "objective":objective, "solution":solution
    }.items() if not v]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    user_msg = _archetype_playbook_user_message(domain, dimension, problem, objective, solution)

    try:
        model_name = None
        raw_text = None
        parsed = None

        if provider == "xai":
            xai = get_xai_client()
            model_name = os.getenv("XAI_MODEL", "grok-4")
            chat = xai.chat.create(model=model_name)
            chat.append(xai_system(ARCHETYPE_PLAYBOOK_SYS_MSG + "\n\nReturn ONLY JSON with keys: classification, phases, north_star. No prose."))
            schema_hint = (
                "Schema:\n"
                "{\n"
                "  classification: { archetype: 'Technology/Product'|'Science/Knowledge'|'Cultural/Artistic'|'Political/Governance', why? },\n"
                "  phases: [ { name, horizon, goal?, milestones:[], outputs:[], indicators:[], decision_gates:[] } ],\n"
                "  north_star: string\n"
                "}"
            )
            chat.append(xai_user(user_msg + "\n\n" + schema_hint))
            response, parsed_obj = chat.parse(ArchetypePlaybook)
            raw_text = getattr(response, "content", None)
            parsed = parsed_obj

        elif provider == "gemini":
            client = get_gemini_client()
            model_name = _gemini_model()
            prompt = ARCHETYPE_PLAYBOOK_SYS_MSG + "\n\nReturn ONLY JSON matching the schema." + "\n\n" + user_msg
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": ArchetypePlaybook,
                },
            )
            parsed = resp.parsed if not isinstance(resp.parsed, list) else resp.parsed[0]
            raw_text = getattr(resp, "text", None)

        elif provider == "deepseek":
            client = get_deepseek_client()
            model_name = _deepseek_model()
            sys_prompt = ARCHETYPE_PLAYBOOK_SYS_MSG + "\n\nReturn ONLY JSON with keys classification, phases, north_star. No prose."
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
            )
            raw_text = (completion.choices[0].message.content or "").strip()
            json_text = _extract_json_text(raw_text)  # uses your existing helper
            try:
                parsed = ArchetypePlaybook.model_validate_json(json_text)
            except Exception:
                parsed = None

        elif provider == "openai_web":
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            sys_prompt = ARCHETYPE_PLAYBOOK_SYS_MSG + "\n\nReturn ONLY JSON matching the schema below. No commentary.\n" \
                        "Schema: { classification:{archetype, why?}, phases:[{name, horizon, goal?, milestones:[], outputs:[], indicators:[], decision_gates:[]}], north_star:string }"
            resp = client.responses.create(
                model=model_name,
                tools=[{"type": "web_search"}],  # optional; consistent with your other endpoints
                input=[
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": user_msg},
                ],
            )
            raw_text = resp.output_text or ""
            json_text = _extract_json_text(raw_text)
            try:
                parsed = ArchetypePlaybook.model_validate_json(json_text)
            except Exception:
                parsed = None

        else:  # "openai"
            client = _get_llm()
            model_name = os.getenv("OPENAI_MODEL", "gpt-5")
            parsed_resp = client.responses.parse(
                model=model_name,
                input=[
                    {"role":"system","content": ARCHETYPE_PLAYBOOK_SYS_MSG + "\n\nReturn ONLY JSON matching the schema. No prose."},
                    {"role":"user","content": (
                        user_msg + "\n\n"
                        "Return an object with keys: classification, phases, north_star. "
                        "classification.archetype ∈ {Technology/Product, Science/Knowledge, Cultural/Artistic, Political/Governance}."
                    )},
                ],
                text_format=ArchetypePlaybook,
            )
            parsed = parsed_resp.output_parsed
            raw_text = parsed_resp.output_text

        if parsed is None:
            return jsonify({
                "ok": False,
                "provider": provider,
                "model": model_name,
                "raw": raw_text,
                "note": "Parsing failed; 'raw' contains unparsed output."
            }), 200

        # Optional sanity: encourage 6 phases (don't hard fail)
        result = parsed.model_dump()
        return jsonify({
            "ok": True,
            "provider": provider,
            "model": model_name,
            "domain": domain,
            "dimension": dimension,
            "archetype_playbook": result
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500
