# backend/backbone_resolver.py
from __future__ import annotations
import os, re, asyncio, json
from typing import Optional, Dict, Any, List
import httpx

from backend.backbone_models import CompanyBackbone, Ticker

# --- config
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "http://127.0.0.1:8000/resolve_company_llm")
USER_AGENT = os.environ.get("SEC_USER_AGENT", "CompanyEvalBot/1.0 (email@example.com)")

WIKI_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

# --- SEC helpers (replace your existing ones) ---
SEC_TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"
SEC_TICKERS_EX_JSON = "https://www.sec.gov/files/company_tickers_exchange.json"

GLEIF_API = "https://api.gleif.org/api/v1/lei-records"

COMPANY_P31 = {"Q79913","Q4830453","Q891723","Q167037","Q11663","Q783794"}

def _domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url: return None
    m = re.match(r"^https?://([^/]+)/?", url.strip(), re.I)
    return m.group(1).lower() if m else None

def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

async def call_llm_normalizer(client: httpx.AsyncClient, query: str) -> Optional[Dict[str, Any]]:
    r = await client.get(LLM_ENDPOINT, params={"company_name": query}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("company")

# ---- Wikidata helpers
async def wd_search(client: httpx.AsyncClient, name: str, limit=6) -> List[Dict[str, Any]]:
    r = await client.get(WIKI_API, params={
        "action":"wbsearchentities","search":name,"language":"en","type":"item","limit":limit,"format":"json"
    }, timeout=25)
    r.raise_for_status()
    return r.json().get("search",[]) or []

async def wd_get_entity(client: httpx.AsyncClient, qid: str) -> Optional[Dict[str, Any]]:
    r = await client.get(WIKIDATA_ENTITY.format(qid=qid), timeout=25)
    if r.status_code==404: return None
    r.raise_for_status()
    return r.json().get("entities",{}).get(qid)

def wd_is_company(entity: Dict[str, Any]) -> bool:
    for c in (entity.get("claims",{}) or {}).get("P31",[]):
        dv = (c.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type")=="wikibase-entityid":
            if (dv.get("value") or {}).get("id") in COMPANY_P31: return True
    return False

def wd_str_claims(entity: Dict[str,Any], pid:str) -> List[str]:
    vals=[]
    for c in (entity.get("claims",{}) or {}).get(pid,[]):
        dv=(c.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type")=="string": vals.append(dv.get("value"))
    return vals

def wd_qids(entity: Dict[str,Any], pid:str) -> List[str]:
    out=[]
    for c in (entity.get("claims",{}) or {}).get(pid,[]):
        dv=(c.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type")=="wikibase-entityid":
            q=(dv.get("value") or {}).get("id")
            if q: out.append(q)
    return out

def wd_sitelink(entity: Dict[str,Any], site="enwiki") -> Optional[str]:
    sl=(entity.get("sitelinks") or {}).get(site)
    if not sl: return None
    title=sl.get("title")
    return f"https://en.wikipedia.org/wiki/{title.replace(' ','_')}" if title else None

def build_from_wikidata(entity: Dict[str,Any]) -> Dict[str,Any]:
    label = (entity.get("labels") or {}).get("en",{}).get("value")
    desc = (entity.get("descriptions") or {}).get("en",{}).get("value")
    website = (entity.get("claims",{}).get("P856",[{}])[0].get("mainsnak",{}).get("datavalue",{}) or {}).get("value")
    isin = (wd_str_claims(entity,"P946") or [None])[0]
    tickers = wd_str_claims(entity,"P249")
    exchanges = wd_qids(entity,"P414")
    wikipedia_url = wd_sitelink(entity,"enwiki")
    tick_objs=[]
    if tickers:
        if exchanges and len(exchanges)==len(tickers):
            tick_objs=[Ticker(symbol=s, exchange_qid=q) for s,q in zip(tickers,exchanges)]
        else:
            tick_objs=[Ticker(symbol=s) for s in tickers]
    return {
        "label": label, "desc": desc, "website": website, "isin": isin,
        "tickers": tick_objs, "wikipedia_url": wikipedia_url, "wikidata_id": entity.get("id"),
        "is_company": wd_is_company(entity)
    }

# ---- SEC helpers
async def sec_load_maps(client: httpx.AsyncClient) -> Dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    async def fetch(url: str):
        r = await client.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    base = await fetch(SEC_TICKERS_JSON)      # keys: "0","1",... with cik_str,ticker,title
    ex   = await fetch(SEC_TICKERS_EX_JSON)   # may include exchange

    tmap: Dict[str, Dict[str, Any]] = {}

    def ingest(obj):
        # Accept dict-of-dicts or list-of-dicts
        if isinstance(obj, dict):
            rows = obj.values()
        elif isinstance(obj, list):
            rows = obj
        else:
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = (row.get("ticker") or row.get("symbol") or "").upper()
            if not ticker:
                continue
            cik_raw = row.get("cik") or row.get("cik_str") or row.get("cikStr")
            title   = row.get("title") or row.get("name") or row.get("entityName")
            exch    = row.get("exchange") or row.get("exchangeShortName") or row.get("primaryExchange")

            cik_fmt = None
            if cik_raw is not None:
                try:
                    cik_fmt = f"{int(cik_raw):010d}"
                except Exception:
                    pass

            rec = tmap.setdefault(ticker, {})
            if cik_fmt: rec["cik"] = cik_fmt
            if title:   rec["title"] = title
            if exch:    rec["exchange"] = exch

    ingest(base)
    ingest(ex)
    return tmap

def sec_find_cik_by_ticker(tmap: Dict[str, Any], symbol: str) -> Optional[str]:
    row = tmap.get(symbol.upper())
    return (row or {}).get("cik")

def sec_find_cik_by_name(tmap: Dict[str, Any], legal_name: str) -> Optional[tuple[str, str]]:
    target = norm_name(legal_name)
    best, best_row = 0.0, None
    for sym, row in tmap.items():
        title = norm_name((row or {}).get("title", ""))
        score = 1.0 if target == title else (0.85 if target in title or title in target else 0.0)
        if score > best and row.get("cik"):
            best, best_row = score, (row["cik"], sym)
    return best_row if best >= 0.85 else None
    

# ---- GLEIF LEI
async def gleif_find_lei(client: httpx.AsyncClient, legal_name: str) -> Optional[str]:
    r = await client.get(GLEIF_API, params={"filter[entity.legalName]": legal_name, "page[size]": 1}, timeout=25)
    r.raise_for_status()
    data=r.json().get("data",[])
    if not data: return None
    return (data[0].get("attributes") or {}).get("lei")

# Add near the top
def canonical_site(url: Optional[str]) -> Optional[str]:
    if not url: return None
    m = re.match(r"^https?://([^/]+)", url.strip(), re.I)
    if not m: return None
    host = m.group(1).lower()
    # collapse regional subpaths like apple.com/at → apple.com
    base = host.split(":")[0]
    return f"https://{base}/"

def norm_name(s: str) -> str:
    s = _norm(s).upper()
    s = re.sub(r"[.,'&]", "", s)
    s = re.sub(r"\b(INC|INCORPORATED|CORP|CORPORATION|PLC|LTD|LIMITED|N\.V|S\.A|AG)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s




# ---- Main: build backbone
async def build_backbone(query: str) -> CompanyBackbone:
    async with httpx.AsyncClient(headers={"User-Agent":"BackboneAgent/1.0"}) as client:
        # 1) LLM normalization (your resolver)
        llm = await call_llm_normalizer(client, query)
        if not llm:
            raise ValueError("LLM resolver could not confidently resolve the company.")

        name = _norm(llm.get("canonical_name") or query)
        aliases = llm.get("aliases") or []

        # 2) Wikidata verification/enrichment
        # prefer exact QID if LLM returned one; else search by name then aliases
        qid = llm.get("wikidata_id")
        entity=None
        if qid:
            entity = await wd_get_entity(client, qid)
        if not entity:
            for term in [name] + aliases:
                hits = await wd_search(client, term, limit=6)
                for h in hits:
                    e = await wd_get_entity(client, h["id"])
                    if e and wd_is_company(e): entity=e; break
                if entity: break

        wiki_facts = build_from_wikidata(entity) if entity else {}
        official_site = canonical_site(wiki_facts.get("website")) or canonical_site(llm.get("wikipedia_url"))
        domain = _domain_from_url(official_site)
        tickers: List[Ticker] = wiki_facts.get("tickers") or []

        # 3) SEC CIK (if any US listing)
        cik = None
        tmap = await sec_load_maps(client)
        if tickers:
            for t in tickers:
                cik = sec_find_cik_by_ticker(tmap, t.symbol)
                if cik: break
        if not cik:
            # fallback: match by name (e.g., “Apple Inc” → CIK 0000320193 + AAPL)
            by_name = sec_find_cik_by_name(tmap, llm.get("canonical_name") or wiki_facts.get("label") or query)
            if by_name:
                cik, sym = by_name
                if not tickers:
                    tickers = [Ticker(symbol=sym)]
                    
        # 4) LEI (try LLM name, then wiki label)
        lei = await gleif_find_lei(client, llm.get("canonical_name")) or \
              (await gleif_find_lei(client, wiki_facts.get("label")) if wiki_facts.get("label") else None)

        # 5) Compose backbone
        bb = CompanyBackbone(
            canonical_name = llm.get("canonical_name") or wiki_facts.get("label") or query,
            aliases = aliases,
            country = llm.get("country"),
            headquarters_city = llm.get("headquarters_city"),
            official_website = official_site,
            domain = domain,
            wikipedia_url = wiki_facts.get("wikipedia_url") or llm.get("wikipedia_url"),
            wikidata_id = wiki_facts.get("wikidata_id") or llm.get("wikidata_id"),
            is_public = bool(tickers),
            tickers = tickers,
            isin = wiki_facts.get("isin"),
            cik = cik,
            lei = lei,
            confidence = float(llm.get("confidence") or 0.0),
            notes = [n for n in [llm.get("disambiguation_note")] if n]
        )
        return bb
