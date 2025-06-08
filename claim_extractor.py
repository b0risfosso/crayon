# claim_extractor.py
from openai import OpenAI
import json, hashlib
from typing import List
from models import Claim, Source

client = OpenAI()

SYS_PROMPT = """
You are an expert scientific summariser.
Extract up to {k} core, mechanistic claims from the paper summary.
Return JSON list. Each item MUST have keys:
 text  – one sentence claim
 confidence – float 0-1 (your confidence)
"""

def clean_json_string(s: str) -> str:
    # Remove ```json ... ```
    if s.startswith("```json"):
        s = s[len("```json"):].strip()
    if s.startswith("```"):
        s = s[len("```"):].strip()
    if s.endswith("```"):
        s = s[:-3].strip()
    return s


def extract_claims(src: Source, k: int = 5) -> List[Claim]:
    prompt = SYS_PROMPT.format(k=k) + "\n\nPAPER SUMMARY:\n" + src.summary
    out = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=600
    ).choices[0].message.content.strip()

    json_out = clean_json_string(out)
    try:
        claims_raw = json.loads(json_out)
    except json.JSONDecodeError:
        print(json_out)
        raise ValueError("LLM did not return valid JSON")

    claims: List[Claim] = []
    for n, c in enumerate(claims_raw, 1):
        cid = hashlib.md5(f"{src.id}_{n}_{c['text']}".encode()).hexdigest()[:10]
        claims.append(
            Claim(
                id=f"{src.id}_c{cid}",
                source_id=src.id,
                topic=src.topic,
                text=c["text"],
                confidence=float(c["confidence"]),
            )
        )
    return claims
