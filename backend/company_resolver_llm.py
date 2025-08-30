# company_resolver_llm.py
from __future__ import annotations

import os, re, time
from typing import Optional, List, Dict, Any, Tuple
from difflib import SequenceMatcher

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from openai import OpenAI


# --- TOP OF FILE: imports (ADD THIS) ---
from fastapi import APIRouter
import os

# --- OPTIONAL: tighten CORS (replace your existing CORSMiddleware block) ---
from fastapi.middleware.cors import CORSMiddleware

from backend.backbone_api import router as backbone_router

# (optional) let the model be configured via env
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-2024-08-06")  # replace your hardcoded MODEL var


# ---------------- Pydantic schema ----------------

class Ticker(BaseModel):
    symbol: str
    exchange: Optional[str] = None  # may be an exchange code or a Wikidata QID

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
    company: Optional[Company]

# ---------------- FastAPI app ----------------

app = FastAPI(title="Company Resolver (LLM + Wikidata verify)", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

app.include_router(backbone_router, prefix="/api")


# Health endpoint for nginx/systemd probes
@app.get("/healthz")
def healthz():
    return {"ok": True}



ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://fantasiagenesis.com").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

client = OpenAI()  # reads OPENAI_API_KEY

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

# ---------------- Wikidata helpers ----------------

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

# Company-like P31 candidates
COMPANY_P31 = {
    "Q79913",     # company
    "Q4830453",   # business
    "Q891723",    # public company
    "Q167037",    # limited company
    "Q11663",     # technology company
    "Q783794",    # organization (kept as a weak match)
}

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()

async def wd_search_titles(client_http: httpx.AsyncClient, name: str, limit: int = 8) -> List[Dict[str, Any]]:
    # Wikidata entity search by label/alias
    r = await client_http.get(
        WIKIDATA_API,
        params={
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "limit": limit,
            "format": "json",
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("search", []) or []

async def wd_get_entity(client_http: httpx.AsyncClient, qid: str) -> Optional[Dict[str, Any]]:
    r = await client_http.get(WIKIDATA_ENTITY.format(qid=qid), timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    return (data.get("entities") or {}).get(qid)

def wd_is_company(entity: Dict[str, Any]) -> bool:
    for claim in (entity.get("claims", {}) or {}).get("P31", []):
        dv = (claim.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type") == "wikibase-entityid":
            qid = (dv.get("value") or {}).get("id")
            if qid in COMPANY_P31:
                return True
    return False

def wd_get_sitelink_url(entity: Dict[str, Any], site: str = "enwiki") -> Optional[str]:
    sl = (entity.get("sitelinks") or {}).get(site)
    if not sl:
        return None
    # Some dumps include 'url'; if not, construct
    if "url" in sl:
        return sl["url"]
    title = sl.get("title")
    return f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}" if title else None

def wd_string_claims(entity: Dict[str, Any], pid: str) -> List[str]:
    vals = []
    for c in (entity.get("claims", {}) or {}).get(pid, []):
        dv = (c.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type") == "string":
            vals.append(dv.get("value"))
    return vals

def wd_entity_claim_qids(entity: Dict[str, Any], pid: str) -> List[str]:
    qids = []
    for c in (entity.get("claims", {}) or {}).get(pid, []):
        dv = (c.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type") == "wikibase-entityid":
            v = dv.get("value") or {}
            if v.get("id"):
                qids.append(v["id"])
    return qids

def wd_extract_facts(entity: Dict[str, Any]) -> Dict[str, Any]:
    tickers = wd_string_claims(entity, "P249")  # ticker symbol
    exchanges = wd_entity_claim_qids(entity, "P414")  # stock exchange
    isin = (wd_string_claims(entity, "P946") or [None])[0]
    name = (entity.get("labels") or {}).get("en", {}).get("value") or None
    desc = (entity.get("descriptions") or {}).get("en", {}).get("value") or None
    url = wd_get_sitelink_url(entity, "enwiki")

    # Pair tickers with exchanges when possible
    primary_tickers: List[Ticker] = []
    if tickers and exchanges and len(tickers) == len(exchanges):
        for sym, ex in zip(tickers, exchanges):
            primary_tickers.append(Ticker(symbol=sym, exchange=ex))
    elif tickers:
        primary_tickers = [Ticker(symbol=s) for s in tickers]

    return {
        "name_en": name,
        "desc_en": desc,
        "wikipedia_url": url,
        "wikidata_id": entity.get("id"),
        "isin": isin,
        "primary_tickers": primary_tickers,
        "is_company": wd_is_company(entity),
    }

def score_candidate(llm_name: str, facts: Dict[str, Any], llm_tickers: List[Ticker]) -> float:
    score = 0.0
    score += 1.4 * _sim(llm_name, facts.get("name_en") or "")
    if facts.get("is_company"):
        score += 1.0
    # ticker overlap boosts confidence
    wd_syms = {t.symbol.upper() for t in facts.get("primary_tickers") or []}
    llm_syms = {t.symbol.upper() for t in llm_tickers or []}
    if wd_syms and (wd_syms & llm_syms):
        score += 0.8
    # presence of enwiki link helps
    if facts.get("wikipedia_url"):
        score += 0.2
    return score

async def verify_with_wikidata(llm_company: Company) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns (best_facts_or_none, debug_info)
    best_facts_or_none is a dict from wd_extract_facts(...) of the best-matching item.
    """
    debug = {"candidates": []}
    async with httpx.AsyncClient(headers={"User-Agent": "CompanyResolver/3.0"}) as h:
        # Try LLM canonical name first; fallback to aliases
        queries = [llm_company.canonical_name] + (llm_company.aliases or [])
        seen_ids = set()
        best: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)

        for q in queries:
            if not q:
                continue
            hits = await wd_search_titles(h, q, limit=8)
            for hit in hits:
                qid = hit.get("id")
                if not qid or qid in seen_ids:
                    continue
                seen_ids.add(qid)
                ent = await wd_get_entity(h, qid)
                if not ent:
                    continue
                facts = wd_extract_facts(ent)
                sc = score_candidate(llm_company.canonical_name, facts, llm_company.primary_tickers)
                debug["candidates"].append({"qid": qid, "name": facts.get("name_en"), "score": sc, "is_company": facts.get("is_company")})
                if sc > best[0]:
                    best = (sc, facts)

        # As a side path: if LLM provided a unique ticker, search by ticker (P249)
        if not best[1] and llm_company.primary_tickers:
            # Try the first ticker symbol to narrow
            sym = llm_company.primary_tickers[0].symbol
            hits = await wd_search_titles(h, sym, limit=6)
            for hit in hits:
                qid = hit.get("id")
                if not qid or qid in seen_ids:
                    continue
                ent = await wd_get_entity(h, qid)
                if not ent:
                    continue
                if sym.upper() in {s.upper() for s in wd_string_claims(ent, "P249")}:
                    facts = wd_extract_facts(ent)
                    sc = score_candidate(llm_company.canonical_name, facts, llm_company.primary_tickers)
                    debug["candidates"].append({"qid": qid, "name": facts.get("name_en"), "score": sc, "is_company": facts.get("is_company"), "via": "ticker"})
                    if sc > best[0]:
                        best = (sc, facts)

        return best[1], debug

# ---------------- LLM + verify endpoint ----------------

_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}
_TTL = 300

def cache_get(k: str) -> Optional[Dict[str, Any]]:
    v = _CACHE.get(k); 
    if not v: return None
    ts, data = v
    if time.time() - ts > _TTL:
        _CACHE.pop(k, None); return None
    return data

def cache_set(k: str, data: Dict[str, Any]): _CACHE[k] = (time.time(), data)

@app.get("/resolve_company_llm")
async def resolve_company_llm(
    company_name: str = Query(..., min_length=1),
    scope: Optional[str] = Query(None),
    as_of: Optional[str] = Query(None),
):
    q = company_name.strip()
    if not q:
        raise HTTPException(status_code=400, detail="company_name cannot be empty")

    ck = f"v3::{_norm(q)}::{scope or ''}"
    cached = cache_get(ck)
    if cached:
        return cached

    # 1) LLM normalize → Pydantic
    try:
        llm_resp = client.responses.parse(
            model=MODEL,
            input=[
                {"role": "system", "content": SYSTEM_MSG},
                {"role": "user", "content": USER_TMPL.format(q=q)},
            ],
            text_format=CompanyResolution,
            temperature=0.1,
        )
        parsed: CompanyResolution = llm_resp.output_parsed
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    if not parsed.company:
        payload = {
            "query": q,
            "company": None,
            "meta": {"source": "llm.parse", "model": MODEL, "verified": False, "reason": "LLM could not confidently resolve", "scope": scope, "as_of": as_of},
        }
        cache_set(ck, payload)
        return payload

    llm_company: Company = parsed.company

    # 2) Verify/correct with Wikidata
    try:
        facts, debug = await verify_with_wikidata(llm_company)
    except Exception as e:
        facts, debug = None, {"error": f"Wikidata verify error: {e}"}

    # 3) Merge strategy:
    #    - Prefer Wikidata for: wikidata_id, wikipedia_url, tickers, ISIN.
    #    - Keep LLM canonical_name/description unless Wikidata label is a much better match.
    merged = llm_company.model_dump()
    meta = {"source": "llm+wikidata", "model": MODEL, "scope": scope, "as_of": as_of, "verified": False}

    if facts:
        meta["verified"] = facts.get("is_company", False)
        meta["wikidata_debug"] = debug
        merged["wikidata_id"] = facts.get("wikidata_id") or merged.get("wikidata_id")
        merged["wikipedia_url"] = facts.get("wikipedia_url") or merged.get("wikipedia_url")
        if facts.get("primary_tickers"):
            merged["primary_tickers"] = [t.model_dump() for t in facts["primary_tickers"]]
        if facts.get("isin"):
            merged["aliases"] = merged.get("aliases", [])
            merged["isin"] = facts["isin"]  # add even if schema didn't require it
        # If Wikidata label is very close to LLM name, adopt it (e.g., “Meta Platforms, Inc.”)
        if facts.get("name_en") and _sim(facts["name_en"], llm_company.canonical_name) >= 0.9:
            merged["canonical_name"] = facts["name_en"]
        # If Wikidata description exists and LLM description is empty/weak, adopt a trimmed one
        if (not merged.get("short_description")) and facts.get("desc_en"):
            merged["short_description"] = facts["desc_en"][:240]

    payload = {"query": q, "company": merged, "meta": meta}
    cache_set(ck, payload)
    return payload

@app.get("/api/resolve_company_llm", include_in_schema=False)
def resolve_company_llm_alias(company_name: str, scope: str | None = None, as_of: str | None = None):
    return await resolve_company_llm(company_name=company_name, scope=scope, as_of=as_of)
