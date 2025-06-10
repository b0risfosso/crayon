# metric_extractor.py
from openai import OpenAI
import hashlib, json
from typing import List
from models import Artifact, Metric

client = OpenAI()

FUNC_SPEC = {
    "type": "function",
    "function": {
        "name": "emit_metrics",
        "description": "Turn artifact outputs into measurable KPIs",
        "parameters": {
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "unit", "lower", "upper"],
                        "properties": {
                            "name":  {"type": "string"},
                            "unit":  {"type": "string"},
                            "lower": {"type": "number"},
                            "upper": {"type": "number"}
                        }
                    }
                }
            },
            "required": ["metrics"]
        }
    }
}

PROMPT_SYS = (
    "You are a QA engineer."
    "For each expected output, create a metric name, its SI/derived unit, "
    "and a sensible lower & upper bound that mark acceptable performance."
)

def extract_metrics(art: Artifact) -> List[Metric]:
    user = "EXPECTED OUTPUTS:\n" + "\n".join(f"- {o}" for o in art.expected_outputs)
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": PROMPT_SYS},
                  {"role": "user", "content": user}],
        tools=[FUNC_SPEC],
        tool_choice="auto",
        temperature=0
    )
    payload = json.loads(res.choices[0].message.tool_calls[0].function.arguments)
    out: List[Metric] = []
    for m in payload["metrics"]:
        hid = hashlib.md5((art.id + m["name"]).encode()).hexdigest()[:10]
        out.append(
            Metric(
                id=f"{art.id}_k{hid}",
                artifact_id=art.id,
                topic=art.topic,
                name=m["name"],
                unit=m["unit"],
                target_range=(m["lower"], m["upper"])
            )
        )
    return out
