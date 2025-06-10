from openai import OpenAI
import hashlib, json
from typing import List
from models import Risk, Control, Artifact, Mechanism

client = OpenAI()

FUNC_SPEC = {
    "type": "function",
    "function": {
        "name": "emit_risks_controls",
        "description": "Identify failure modes and mitigations for an artifact",
        "parameters": {
            "type": "object",
            "properties": {
                "risks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "description", "severity", "likelihood",
                            "control_description", "effectiveness", "cost_level"
                        ],
                        "properties": {
                            "description":        {"type": "string"},
                            "severity":           {"type": "string"},
                            "likelihood":         {"type": "string"},
                            "control_description":{"type": "string"},
                            "effectiveness":      {"type": "string"},
                            "cost_level":         {"type": "string"}
                        }
                    }
                }
            },
            "required": ["risks"]
        }

    }
}

SYS_PROMPT = (
    "You are a safety-critical design auditor. "
    "Given the artifact description and its enabling mechanisms, "
    "list up to 5 major risks/failure modes with a one-to-one mitigation. "
    "Follow the function schema exactly."
)

def extract_risks_controls(
    art: Artifact,
    mechanisms: List[Mechanism],
    max_risks: int = 5
) -> tuple[list[Risk], list[Control]]:
    
    mech_bullets = "\n".join(f"- {m.id}: {m.principle}" for m in mechanisms)
    user_msg = (
        f"ARTIFACT: {art.name}\n"
        f"DESCRIPTION: {art.description}\n"
        f"ENABLING MECHANISMS:\n{mech_bullets}\n"
        f"MAX_RISKS={max_risks}"
    )
    
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user",   "content": user_msg}
        ],
        tools=[FUNC_SPEC],
        tool_choice="auto",
        temperature=0
    )
    
    payload = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
    risks_raw = payload["risks"][:max_risks]
    
    risks: list[Risk] = []
    controls: list[Control] = []
    for idx, r in enumerate(risks_raw, 1):
        h = hashlib.md5((art.id + r["description"]).encode()).hexdigest()[:10]
        rid = f"{art.id}_r{h}"
        cid = f"{art.id}_c{h}"
        risks.append(
            Risk(
                id=rid,
                artifact_id=art.id,
                topic=art.topic,
                description=r["description"],
                severity=r["severity"],
                likelihood=r["likelihood"]
            )
        )
        controls.append(
            Control(
                id=cid,
                risk_id=rid,
                artifact_id=art.id,
                description=r["control_description"],
                effectiveness=r["effectiveness"],
                cost_level=r["cost_level"]
            )
        )
    return risks, controls
