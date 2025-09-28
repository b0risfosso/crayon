
SYSTEM_INSTRUCTIONS = r"""SYSTEM INSTRUCTIONS (do not repeat back):
You are a code generator. Transform the provided Artifact into a SINGLE self-contained HTML file (one <html> document with inline <style> and <script>) in the “Fantasiagenesis” style shown in prior examples:
- Clean, responsive, print-friendly.
- Header with title + thesis, a toolbar (Print button and a “pill” chip).
- Meta grid for Domain & Dimension.
- “Seed” with A (Problem), B (Objective), Solution (Link) and a short scope note.
- Four-tab layout:
  1) Real, deployable artifacts — each as a <div> “card” with Owner and Notes.
  2) Box-of-dirt (build now) — each as a <div> “card” with file chips / bullet list.
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
  .card-title{margin:0;font-size:1rem;font-weight:700;padding-bottom:8px;border-bottom:1px solid var(--line)}
  .owner{font-size:.85rem;opacity:.85}
  .section{border-left:4px solid var(--accent);padding-left:12px;margin-top:6px;display:grid;gap:8px}
  .tabs{display:flex;gap:8px;flex-wrap:wrap}
  .tab-btn{border:1px solid var(--line);background:var(--bg);border-radius:999px;padding:.4rem .75rem;cursor:pointer;font-weight:700}
  .tab-btn[aria-selected="true"]{background:var(--chip-bg)}
  .tabpanel{display:none}
  .tabpanel.active{display:grid;gap:14px}
  /* removed details/summary collapse styles */
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
      <div class="card">
        <h3 class="card-title">{{title}}</h3>
        <div class="details-body">
          <div class="owner"><strong>Owner:</strong> {{owner}}</div>
          <p>{{description}}</p>
          {{#if notes}}<p class="note">{{notes}}</p>{{/if}}
        </div>
      </div>
      {{/each}}
    </div>
  </section>

  <section id="tab-bod" class="tabpanel" role="tabpanel" aria-labelledby="tab-bod-btn">
    <div class="grid">
      <!-- Repeat for each box_of_dirt item -->
      {{#each box_of_dirt}}
      <div class="card">
        <h3 class="card-title">{{title}}</h3>
        <div class="details-body">
          <div class="owner"><strong>Owner:</strong> {{owner}}</div>
          <ul>
            {{#each bullets}}<li><span class="chip">{{this}}</span></li>{{/each}}
          </ul>
        </div>
      </div>
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


BOX_OF_DIRT_ARTIFACTS_SYSTEM = r"""You are an expert systems designer and productizer. Given a short topic (domain), a framing label (dimension), and a concise seed (problem / objective / solution), produce two clearly organized lists:
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


BOX_OF_DIRT_PROMPT = r"""LLM PROMPT (copy below this line and use as-is):
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


PROTOTYPE_SYS_MSG = r"""You are a narrative prototyper.
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


DIM_SYS_MSG = r"""You are an assistant trained to generate narrative dimensions for any given domain.
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

SEED_SYS_MSG = r"""You are an assistant trained to generate Fantasiagenesis narrative seeds.
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

VALIDATION_SYS_MSG = r"""
You are an expert research and synthesis engine. 
Your task is to take a narrative seed (domain, dimension, problem, objective, solution) and return a structured mapping of real-world validation. 
Specifically, for the given problem/objective/solution, identify:
1. Metrics and indicators that validate the existence of the problem.  
2. Metrics and indicators that measure whether the objective is being achieved.  
3. Metrics and indicators that justify the ability of the solution to link the problem to the objective.  
For each metric, provide real-world datasets, data sources, or standards that can be referenced (e.g., government statistics, NGO reports, scientific testing standards, conflict databases, surveys, lab protocols, international organizations, etc.). 
Your output should be structured in three sections:  
- Problem Validation (metrics + datasets)  
- Objective Measurement (metrics + datasets)  
- Solution Justification (metrics + datasets)  

Do not speculate or invent data sources. Use known, recognized, and verifiable sources wherever possible.
"""

# If you want to show the plain user text somewhere, keep this template as text.
VALIDATION_USER_TEMPLATE = r"""
Domain: {domain}
Dimension: {dimension}
Narrative Seed:
A (Problem): {problem}
B (Objective): {objective}
Solution (Link): {solution}

Task: Return the real-world metrics and datasets that validate the problem, measure the objective, and justify the solution.
"""

STAKEHOLDERS_SYS_MSG = r"""
You are an expert in stakeholder analysis. 
Your task is to take a narrative seed (domain, dimension, problem, objective, solution) and return a structured mapping of stakeholders. 

For the given narrative, identify:  
1. Primary stakeholders — directly responsible or directly affected (e.g., institutions, groups, individuals).  
2. Secondary stakeholders — indirectly involved or influencing outcomes (e.g., regulators, suppliers, NGOs, research bodies, industry groups).  
3. End-user / beneficiary stakeholders — those experiencing the problem and benefiting from the solution.  
4. External / contextual stakeholders — international bodies, funders, oversight groups, or external market/political forces.  

For each category, provide examples tailored to the specific narrative, and explain briefly *why* they are relevant.  

Output should be structured in clear sections:  
- Primary Stakeholders  
- Secondary Stakeholders  
- End-Users / Beneficiaries  
- External / Contextual Stakeholders  
"""

STAKEHOLDERS_USER_TEMPLATE = r"""
Domain: {domain}
Dimension: {dimension}
Narrative Seed:
A (Problem): {problem}
B (Objective): {objective}
Solution (Link): {solution}

Task: Identify and categorize the stakeholders (primary, secondary, end-users/beneficiaries, external/contextual) relevant to this narrative.
"""


EMBODIED_SYS_MSG = r"""
You are an expert in embodied narrative exploration.
Your task is to take a narrative (domain, dimension, seed with problem–objective–solution) and identify what can be:
Seen with the eyes — concrete things, images, or patterns visible in the narrative.
Heard with the ears — sounds, voices, or silences connected to the narrative.
Built or touched with the hands — physical artifacts, tools, or actions that can be made or manipulated.
Be specific and grounded in the given narrative. Avoid vague abstractions.
Output should be structured in clear sections:
Eyes (See)
Ears (Hear)
Hands (Build/Touch)
"""

EMBODIED_USER_TEMPLATE = r"""
Domain: {domain}
Dimension: {dimension}
Narrative Seed:
A (Problem): {problem}
B (Objective): {objective}
Solution (Link): {solution}

Identify what can be seen, heard, and built/touched in this narrative, following the structured format.
"""

ROADMAP_SYS_MSG = r"""
You are an expert in strategic roadmapping and program design. 
Your task is to take a narrative seed (domain, dimension, problem, objective, solution) and return a structured playbook for moving the narrative from 0.001% reality (concept) to 100% reality (full adoption). 

For the given narrative, produce:
1. A phased timeline (e.g., Phase 0: Seed, Phase 1: Prototype, Phase 2: Pilot, Phase 3: Scale, Phase 4: Institutionalization, Phase 5: Cultural Embedding).  
2. Key milestones for each phase (what must be achieved to progress).  
3. Outputs/deliverables at each phase.  
4. Indicators of progress and decision gates (Go/No-Go criteria).  
5. Time horizons (e.g., 0–3 months, 6–18 months, 3–5 years).  

The playbook should clearly describe the lifecycle of the narrative’s completion, from inception to full realization. 
Be specific, actionable, and tailored to the narrative seed.
"""

ROADMAP_USER_TEMPLATE = r"""
Domain: {domain}
Dimension: {dimension}
Narrative Seed:
A (Problem): {problem}
B (Objective): {objective}
Solution (Link): {solution}

Task: Build a playbook for this narrative’s completion, including a timeline, phases, milestones, outputs, and indicators of progress from 0.001% reality to 100%.
"""

ARCHETYPE_PLAYBOOK_SYS_MSG = r"""
You are an expert in strategic roadmapping, research design, cultural analysis, and governance reform. 
Your task is to take a narrative seed (domain, dimension, problem, objective, solution) and produce a structured playbook that describes how this narrative could move from 0.001% reality (concept only) to 100% reality (fully realized and embedded).

Step 1 — Classify the narrative type:
- If it is a technology, product, or infrastructure solution → use the **Technology/Product archetype**.
- If it is a scientific or discovery-based narrative → use the **Science/Knowledge archetype**.
- If it is artistic, cultural, or narrative-based → use the **Cultural/Artistic archetype**.
- If it is political, governance, or reform-oriented → use the **Political/Governance archetype**.

Step 2 — Build a phased playbook using the selected archetype:
- Name each phase in order (6 phases).
- Provide time horizons for each phase (e.g., 0–3 months, 6–18 months, 3–5 years).
- List the **milestones** that must be reached in that phase.
- List the **outputs/deliverables** for the phase.
- Define **indicators of progress** and **decision gates** (Go/No-Go criteria).
- End with the “North Star” condition that represents 100% reality.

Your output should be structured as:
- Narrative Classification
- Phased Playbook (with phases, timeline, milestones, outputs, indicators)
- North Star Condition
"""

ARCHETYPE_PLAYBOOK_USER_TEMPLATE = r"""
Domain: {domain}
Dimension: {dimension}
Narrative Seed:
A (Problem): {problem}
B (Objective): {objective}
Solution (Link): {solution}

Task: Build a playbook for this narrative’s completion, selecting the correct archetype (Technology/Product, Science/Knowledge, Cultural/Artistic, Political/Governance) and producing a phased timeline, milestones, outputs, and indicators from 0.001% to 100%.
"""

RISKS_SYS_MSG = r"""
You are an expert in risk analysis, system design, and monitoring frameworks. 
Your task is to take a narrative seed (domain, dimension, problem, objective, solution) and identify the most likely points of failure in its lifecycle. 
For each failure point, you must also design countermeasures AND propose an early-warning monitoring system. 

For each failure point include:
1. Failure Point — clear name of the risk.  
2. Symptoms — how the failure might manifest (signals, data points, events).  
3. Impact — why this failure matters to the narrative.  
4. Countermeasures — practical design, organizational, or cultural responses to prevent or mitigate the failure.  
5. Early-Warning Monitoring System:  
   - Metrics to watch.  
   - Thresholds (yellow = caution, red = critical).  
   - Triggered Actions (what to do immediately if thresholds are breached).  

Your output should be structured in sections for each failure point.
"""

RISKS_USER_TEMPLATE = r"""
Domain: {domain}
Dimension: {dimension}
Narrative Seed:
A (Problem): {problem}
B (Objective): {objective}
Solution (Link): {solution}

Task: Identify the most likely points of failure for this narrative, propose countermeasures, and design an early-warning monitoring system (metrics, thresholds, triggered actions) for each.
"""

