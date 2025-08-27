from __future__ import annotations
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKIDATA_ENTITY_API = "https://www.wikidata.org/wiki/Special:EntityData/{id}.json"

# Wikidata Q-ids that indicate a company-like thing
COMPANY_P31_ALLOW = {
    "Q4830453",   # business
    "Q6881511",   # enterprise
    "Q891723",    # public company
    "Q79913",     # company
    "Q11663",     # technology company
    "Q783794",    # organization
    "Q167037",    # limited company
    "Q891723",    # public company (again for emphasis)
}

app = FastAPI(title="Company Resolver", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _shorten(text: str, n: int = 300) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"

def _is_company_entity(entity: Dict[str, Any]) -> bool:
    claims = entity.get("claims", {})
    p31s = claims.get("P31", [])
    for c in p31s:
        v = (c.get("mainsnak", {}) or {}).get("datavalue", {}) or {}
        if v.get("type") == "wikibase-entityid":
            qid = (v.get("value") or {}).get("id")
            if qid in COMPANY_P31_ALLOW:
                return True
    return False

def _label_values(entity: Dict[str, Any], pid: str) -> List[str]:
    out: List[str] = []
    for c in entity.get("claims", {}).get(pid, []):
        dv = (c.get("mainsnak", {}) or {}).get("datavalue", {}) or {}
        if not dv:
            continue
        if dv.get("type") == "string":
            out.append(dv.get("value"))
        elif dv.get("type") == "wikibase-entityid":
            vid = (dv.get("value") or {}).get("id")
            if vid:
                out.append(vid)
    return out

def _score_candidate(query: str, title: str, is_company: bool, has_ticker: bool, desc: str) -> float:
    q = query.lower()
    t = title.lower()
    score = 0.0
    if q == t or q in t:
        score += 1.0
    if is_company:
        score += 1.2
    if has_ticker:
        score += 0.6
    if "company" in desc.lower() or "corporation" in desc.lower():
        score += 0.3
    # prefer well-known suffixes
    if any(s in t for s in ("inc", "plc", "ltd", "n.v", "s.a.", "ag", "gmbh")):
        score += 0.2
    return score

async def _wiki_fetch(client: httpx.AsyncClient, params: Dict[str, Any]) -> Dict[str, Any]:
    r = await client.get(WIKI_API, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

async def wiki_search_company_biased(client: httpx.AsyncClient, query: str) -> List[str]:
    """
    Use CirrusSearch + haswbstatement:P31=<company-like QIDs> to bias towards companies.
    Returns a list of page titles (strings).
    """
    company_filter = " haswbstatement:P31=(" + "|".join(COMPANY_P31_ALLOW) + ")"
    candidates: List[str] = []

    search_variants = [
        f'{query}{company_filter}',
        f'"{query}" company',
        f'{query} inc OR plc OR ltd',
        query,
    ]

    for q in search_variants:
        data = await _wiki_fetch(client, {
            "action": "query",
            "list": "search",
            "srsearch": q,
            "srlimit": 10,
            "srqiprofile": "classic_noboostlinks",
            "srwhat": "text",
            "format": "json",
        })
        hits = (data.get("query", {}) or {}).get("search", []) or []
        for h in hits:
            t = h.get("title")
            if t and t not in candidates:
                candidates.append(t)
        if candidates:
            break  # good enough for this round

    return candidates[:10]

async def wiki_summary(client: httpx.AsyncClient, title: str) -> Optional[Dict[str, Any]]:
    r = await client.get(WIKI_SUMMARY_API.format(title=title), timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

async def wikidata_id_from_page(client: httpx.AsyncClient, title: str) -> Optional[str]:
    data = await _wiki_fetch(client, {
        "action": "query",
        "prop": "pageprops",
        "titles": title,
        "format": "json",
    })
    pages = (data.get("query", {}) or {}).get("pages", {}) or {}
    for _, page in pages.items():
        wd = (page.get("pageprops") or {}).get("wikibase_item")
        if wd:
            return wd
    return None

async def fetch_wikidata_entity(client: httpx.AsyncClient, qid: str) -> Optional[Dict[str, Any]]:
    r = await client.get(WIKIDATA_ENTITY_API.format(id=qid), timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    return (data.get("entities") or {}).get(qid)

def build_tickers(entity: Dict[str, Any]) -> List[Dict[str, str]]:
    tickers = _label_values(entity, "P249")  # ticker symbol
    exchanges = _label_values(entity, "P414")  # exchange (Q-id)
    if tickers and exchanges and len(tickers) == len(exchanges):
        return [{"symbol": s, "exchange": e} for s, e in zip(tickers, exchanges)]
    return [{"symbol": s} for s in tickers]

def confidence_for(title: str, is_company: bool, disambig: bool) -> float:
    base = 0.55
    if is_company:
        base += 0.35
    if disambig:
        base -= 0.25
    return max(0.0, min(1.0, round(base, 2)))

@app.get("/resolve_company")
async def resolve_company(
    company_name: str = Query(..., min_length=1),
    scope: Optional[str] = Query(None),
    as_of: Optional[str] = Query(None),
):
    q = company_name.strip()
    if not q:
        raise HTTPException(status_code=400, detail="company_name cannot be empty")

    async with httpx.AsyncClient(headers={"User-Agent": "CompanyResolver/1.1"}) as client:
        titles = await wiki_search_company_biased(client, q)
        if not titles:
            raise HTTPException(status_code=404, detail="No results")

        scored: List[Tuple[float, Dict[str, Any]]] = []

        for title in titles[:8]:
            summary = await wiki_summary(client, title)
            if not summary or summary.get("type") == "disambiguation":
                # try next candidate
                continue

            wd_id = await wikidata_id_from_page(client, summary.get("title") or title)
            entity = await fetch_wikidata_entity(client, wd_id) if wd_id else None

            is_company = _is_company_entity(entity) if entity else False
            tickers = build_tickers(entity) if entity else []
            has_ticker = len(tickers) > 0

            desc = (summary.get("description") or "") + " " + (summary.get("extract") or "")
            score = _score_candidate(q, summary.get("title") or title, is_company, has_ticker, desc)

            scored.append((score, {
                "summary": summary,
                "wd_id": wd_id,
                "entity": entity,
                "is_company": is_company,
                "tickers": tickers,
            }))

        if not scored:
            # As a last resort, accept a non-company page if it clearly matches name
            for title in titles[:5]:
                summary = await wiki_summary(client, title)
                if summary and summary.get("type") != "disambiguation":
                    scored.append((0.1, {"summary": summary, "wd_id": None, "entity": None, "is_company": False, "tickers": []}))
                    break

        if not scored:
            raise HTTPException(status_code=404, detail="Could not resolve a company page")

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        summary = best["summary"]

        canonical_name = summary.get("title")
        url = (summary.get("content_urls") or {}).get("desktop", {}).get("page") or \
              (summary.get("content_urls") or {}).get("mobile", {}).get("page")
        thumb = (summary.get("thumbnail") or {}).get("source")
        text = (summary.get("extract") or summary.get("description") or "")
        wd_id = best["wd_id"]
        entity = best["entity"]
        tickers = best["tickers"]
        isin_vals = _label_values(entity, "P946") if entity else []
        isin = isin_vals[0] if isin_vals else None

        resp = {
            "query": q,
            "company": {
                "canonical_name": canonical_name,
                "description": _shorten(text, 320),
                "wikipedia_url": url,
                "thumbnail": thumb,
                "wikidata_id": wd_id,
                "tickers": tickers,
                "isin": isin,
            },
            "meta": {
                "scope": scope,
                "as_of": as_of,
                "source": "wikipedia+wikidata (company-filtered)",
                "confidence": confidence_for(canonical_name, best["is_company"], False),
            },
        }
        return resp
