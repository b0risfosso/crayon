# mechanism_extractor.py
from openai import OpenAI
from typing import List
import hashlib, json
from models import Mechanism, Source

client = OpenAI()

FUNC_SPEC = {
    "type": "function",
    "function": {
        "name": "emit_mechanisms",
        "description": "Return up to N mechanisms from the given paper",
        "parameters": {
            "type": "object",
            "properties": {
                "mechanisms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["inputs", "outputs", "principle", "confidence"],
                        "properties": {
                            "inputs":     {"type": "array", "items": {"type": "string"}},
                            "outputs":    {"type": "array", "items": {"type": "string"}},
                            "principle":  {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                        },
                    }
                }
            },
            "required": ["mechanisms"]
        },
    }
}

SYS_PROMPT = (
    "Extract mechanistic statements in the form "
    "inputs ➜ principle ➜ outputs from the paper abstract. "
    "Return 1-3 mechanisms ordered by importance."
)

def extract_mechanisms(src: Source, max_mechs: int = 3) -> List[Mechanism]:
    messages = [
        {"role": "system", "content": SYS_PROMPT},
        {"role": "user", "content": f"TITLE: {src.title}\nABSTRACT: {src.summary}\nN={max_mechs}"}
    ]

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=[FUNC_SPEC],
        tool_choice="auto",
        temperature=0
    )

    tool_call = resp.choices[0].message.tool_calls[0]
    args_json = tool_call.function.arguments    # ← dot access
    payload   = json.loads(args_json)

    mechs_raw = payload["mechanisms"][:max_mechs]

    out: List[Mechanism] = []
    for idx, m in enumerate(mechs_raw, 1):
        mid = hashlib.md5(f"{src.id}_{idx}_{m['principle']}".encode()).hexdigest()[:10]
        out.append(
            Mechanism(
                id=f"{src.id}_m{mid}",
                source_id=src.id,
                topic=src.topic,
                inputs=m["inputs"],
                outputs=m["outputs"],
                principle=m["principle"],
                confidence=float(m["confidence"])
            )
        )
    return out
