import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request, Response
from typing import List, Optional
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

app = Flask(__name__)

DB_PATH = "/var/www/site/data/narratives_data.db"  # keep consistent with your setup

PANELS_ROOT = Path("/var/www/site/data/assets/panels")  # target on disk
ASSETS_ROOT = Path("/var/www/site/data/assets/panels")   # << per your ask
WEB_PREFIX  = "/assets/panels"                            # serve as: /assets/seed_{id}/...

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



# --- LLM wiring ---

SYSTEM_INSTRUCTIONS = """SYSTEM INSTRUCTIONS (do not repeat back):
You are a code generator. Transform the provided Artifact into a SINGLE self-contained HTML file (one <html> document with inline <style> and <script>) in the “Fantasiagenesis” style shown in prior examples:
- Clean, responsive, print-friendly.
- Header with title + thesis, a toolbar (Print button and a “pill” chip).
- Meta grid for Domain & Dimension.
- “Seed” with A (Problem), B (Objective), Solution (Link) and a short scope note.
- Four-tab layout:
  1) Real, deployable artifacts — each as a <details> “card” with Owner and Notes.
  2) Box-of-dirt (build now) — each as a <details> “card” with file chips / bullet list.
  3) Safety guardrails — highlight constraints and what’s intentionally excluded.
  4) 3 Next steps (48–72 hours) — checkbox tasks.
- Accessible tabs (ARIA) with keyboard nav (Left/Right/Home/End).
- No external fonts, libraries, images, or network calls.
- NOTE: Do NOT include any “Expand/Collapse All” controls or related JavaScript.

CRITICAL OUTPUT RULES:
- OUTPUT ONLY one fenced code block marked ```html with the complete file. No extra commentary.
- Use the boilerplate CSS/JS provided here verbatim for consistency (minus any expand/collapse code).
- If some fields are missing, omit gracefully.
- Never invent operational/unsafe details; if the Artifact contains safety-sensitive domains, preserve and emphasize the guardrails text and keep content non-operational.
- Use HTML entities for special characters (e.g., &amp;).
"""

# Keep this boilerplate EXACT (only text nodes and repeated sections may change in model output)
HTML_BOILERPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{{page_meta.page_title}}</title>
<meta name="color-scheme" content="light dark" />
<style>
  :root{
    --bg: Canvas; --fg: CanvasText;
    --muted: color-mix(in oklab, var(--fg) 65%, transparent);
    --line: color-mix(in oklab, var(--fg) 14%, transparent);
    --accent: color-mix(in oklab, var(--fg) 22%, transparent);
    --card: color-mix(in oklab, var(--bg) 95%, var(--fg) 5%);
    --ok:#1a7f37; --warn:#b7791f; --danger:#b42318;
    --chip-bg: color-mix(in oklab, var(--fg) 8%, transparent);
    --radius:16px;
  }
  *{box-sizing:border-box}
  html,body{height:100%;background:var(--bg);color:var(--fg)}
  body{margin:0;font:15px/1.5 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji"}
  header{padding:20px clamp(16px,4vw,28px);border-bottom:1px solid var(--line);display:grid;gap:8px}
  .badge{display:inline-flex;gap:.4rem;align-items:center;padding:.2rem .6rem;border-radius:999px;background:var(--chip-bg);font-weight:600;font-size:.8rem}
  .title{font-size:clamp(1.2rem,2.4vw,1.6rem);margin:2px 0 0;font-weight:800;letter-spacing:.01em}
  .subtitle{opacity:.85;font-size:.95rem}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .btn{border:1px solid var(--line);background:var(--bg);border-radius:10px;padding:.5rem .8rem;cursor:pointer;font-weight:700}
  .btn:hover{background:var(--chip-bg)}
  .pill{border:1px solid var(--line);border-radius:999px;padding:.15rem .55rem;font-size:.8rem}
  .sep{flex:1}
  main{padding:22px clamp(16px,4vw,28px);display:grid;gap:20px;max-width:1100px;margin:0 auto}
  .meta{display:grid;gap:12px;grid-template-columns:repeat(12,minmax(0,1fr))}
  .meta>section{grid-column:span 12;background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px}
  @media (min-width:900px){.meta>.col-4{grid-column:span 4}.meta>.col-8{grid-column:span 8}}
  h2{font-size:1.05rem;margin:.1rem 0 .6rem;letter-spacing:.01em}
  h3{font-size:1rem;margin:.1rem 0 .4rem;letter-spacing:.01em}
  .kv{display:grid;gap:8px}
  .kv .row{display:flex;gap:.6rem;align-items:flex-start}
  .kv .key{width:170px;opacity:.7;font-weight:600}
  .chip{display:inline-flex;align-items:center;gap:.35rem;padding:.18rem .55rem;border-radius:999px;background:var(--chip-bg);font-size:.8rem}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
  .note{font-size:.9rem;opacity:.85}
  .grid{display:grid;gap:12px}
  .cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px;display:grid;gap:10px}
  .owner{font-size:.85rem;opacity:.85}
  .section{border-left:4px solid var(--accent);padding-left:12px;margin-top:6px;display:grid;gap:8px}
  .tabs{display:flex;gap:8px;flex-wrap:wrap}
  .tab-btn{border:1px solid var(--line);background:var(--bg);border-radius:999px;padding:.4rem .75rem;cursor:pointer;font-weight:700}
  .tab-btn[aria-selected="true"]{background:var(--chip-bg)}
  .tabpanel{display:none}
  .tabpanel.active{display:grid;gap:14px}
  details{border:1px solid var(--line);border-radius:12px;background:var(--card)}
  summary{padding:12px;cursor:pointer;list-style:none;user-select:none;font-weight:700;border-bottom:1px solid var(--line)}
  summary::-webkit-details-marker{display:none}
  .details-body{padding:12px;display:grid;gap:10px}
  .safety{border:1px dashed var(--line);border-left:5px solid var(--warn);background:color-mix(in oklab,var(--warn) 7%,transparent);border-radius:var(--radius);padding:14px;display:grid;gap:8px}
  .status-warn{color:var(--warn);font-weight:700}
  .status-ok{color:var(--ok);font-weight:700}
  .tasks{display:grid;gap:8px}
  .task{display:flex;gap:.6rem;align-items:flex-start;padding:10px;border:1px solid var(--line);border-radius:12px;background:var(--card)}
  .task input{margin-top:.25rem}
  footer{padding:26px clamp(16px,4vw,28px);border-top:1px solid var(--line);opacity:.8}
</style>
</head>
<body>

<header>
  <div><span class="badge">Fantasiagenesis • Seed</span></div>
  <h1 class="title">{{page_meta.header_title}}</h1>
  <p class="subtitle">{{thesis}}</p>
  <div class="toolbar">
    <button class="btn" id="printBtn">Export / Print</button>
    <span class="sep"></span>
    <span class="pill">{{page_meta.header_pill}}</span>
  </div>
</header>

<main>
  <div class="meta">
    <section class="col-4">
      <h2>Domain</h2>
      <div class="kv">
        <div class="row"><div class="key">Label</div><div class="val"><span class="chip">{{domain.label}}</span></div></div>
        <div class="row"><div class="key">ID</div><div class="val mono">{{domain.id}}</div></div>
      </div>
    </section>
    <section class="col-8">
      <h2>Dimension</h2>
      <div class="kv">
        <div class="row"><div class="key">Label</div><div class="val"><span class="chip">{{dimension.label}}</span></div></div>
        <div class="row"><div class="key">ID</div><div class="val mono">{{dimension.id}}</div></div>
        <div class="row"><div class="key">Thesis</div><div class="val">{{thesis}}</div></div>
      </div>
    </section>
    <section class="col-12">
      <h2>Seed</h2>
      <div class="grid">
        <div class="section"><h3>A — Problem</h3><p>{{seed.problem}}</p></div>
        <div class="section"><h3>B — Objective</h3><p>{{seed.objective}}</p></div>
        <div class="section">
          <h3>Solution (Link)</h3>
          <p>{{seed.solution_link}}</p>
          <p class="note"><strong>Scope note:</strong> {{seed.scope_note}}</p>
        </div>
      </div>
    </section>
  </div>

  <div class="tabs" role="tablist" aria-label="Artifacts">
    <button class="tab-btn" role="tab" aria-selected="true" aria-controls="tab-real" id="tab-real-btn">Real, deployable artifacts</button>
    <button class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bod" id="tab-bod-btn">Box-of-dirt (build now)</button>
    <button class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-safety" id="tab-safety-btn">Safety guardrails</button>
    <button class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-next" id="tab-next-btn">{{next_steps_title}}</button>
  </div>

  <section id="tab-real" class="tabpanel active" role="tabpanel" aria-labelledby="tab-real-btn">
    <div class="cards">
      <!-- Repeat for each real_artifacts item -->
      {{#each real_artifacts}}
      <details class="card" {{#if @first}}open{{/if}}>
        <summary>{{title}}</summary>
        <div class="details-body">
          <div class="owner"><strong>Owner:</strong> {{owner}}</div>
          <p>{{description}}</p>
          {{#if notes}}<p class="note">{{notes}}</p>{{/if}}
        </div>
      </details>
      {{/each}}
    </div>
  </section>

  <section id="tab-bod" class="tabpanel" role="tabpanel" aria-labelledby="tab-bod-btn">
    <div class="grid">
      <!-- Repeat for each box_of_dirt item -->
      {{#each box_of_dirt}}
      <details class="card" {{#if @first}}open{{/if}}>
        <summary>{{title}}</summary>
        <div class="details-body">
          <div class="owner"><strong>Owner:</strong> {{owner}}</div>
          <ul>
            {{#each bullets}}<li><span class="chip">{{this}}</span></li>{{/each}}
          </ul>
        </div>
      </details>
      {{/each}}
    </div>
  </section>

  <section id="tab-safety" class="tabpanel" role="tabpanel" aria-labelledby="tab-safety-btn">
    <div class="safety">
      <h3>Safety guardrails</h3>
      <p>{{safety_guardrails}}</p>
      <p class="note">Status: <span class="status-warn">Guardrails enforced</span></p>
    </div>
  </section>

  <section id="tab-next" class="tabpanel" role="tabpanel" aria-labelledby="tab-next-btn">
    <div class="grid">
      <h2>{{next_steps_title}}</h2>
      <div class="tasks">
        {{#each next_steps}}
        <label class="task"><input type="checkbox" /><div>{{this}}</div></label>
        {{/each}}
      </div>
    </div>
  </section>
</main>

<footer>
  <div>Fantasiagenesis • Box of Dirt • <span class="mono">{{page_meta.footer_seed_id}}</span></div>
  <div class="note">Planning scaffold; intentionally excludes hazardous operational detail.</div>
</footer>

<script>
  const tabs = [
    {btn:'tab-real-btn', panel:'tab-real'},
    {btn:'tab-bod-btn', panel:'tab-bod'},
    {btn:'tab-safety-btn', panel:'tab-safety'},
    {btn:'tab-next-btn', panel:'tab-next'},
  ];
  function selectTab(id){
    tabs.forEach(t=>{
      const b=document.getElementById(t.btn), p=document.getElementById(t.panel);
      const active=(t.btn===id);
      b.setAttribute('aria-selected', active?'true':'false');
      p.classList.toggle('active', active);
    });
  }
  tabs.forEach(t=>{
    document.getElementById(t.btn).addEventListener('click', ()=>selectTab(t.btn));
  });
  document.getElementById('printBtn').addEventListener('click', ()=>window.print());
  document.querySelectorAll('.tab-btn').forEach((btn,idx,list)=>{
    btn.addEventListener('keydown',(e)=>{
      if(e.key==='ArrowRight'){e.preventDefault();list[(idx+1)%list.length].focus();}
      if(e.key==='ArrowLeft'){e.preventDefault();list[(idx-1+list.length)%list.length].focus();}
      if(e.key==='Home'){e.preventDefault();list[0].focus();}
      if(e.key==='End'){e.preventDefault();list[list.length-1].focus();}
    });
  });
  selectTab('tab-real-btn');
</script>
</body>
</html>"""


BOX_OF_DIRT_ARTIFACTS_SYSTEM = """You are an expert systems designer and productizer. Given a short topic (domain), a framing label (dimension), and a concise seed (problem / objective / solution), produce two clearly organized lists:
(A) Real, deployable artifacts — the things that must exist in the real world for the proposed solution to operate reliably at production scale. These are physical, legal, governance, digital, operational and financial artifacts (hardware, software, permits, contracts, supply chain, QA, monitoring, workforce, regulatory artifacts, etc.).
(B) Box-of-dirt artifacts — the minimal, safe prototypes and deliverables that can be created immediately (words, diagrams, JSON/SQL schema, mock UI, simulations, checklists, slide decks). These are explicitly non-actionable if the seed touches restricted domains — they should be documents, mockups, safe simulators, or governance artifacts only.
Rules and constraints:
Produce no step-by-step wet-lab protocols, experimental parameters, recipes, or instructions that enable the construction or misuse of biological agents, weapons, illegal hacking tools, or other harmful capabilities.
For any domain with potential biosafety/chemical/security risk, explicitly replace actionable operational detail with higher-level system artifacts and policy/regulatory templates. Always include a short safety guardrail paragraph.
For each artifact (both A and B) include:
A short name/title (one line).
A 1–2 sentence description of what it is and why it’s required.
For box-of-dirt artifacts (B) add 2–4 immediate prototype actions: tangible files or items to create now (e.g., feedstock_schema.json, marketplace_schema.sql, single-page HTML mock, Monte Carlo notebook).
An owner (role or team) who would be responsible for it.
Limit each list to 8–12 high-value items, prioritized (top items most critical). Keep each artifact entry concise.
At the end, provide 3 short, concrete next steps the requester can do in the next 48–72 hours (no external approvals required).
Output must be in Markdown with headings (A) Real artifacts and (B) Box-of-dirt prototypes. Use bullet lists with short sub-bullets. Do not ask clarifying questions — make best-effort assumptions from the seed.
Tone: practical, direct, and action-oriented."""


BOX_OF_DIRT_PROMPT = """LLM PROMPT (copy below this line and use as-is):
You are a front-end generator. Produce a single, self-contained HTML page that presents a “Box of Dirt” narrative prototype based on the INPUT provided at the end of this prompt.
Output rules (must follow)
Output only raw HTML (no Markdown fences, no commentary).
The HTML must be standalone: inline CSS, optional minimal inline JS, no external fonts, images, or network requests.
Use accessible, clean markup and a compact, modern look. Support dark/light with :root { color-scheme: light dark; }.
If the Prototype describes toggles/buttons/sliders, include non-functional controls that visually flip canned states (no backend).
Use the content exactly as given; you may lightly normalize punctuation/quotes, but do not invent metrics or claims beyond the INPUT.
Title pattern: <Dimension Name> — Box of Dirt (<Domain Name>). Do not include any internal IDs in the title.
Page layout (sections)
Header
Title as above.
Domain + Dimension line.
Optional thesis (if provided with the Dimension).
Seed Card
Show A (Problem), B (Objective), and Solution (Link).
A small note: “All numbers are mocked/prefilled; no backend.”
Core Intent
One concise paragraph from Prototype → “Core Intent — …”.
Minimal Build / Storyboard
If the Prototype has a “Minimal Build — Title: …”, show that subtitle.
If Scenes/Panels are enumerated (e.g., “Panel 1 — …”, “Scene 3 — …”), render each as a card:
A mini header (e.g., “Panel 2 — Magnet and Motor Design Choices”).
Body with bullets or short paragraphs.
For visual cues, insert .placeholder boxes (no images).
If toggles/sliders/states are described, add a top Controls section with non-functional UI that drives pre-scripted text changes only (no live calculations). Never fetch data.
Metrics / Dials (optional)
If the Prototype lists KPIs/outcomes, render them in small metric rows (labels on left, values right).
Load-Bearing Test
“What to show” bullets.
Validating Reactions / Red Flags
Two columns: “Validating reactions we want” and “Red flags (invalidate)”.
First Eyes
A bullet list of the stakeholder groups.
Why This is a Box of Dirt
Bulleted points for Minimal / Disposable / Growth-inviting.
Footnote
“Prototype storyboard for discussion; all content is illustrative.”
Style guide (use this exact CSS skeleton)
Keep class names and structure so pages are consistent across runs.
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title><!-- INSERT TITLE --></title>
<style>
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 24px; font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji"; }
h1 { font-size: 1.6rem; margin: 0 0 .35rem; }
h2 { font-size: 1.15rem; margin: 1rem 0 .6rem; }
h3 { font-size: 1rem; margin: .2rem 0 .45rem; }
p  { margin: .45rem 0; }
.small { font-size:.9rem; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
.muted { opacity:.75; }
.thesis { font-style: italic; margin:.3rem 0 .8rem; }
.badge, .pill { display:inline-block; padding:.18rem .5rem; border:1px solid rgba(0,0,0,.18); border-radius:.45rem; font-size:.78rem; background: color-mix(in oklab, Canvas 96%, CanvasText 4%); }
.pill { border-radius:999px; }
.card  { border:1px solid rgba(0,0,0,.18); border-radius:.65rem; background: color-mix(in oklab, Canvas 94%, CanvasText 6%); padding:.9rem 1rem; }
.panel { border:1px solid rgba(0,0,0,.16); border-radius:.6rem; padding:.8rem; background: Canvas; }
.grid  { display:grid; gap:.9rem; }
.cols-2 { grid-template-columns: 1fr 1fr; }
.cols-3 { grid-template-columns: 1fr 1fr 1fr; }
.cols-4 { grid-template-columns: 1fr 1fr 1fr 1fr; }
@media (max-width: 1000px){ .cols-2,.cols-3,.cols-4 { grid-template-columns: 1fr; } }
.row { display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; }
.spacer { flex:1; }
.btn { padding:.5rem .8rem; border:1px solid rgba(0,0,0,.2); background: color-mix(in oklab, Canvas 92%, CanvasText 8%); border-radius:.5rem; cursor:pointer; }
.btn[disabled]{ opacity:.55; cursor:not-allowed; }
.placeholder { border:1px dashed rgba(0,0,0,.28); border-radius:.5rem; display:grid; place-items:center; min-height: 120px; font-size:.92rem; opacity:.75; }
.metrics { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:.4rem .8rem; }
.metrics div { display:flex; justify-content:space-between; gap:.5rem; }
.metrics .k { opacity:.75; }
.scene { border:1px solid rgba(0,0,0,.16); border-radius:.6rem; padding:.7rem; background: color-mix(in oklab, Canvas 98%, CanvasText 2%); }
.scene h4 { margin:.2rem 0 .4rem; font-size:.98rem; }
.scene .cap { margin-top:.45rem; font-size:.9rem; opacity:.8; }
.controls { border:1px solid rgba(0,0,0,.16); border-radius:.6rem; padding:.7rem; background: color-mix(in oklab, Canvas 98%, CanvasText 2%); }
label.opt { display:flex; align-items:center; gap:.5rem; }
input[type="range"] { width: 220px; }
</style>
</head>
<body>
  <!-- HEADER (title + domain/dimension + thesis) -->
  <header>
    <h1><!-- DIMENSION --> — Box of Dirt (<!-- DOMAIN -->)</h1>
    <div class="muted">Domain: <strong><!-- DOMAIN --></strong> • Dimension: <strong><!-- DIMENSION --></strong></div>
    <!-- optional thesis -->
    <!-- <p class="thesis">DIMENSION THESIS</p> -->
    <div class="card">
      <p><strong>A (Problem):</strong> <!-- A_TEXT --></p>
      <p><strong>B (Objective):</strong> <!-- B_TEXT --></p>
      <p><strong>Solution (Link):</strong> <!-- SOLUTION_TEXT --></p>
      <p class="muted small">All numbers are mocked/prefilled for narrative exploration. No backend.</p>
    </div>
  </header>

  <!-- CORE INTENT -->
  <section class="panel">
    <h2>Core Intent</h2>
    <p><!-- CORE_INTENT --></p>
  </section>

  <!-- OPTIONAL CONTROLS (only if Prototype mentions toggles/sliders; otherwise omit this block) -->
  <!--
  <section class="controls">
    <h2>Controls (mocked)</h2>
    <div class="row">
      <label class="opt"><input type="checkbox"/> Toggle A</label>
      <label class="opt"><input type="checkbox"/> Toggle B</label>
      <label class="opt">Slider C <span class="mono">50</span><input type="range" min="0" max="100" value="50"/></label>
      <button class="btn">Preset: Baseline</button>
      <button class="btn">Preset: Scenario</button>
    </div>
    <p class="muted small">Controls are non-functional and advance pre-scripted states only.</p>
  </section>
  -->

  <!-- MINIMAL BUILD / STORYBOARD -->
  <section class="panel">
    <h2>Minimal Build</h2>
    <p class="small"><span class="pill">Storyboard</span> <!-- If provided, include the Prototype’s Minimal Build title/label here --></p>
  </section>

  <!-- SCENES / PANELS (repeat per item from Prototype) -->
  <section class="grid cols-2">
    <!-- Repeat a .scene card per “Panel X — …” or “Scene X — …” from the INPUT -->
    <!-- Example scene shell; duplicate as needed and fill content -->
    <!--
    <div class="scene">
      <h4>Panel 1 — TITLE</h4>
      <div class="placeholder">[Visual description from Prototype]</div>
      <ul class="small">
        <li>Bullet 1…</li>
        <li>Bullet 2…</li>
      </ul>
      <p class="cap">Caption or note…</p>
    </div>
    -->
  </section>

  <!-- OPTIONAL METRICS / OUTCOME CARDS -->
  <!--
  <section class="panel">
    <h2>Outcome Snapshot (prefilled)</h2>
    <div class="metrics">
      <div><span class="k">Throughput</span><span class="mono">+22%</span></div>
      <div><span class="k">Lead time</span><span class="mono">−10 wks</span></div>
      <div><span class="k">CO₂e</span><span class="mono">−15%</span></div>
    </div>
  </section>
  -->

  <!-- LOAD-BEARING TEST -->
  <section class="panel">
    <h2>Load-Bearing Test — What to show</h2>
    <ul class="small">
      <!-- BULLETS FROM PROTOTYPE -->
    </ul>
  </section>

  <!-- VALIDATIONS / RED FLAGS -->
  <section class="grid cols-2">
    <div class="panel">
      <h2>Validating reactions we want</h2>
      <ul class="small"><!-- bullets --></ul>
    </div>
    <div class="panel">
      <h2>Red flags (invalidate)</h2>
      <ul class="small"><!-- bullets --></ul>
    </div>
  </section>

  <!-- FIRST EYES -->
  <section class="panel">
    <h2>First Eyes</h2>
    <ul class="small"><!-- list from Prototype --></ul>
  </section>

  <!-- WHY BOX OF DIRT -->
  <section class="panel">
    <h2>Why This is a Box of Dirt</h2>
    <ul class="small">
      <!-- Convert the Prototype’s rationale into bullets: Minimal / Disposable / Growth-inviting -->
    </ul>
    <p class="muted small">Prototype storyboard for discussion only; replace with measured data if piloted.</p>
  </section>

  <!-- (Optional) tiny inline JS only if you added mock controls to flip canned text -->
  <script>
  // If you included controls, you may wire minimal, no-network state flips here.
  // Keep it strictly cosmetic (e.g., toggling CSS classes or swapping prewritten text).
  </script>
</body>
</html>
How to map INPUT → page
Replace <!-- DOMAIN -->, <!-- DIMENSION -->, and optional thesis.
Insert Seed text into the Seed Card (A/B/Solution).
Place Prototype “Core Intent” verbatim in the Core Intent section.
Under “Minimal Build,” add the minimal build’s title/label (e.g., “Title: …”), then build one .scene card per described “Panel/Scene” with bullets and captions.
Populate Load-Bearing Test, Validations, First Eyes, and Why sections using Prototype text.
If Prototype mentions sliders/toggles/presets, include the Controls block (non-functional).
INPUT (paste your case here; the model will parse it)
Domain
[Domain Name (ignore any #IDs)]
Dimension
[Dimension Name (ignore any #IDs)]
[Optional Dimension Thesis sentence/paragraph]
Seed
A (Problem): [text]
B (Objective): [text]
Solution (Link): [text]
Prototype
Core Intent — [text]
Minimal Build — [title + description]
[Then any Panels/Scenes with headings and bullets]
Load-Bearing Test — [bullets]
Validating reactions we want — [bullets]
Red flags (invalidate) — [bullets]
First Eyes — [bulleted roles]
Why This is a Box of Dirt — [paragraph; the model will bulletize Minimal / Disposable / Growth-inviting]
"""


PROTOTYPE_SYS_MSG = """You are a narrative prototyper.
Your task is to translate narrative seeds into narrative prototypes.
A narrative prototype — also called a "box of dirt" — is a minimal, disposable artifact
that embodies the intent of the seed, tests whether the idea feels real, and invites growth into more complex systems.

Your output must include:
1. Core Intent — the smallest truth or principle the prototype tests.
2. Minimal Build — a storyboard, dashboard, flow chart, or pre-scripted simulation 
   that illustrates how the system would work. It should be conceptual and visualizable,
   not a functional application. No real backend, uploads, or panels — only mocked or prefilled flows.
3. Load-Bearing Test — what to show to first eyes and what reaction would validate it.
4. First Eyes — who to put it in front of first (supporters, skeptics, peers).
5. Why This is a Box of Dirt — how it is minimal, disposable, and growth-inviting.

Do not output code or a full implementation; focus only on the simplest conceptual sketch.

Return ONLY structured JSON in the provided schema.
"""


DIM_SYS_MSG = """You are an assistant trained to generate narrative dimensions for any given domain.
Each narrative dimension should have two parts:

1. A compressed, evocative description (1–2 sentences, almost like a thesis or proverb).
   It should feel like a distilled truth or lens, e.g., 
   "Energy is control. Empires rose with coal, oil wars redrew borders, battery supply chains shape the future."

2. A short list of concrete narrative targets that exist inside this dimension.
   These are examples, subtopics, or arenas where stories can be developed, e.g., 
   "geopolitics of oil/gas, rare earths, solar supply chains, energy security."

Output format:
[Number]. [Dimension Name] — [Thesis/Description]  
Narrative Targets: [list of 3–6 examples]

Generate 3–4 narrative dimensions unless otherwise requested.
"""

SEED_SYS_MSG = """You are an assistant trained to generate Fantasiagenesis narrative seeds.
Input:
- A narrative domain (e.g., biotechnology).
- A single narrative dimension within that domain, including its thesis/description and narrative targets.

Output:
- 3–5 narrative seeds, each framed as an A→B arc in this structure:

A (Problem): [the tension, obstacle, or deficiency in the current state]  
B (Objective): [the desired outcome or state to reach]  
Solution (Link): [the mechanism, innovation, or transformation that connects A to B]

Seeds should tie directly to the narrative targets of the dimension where possible. 
Keep each seed concise, concrete, and imaginative.

Return ONLY structured JSON in the provided schema.
"""

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
    return p if p in {"openai", "xai", "gemini", "deepseek"} else "openai"

def build_user_prompt(artifact_yaml: str) -> str:
    return f"""----------------------------------------
ARTIFACT (YAML)
# Replace everything under this line with your content.
{artifact_yaml}

----------------------------------------
RENDERING REQUIREMENTS:
- Map Artifact fields to the layout:
  - Header: header_title, thesis; toolbar with Print and header_pill.
  - Meta: Domain (label, id) and Dimension (label, id, thesis).
  - Seed: problem, objective, solution_link, scope_note.
  - “Real, deployable artifacts”: iterate real_artifacts; each becomes a <details class="card"> with <summary>{{{{title}}}}</summary> and body with Owner, description, notes.
  - “Box-of-dirt”: iterate box_of_dirt; each becomes a <details class="card"> with bullets rendered as chip-styled list items.
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
            model="gpt-5-nano-2025-08-07",
            messages=messages,
            temperature=temperature,
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

# ---------- NEW: LLM adapters (OpenAI + xAI + Gemini + Deepseek) ----------
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

@app.post("/render-seed")
def render_seed():
    payload = request.get_json(silent=True) or {}
    artifact_yaml = payload.get("artifact_yaml", "")
    model = payload.get("model")
    temperature = payload.get("temperature", 0.1)
    if not artifact_yaml or "domain:" not in artifact_yaml:
        return jsonify({"error": "artifact_yaml is required and must include YAML content."}), 400
    try:
        html = generate_seed_html(artifact_yaml, model=model, temperature=float(temperature))
        return jsonify({"html": html})
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500
