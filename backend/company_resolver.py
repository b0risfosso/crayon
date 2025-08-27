# file: company_resolver_llm.py
from __future__ import annotations
import os, time, json
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# --- OpenAI client (install: pip install openai==1.*)
from openai import OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="Company Resolver (LLM)", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# --------- Response schema we want back from the LLM ----------
class Ticker(BaseModel):
    symbol: str
    exchange: Optional[str] = None

class LLMCompany(BaseModel):
    canonical_name: str = Field(..., description="Official company name, e.g., 'Apple Inc.'")
    short_description: str = Field(..., description="<= 240 chars.")
    aliases: list[str] = Field(default_factory=list, description="Common aliases/brands.")
    country: Optional[str] = None
    headquarters_city: Optional[str] = None
    wikipedia_url: Optional[str] = None
    wikidata_id: Optional[str] = None
    primary_tickers: list[Ticker] = Field(default_factory=list, description="Main stock tickers if any.")
    # useful for UI gating:
    confidence: float = Field(..., ge=0, le=1)
    disambiguation_note: Optional[str] = None

class LLMResolveResponse(BaseModel):
    query: str
    company: Optional[LLMCompany] = None
    meta: Dict[str, Any] = {}

JSON_SCHEMA = {
    "name": "CompanyResolution",
    "schema": {
        "type": "object",
        "properties": {
            "company": {
                "type": ["object", "null"],
                "properties": {
                    "canonical_name": {"type": "string"},
                    "short_description": {"type": "string", "maxLength": 240},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "country": {"type": ["string", "null"]},
                    "headquarters_city": {"type": ["string", "null"]},
                    "wikipedia_url": {"type": ["string", "null"]},
                    "wikidata_id": {"type": ["string", "null"]},
                    "primary_tickers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "exchange": {"type": ["string", "null"]},
                            },
                            "required": ["symbol"],
                            "additionalProperties": False
                        }
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "disambiguation_note": {"type": ["string", "null"]},
                },
                "required": ["canonical_name", "short_description", "confidence", "primary_tickers"],
                "additionalProperties": False
            }
        },
        "required": ["company"],
        "additionalProperties": False
    }
}

SYSTEM_PROMPT = """You normalize ambiguous company names.
Rules:
- Resolve to the globally recognized parent company unless the query clearly targets a subsidiary or unrelated entity.
- Prefer entities with stock tickers (if public). If multiple, pick the best-known global listing.
- If the query is a brand/product (e.g., 'Instagram'), resolve to its owning company with a disambiguation_note.
- If the query is ambiguous ('Meta', 'Square', 'Xiaomi'), choose the most globally recognized company.
- Keep short_description ≤ 240 chars, neutral tone.
- If you can't confidently resolve to a real company, return company=null (but still valid JSON).
- NEVER invent tickers, urls, or IDs if you’re unsure; omit them instead.
"""

USER_PROMPT_TEMPLATE = """Query: {q}

Return a JSON that strictly matches the provided schema. 
If you suspect confusion with non-company pages (TV shows, songs, etc.), resolve to the company and explain briefly in disambiguation_note."""

def call_llm(query: str) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    # OpenAI Responses API with JSON schema
    resp = client.responses.create(
        model="gpt-4.1-mini",  # light; use a larger model if you want
        temperature=0.1,
        system=SYSTEM_PROMPT,
        input=USER_PROMPT_TEMPLATE.format(q=query),
        response_format={
            "type": "json_schema",
            "json_schema": JSON_SCHEMA,
            "strict": True
        },
    )
    # The result JSON is in resp.output_text when using response_format=json_schema
    data = json.loads(resp.output_text)
    return data

# tiny in-memory cache (helps when users try a few times)
_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}
_TTL = 300  # seconds

def cache_get(key: str) -> Optional[Dict[str, Any]]:
    v = _CACHE.get(key)
    if not v: return None
    ts, data = v
    if time.time() - ts > _TTL:
        _CACHE.pop(key, None); return None
    return data

def cache_set(key: str, data: Dict[str, Any]):
    _CACHE[key] = (time.time(), data)

@app.get("/resolve_company_llm", response_model=LLMResolveResponse)
def resolve_company_llm(
    company_name: str = Query(..., min_length=1),
    scope: Optional[str] = Query(None),
    as_of: Optional[str] = Query(None),
):
    q = company_name.strip()
    if not q:
        raise HTTPException(status_code=400, detail="company_name cannot be empty")

    ck = q.lower()
    cached = cache_get(ck)
    if cached:
        return {"query": q, "company": cached.get("company"), "meta": {"source": "llm-cache", "scope": scope, "as_of": as_of}}

    try:
        data = call_llm(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # Validate minimally with Pydantic before returning
    try:
        company = data.get("company", None)
        if company is not None:
            _ = LLMCompany(**company)  # validation
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema validation failed: {e}")

    cache_set(ck, data)
    return {"query": q, "company": data.get("company"), "meta": {"source": "llm", "scope": scope, "as_of": as_of}}
