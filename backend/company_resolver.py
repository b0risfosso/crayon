from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

WIKI_SEARCH_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKIDATA_ENTITY_API = "https://www.wikidata.org/wiki/Special:EntityData/{id}.json"

app = FastAPI(title="Company Resolver", version="1.0.0")

# Allow your HTML file to call this API from localhost or your domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Helpers
# -----------------------------
def _is_probably_company(title: str) -> bool:
    title_lower = title.lower()
    return any(
        kw in title_lower
        for kw in ["inc", "corp", "corporation", "company", "co.", "ltd", "plc", "nv", "se", "ag", "sa", "sas", "gmbh", "oyj", "ab"]
    )

def _shorten(text: str, n: int = 300) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"

def _label_value(entity: Dict[str, Any], pid: str) -> List[str]:
    """Return a list of string values for a Wikidata property ID."""
    out: List[str] = []
    claims = entity.get("claims", {}).get(pid, [])
    for c in claims:
        m = c.get("mainsnak", {})
        dv = m.get("datavalue", {})
        if not dv:
            continue
        if dv.get("type") == "string":
            out.append(dv.get("value"))
        elif dv.get("type") == "wikibase-entityid":
            # For exchange (P414), get the Q-id label later if needed.
            v = dv.get("value", {})
            if "id" in v:
                out.append(v["id"])
    return out

async def wiki_search(client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
    # Try biasing toward companies first
    queries = [f"{query} company", query]
    for q in queries:
        r = await client.get(
            WIKI_SEARCH_API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": q,
                "format": "json",
                "srlimit": 10,
                "srprop": "",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        hits = data.get("query", {}).get("search", []) or []
        if hits:
            return hits
    return []

async def wiki_summary(client: httpx.AsyncClient, title: str) -> Optional[Dict[str, Any]]:
    r = await client.get(WIKI_SUMMARY_API.format(title=title), timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

async def wikidata_from_page(client: httpx.AsyncClient, title: str) -> Optional[str]:
    # Get pageprops via API to find wikibase_item (Wikidata ID)
    r = await client.get(
        WIKI_SEARCH_API,
        params={
            "action": "query",
            "prop": "pageprops",
            "titles": title,
            "format": "json",
        },
        timeout=15,
    )
    r.raise_for_status()
    pages = (r.json().get("query", {}) or {}).get("pages", {}) or {}
    for _, page in pages.items():
        wd = page.get("pageprops", {}).get("wikibase_item")
        if wd:
            return wd
    return None

async def fetch_wikidata_entity(client: httpx.AsyncClient, qid: str) -> Optional[Dict[str, Any]]:
    r = await client.get(WIKIDATA_ENTITY_API.format(id=qid), timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    return data.get("entities", {}).get(qid)

def build_tickers(entity: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract tickers from Wikidata (P249 ticker symbol, P414 stock exchange)."""
    tickers = _label_value(entity, "P249")  # ticker symbol
    exchanges = _label_value(entity, "P414")  # stock exchange (Q-id)
    # We don't resolve exchange Q-ids to labels here (keep it minimal).
    # Pair them if lengths match; otherwise just return symbols.
    if tickers and exchanges and len(tickers) == len(exchanges):
        return [{"symbol": s, "exchange": e} for s, e in zip(tickers, exchanges)]
    return [{"symbol": s} for s in tickers]

def confidence_for(title: str, disambiguation: bool) -> float:
    base = 0.6
    if _is_probably_company(title):
        base += 0.25
    if disambiguation:
        base -= 0.2
    return max(0.0, min(1.0, round(base, 2)))

# -----------------------------
# API
# -----------------------------
@app.get("/resolve_company")
async def resolve_company(
    company_name: str = Query(..., min_length=1, description="Raw user input for company"),
    scope: Optional[str] = Query(None),
    as_of: Optional[str] = Query(None),
):
    q = company_name.strip()
    if not q:
        raise HTTPException(status_code=400, detail="company_name cannot be empty")

    async with httpx.AsyncClient(headers={"User-Agent": "CompanyResolver/1.0"}) as client:
        # 1) Search
        hits = await wiki_search(client, q)
        if not hits:
            raise HTTPException(status_code=404, detail="No Wikipedia results for that query")

        # Prefer titles that look like companies
        def score_hit(h: Dict[str, Any]) -> int:
            t = h.get("title", "")
            return (2 if _is_probably_company(t) else 0) + (1 if q.lower() in t.lower() else 0)

        hits_sorted = sorted(hits, key=score_hit, reverse=True)
        best_title = hits_sorted[0]["title"]

        # 2) Summary (detect disambiguation and fallbacks)
        summary = await wiki_summary(client, best_title)
        if not summary:
            # try the next hit if summary missing
            for h in hits_sorted[1:]:
                summary = await wiki_summary(client, h["title"])
                if summary:
                    best_title = h["title"]
                    break

        if not summary:
            raise HTTPException(status_code=404, detail="Could not retrieve a valid summary")

        is_disambig = bool(summary.get("type") == "disambiguation")
        # If disambiguation, scan other hits for a better company-like title
        if is_disambig:
            for h in hits_sorted[1:]:
                if _is_probably_company(h["title"]):
                    maybe = await wiki_summary(client, h["title"])
                    if maybe and maybe.get("type") != "disambiguation":
                        summary = maybe
                        best_title = h["title"]
                        is_disambig = False
                        break

        canonical_name = summary.get("title") or best_title
        short_desc = summary.get("description") or ""
        extract = summary.get("extract") or ""
        text = extract or short_desc or ""

        url = summary.get("content_urls", {}).get("desktop", {}).get("page") or summary.get("content_urls", {}).get("mobile", {}).get("page")
        thumb = (summary.get("thumbnail") or {}).get("source")

        # 3) Wikidata (optional)
        wikidata_id = await wikidata_from_page(client, canonical_name)
        tickers: List[Dict[str, str]] = []
        isin: Optional[str] = None

        if wikidata_id:
            entity = await fetch_wikidata_entity(client, wikidata_id)
            if entity:
                tickers = build_tickers(entity)
                # ISIN is P946 on Wikidata
                isin_vals = _label_value(entity, "P946")
                isin = isin_vals[0] if isin_vals else None

        resp = {
            "query": q,
            "company": {
                "canonical_name": canonical_name,
                "description": _shorten(text, 320),
                "wikipedia_url": url,
                "thumbnail": thumb,
                "wikidata_id": wikidata_id,
                "tickers": tickers,
                "isin": isin,
            },
            "meta": {
                "scope": scope,
                "as_of": as_of,
                "source": "wikipedia+wikidata",
                "confidence": confidence_for(canonical_name, is_disambig),
            },
        }
        return resp
