# /var/www/site/current/backend/prompts.py

BRIDGE_PROMPT_TEMPLATE = """You will be given two documents.
Treat each document as a little world with its own assets (strengths, resources, concepts, features) and needs (gaps, weaknesses, desires, problems).
Your task is to:
Understand each world,
Design value exchanges at the boundary between the two worlds, and
For each value exchange, explain both how to build it and how to measure it.

1. Input Documents
Document A:
{{DOC1}}

Document B:
{{DOC2}}

2. Your Tasks

Step 1 – Extract the “Worlds”
For each document:
Identify that document’s assets
- Capabilities, strengths, unique ideas, content, products, stories, etc.
Identify that document’s needs
- Pain points, missing pieces, implied desires, opportunities, or weaknesses.

Output this as:
World A – Assets: …
World A – Needs: …
World B – Assets: …
World B – Needs: …

Keep this section concise but specific (3–7 bullets per list).

Step 2 – Propose Value Exchanges at the Boundary
Now imagine value flowing between these two worlds.
Create 3–7 distinct value exchanges where:
- An asset in World A satisfies a need in World B, and/or
- An asset in World B satisfies a need in World A.

For each value exchange:
- Give it a short, descriptive name.
- Describe what is being exchanged and why it’s valuable to each side.

Format example:
Exchange Name
From A to B: what A gives to B.
From B to A: what B gives to A.
Why it matters: short explanation of the mutual benefit.

Step 3 – For Each Exchange, Explain How to Build It
For each value exchange from Step 2, describe how one would actually implement it in practice.

Be concrete. Think in terms such as:
- Products or systems
- Workflows, processes, or cooperations
- Technologies or tools

For each exchange, include:
Implementation Plan
- 3–7 bullet points
- Focus on practical steps that someone could actually execute.

Step 4 – For Each Exchange, Explain How to Measure It
For each value exchange, define how success would be measured.

Specify:
- What to measure (KPIs, signals, or outcomes)
- How to measure (methods, tools, experiments, or data sources)

Measurement Framework
- 3–5 bullet points per exchange
- Be specific about metrics and methods.
Notes:
- Use concrete details from the documents to ground your analysis.
- Avoid vague generalities.
- Focus on practical, actionable insights.

3. Output Format
Your final answer should follow this structure:

World A Overview
- Assets
- Needs

World B Overview
- Assets
- Needs

Value Exchanges
For each exchange (e.g., 1–5):
- Name
- From A to B
- From B to A
- Why it matters
- Implementation Plan
- Measurement Framework

Be specific, avoid vague generalities, and keep everything grounded in the actual content of the two documents.
"""


def build_bridge_prompt(doc1: str, doc2: str) -> str:
    """
    Take two document excerpts and inject them into the template.
    We keep the {{DOC1}} and {{DOC2}} markers in the prompt file
    so the template is easy to read and edit.
    """
    prompt = BRIDGE_PROMPT_TEMPLATE.replace("{{DOC1}}", doc1)
    prompt = prompt.replace("{{DOC2}}", doc2)
    return prompt
