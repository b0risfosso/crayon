# company_resolver_llm.py
from __future__ import annotations

import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from openai import OpenAI

# ---------------- Pydantic schema (strict, typed) ----------------

class Ticker(BaseModel):
    symbol: str
    exchange: Optional[str] = None

class Company(BaseModel):
    canonical_name: str = Field(..., description="Official name, e.g., 'Apple Inc.'")
    short_description: str = Field(..., max_length=240)
    aliases: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    headquarters_city: Optional[str] = None
    wikipedia_url: Optional[str] = None
    wikidata_id: Optional[str] = None
    primary_tickers: List[Ticker] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    disambiguation_note: Optional[str] = None

class CompanyResolution(BaseModel):
    # Allow null when we truly can't resolve confidently
    company: Optional[Company]

# ---------------- FastAPI app ----------------

app = FastAPI(title="Company Resolver (LLM+Pydantic)", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

client = OpenAI()  # reads OPENAI_API_KEY from env

SYSTEM_MSG = (
    "You normalize ambiguous company names. Rules:\n"
    "- Resolve to the globally recognized parent company unless the query clearly targets a subsidiary.\n"
    "- Prefer public companies (include main ticker if known).\n"
    "- If the query is a brand/product (e.g., Instagram), resolve to its owning company and set a disambiguation_note.\n"
    "- Keep short_description ≤ 240 chars, neutral.\n"
    "- If you cannot confidently resolve a real company, return company = null.\n"
    "- Do NOT invent tickers/IDs/links—omit them if unsure."
)

USER_TMPL = (
    "Query: {q}\n\n"
    "Return ONLY the object matching the provided schema. If ambiguous with non-company entities "
    "(songs/films/TV/etc.), resolve to the company and explain briefly in disambiguation_note."
)

MODEL = "gpt-4o-2024-08-06"

@app.get("/resolve_company_llm")
def resolve_company_llm(
    company_name: str = Query(..., min_length=1),
    scope: Optional[str] = Query(None),
    as_of: Optional[str] = Query(None),
):
    q = company_name.strip()
    if not q:
        raise HTTPException(status_code=400, detail="company_name cannot be empty")

    try:
        # Responses.parse with Pydantic model (structured outputs)
        resp = client.responses.parse(
            model=MODEL,
            input=[
                {"role": "system", "content": SYSTEM_MSG},
                {"role": "user", "content": USER_TMPL.format(q=q)},
            ],
            text_format=CompanyResolution,  # <-- Pydantic schema
            temperature=0.1,
        )
        parsed: CompanyResolution = resp.output_parsed  # <-- typed result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # Return a clean JSON payload your UI can consume
    return {
        "query": q,
        "company": parsed.company.model_dump() if parsed.company else None,
        "meta": {
            "source": "llm.parse",
            "model": MODEL,
            "scope": scope,
            "as_of": as_of,
        },
    }
