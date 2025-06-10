# artifact_generator.py
from openai import OpenAI
from typing import List
import hashlib, json
from models import Artifact, Mechanism
from neo4j import GraphDatabase
from loader import driver   # reuse Neo4j driver
import random, math
import tiktoken     # pip install tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")

MAX_TOK = 7000      # hard cap for user_msg

def _token_len(text: str) -> int:
    return len(enc.encode(text))

client = OpenAI()
GEN_FUNC = {
    "type": "function",
    "function": {
        "name": "emit_artifacts",
        "description": "Combine mechanisms into buildable artifacts",
        "parameters": {
            "type": "object",
            "properties": {
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "name", "description",
                            "mechanism_ids", "expected_outputs",
                            "trl"
                        ],
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "mechanism_ids": {
                                "type": "array", "items": {"type": "string"}
                            },
                            "expected_outputs": {
                                "type": "array", "items": {"type": "string"}
                            },
                            "trl": {"type": "integer", "minimum": 1, "maximum": 9}
                        }
                    }
                }
            },
            "required": ["artifacts"]
        }
    }
}



def generate_artifacts(topic: str,
                       mechs: List[Mechanism],
                       max_artifacts: int = 3) -> List[Artifact]:

    # 1️⃣  shuffle mechanisms in-place (copy first to not mutate caller)
    shuffled = mechs[:]
    random.shuffle(shuffled)

    # 2️⃣  build summaries until we hit ~7k tokens
    lines, tok_so_far = [], 0
    for m in shuffled:
        line = f"- {m.id}: {m.principle}, outputs {m.outputs}"
        line_tok = _token_len(line + "\n")
        if tok_so_far + line_tok > MAX_TOK:
            break
        lines.append(line)
        tok_so_far += line_tok

    mech_summaries = "\n".join(lines)
    user_msg = f"TOPIC: {topic}\nMECHANISMS:\n{mech_summaries}"

    SYS_PROMPT = (
        "You are a design engineer. "
        "Given a list of mechanisms for the SAME topic, "
        f"bundle compatible ones into up to {max_artifacts} concrete, buildable artifacts. "
        "Each artifact must:\n"
        "• have a short name\n"
        "• describe how the mechanisms chain together\n"
        "• list mechanism IDs used (ordered)\n"
        "• predict key outputs/performance\n"
        "• assign a Technology Readiness Level (1 research – 9 market)\n"
        "Return JSON via the function spec."
    )

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user",   "content": user_msg}
        ],
        tools=[GEN_FUNC],
        tool_choice="auto",
        temperature=0
    )

    payload = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
    arts_raw = payload["artifacts"][:max_artifacts]

    out: List[Artifact] = []
    for art in arts_raw:
        hash_id = hashlib.md5(
            (topic + art["name"] + "".join(art["mechanism_ids"])).encode()
        ).hexdigest()[:10]
        out.append(
            Artifact(
                id=f"{topic.replace(' ', '_')}_a{hash_id}",
                topic=topic,
                name=art["name"],
                description=art["description"],
                principle_chain=art["mechanism_ids"],
                expected_outputs=art["expected_outputs"],
                trl=art["trl"]
            )
        )
    return out