# backend/moat_market_leadership.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
import os, re, httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from openai import OpenAI
from datetime import datetime

router = APIRouter()
SEC_UA = os.environ.get("SEC_USER_AGENT", "CompanyEval/1.0 (b@fantasiagenesis.com)")
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"

def pad_cik(cik: str) -> str:
    d = "".join(ch for ch in cik if ch.isdigit())
    if not d: raise HTTPException(status_code=400, detail="Invalid CIK")
    return f"{int(d):010d}"

async def latest_annual_filing_url(cik10: str) -> Optional[str]:
    async with httpx.AsyncClient(headers={"User-Agent": SEC_UA}) as h:
        r = await h.get(SUBMISSIONS.format(cik=cik10), timeout=30)
        if r.status_code == 404: return None
        r.raise_for_status()
        data = r.json()
    # Prefer 10-K, fall back to 20-F
    for form in ("10-K","20-F"):
        try:
            filings = data["filings"]["recent"]
            idxs = [i for i,f in enumerate(filings["form"]) if f == form]
            if not idxs: continue
            i = idxs[0]  # most recent appears first
            acc = filings["accessionNumber"][i].replace("-","")
            prim = filings["primaryDocument"][i]
            # EDGAR URL shape
            cik_no_zeros = str(int(cik10))
            return f"https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{acc}/{prim}"
        except Exception:
            continue
    return None

def extract_competition_section(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    # crude section split around common headers
    patterns = [r"\bCOMPETITION\b", r"\bMARKET\b", r"\bINDUSTR(Y|IES)\b"]
    start = None
    for p in patterns:
        m = re.search(p, text, re.I)
        if m: start = m.start(); break
    if start is None:
        return text[:4000]  # fallback small chunk
    chunk = text[start:start+18000]  # take ~18k chars after header
    # stop at next all-caps header-ish line
    m2 = re.search(r"\n[A-Z ]{6,}\n", chunk[2000:], re.M)
    if m2: chunk = chunk[:2000+m2.start()]
    return chunk

# ---------- LLM struct parse ----------
client = OpenAI()

class SharePoint(BaseModel):
    category: str
    region: Optional[str] = None
    share_pct: Optional[float] = Field(None, ge=0, le=100)
    rank: Optional[int] = Field(None, ge=1)
    date: Optional[str] = None
    text_span: Optional[str] = None
    source_hint: Optional[str] = None  # "10-K", "press", etc.

class MarketLeadershipExtraction(BaseModel):
    items: List[SharePoint] = []
    phrases: List[str] = []  # e.g., "market leader", "largest", "top"
    notes: Optional[str] = None

SYSTEM = (
    "Extract structured market share/rank evidence for the issuer from the provided filing text. "
    "Capture the category (product-market), region if present, numerical share %, and rank if stated. "
    "Keep text_span to a short quote (<200 chars) supporting the claim. If multiple categories appear, include multiple items."
)

def hhi_from_shares(shares: List[float]) -> Optional[float]:
    if not shares: return None
    return sum((s)**2 for s in shares)

def score_market_leadership(items: List[SharePoint]) -> Dict[str, Any]:
    # Simplified scoring: average of normalized features, penalize decline if detectable (not available here yet)
    shares = [it.share_pct for it in items if it.share_pct is not None]
    ranks  = [it.rank for it in items if it.rank is not None]
    hhi = hhi_from_shares(shares) if shares else None

    def norm_share(s): return min(1.0, (s or 0)/60.0)  # 60%+ saturates
    def norm_rank(r): return 1.0 if r == 1 else (0.7 if r == 2 else 0.4)
    def norm_hhi(h): 
        if h is None: return 0.5
        # HHI in percentage-squared terms; 2500 ~ moderately concentrated
        return max(0.0, min(1.0, (h - 1500) / 3500))

    if not items:
        return {"score": 0.0, "HHI": None}

    s_part = max((norm_share(max(shares)) if shares else 0), 0)
    r_part = max((norm_rank(min(ranks)) if ranks else 0), 0)
    h_part = norm_hhi(hhi)

    score_0_5 = 5.0 * (0.5*s_part + 0.3*r_part + 0.2*h_part)
    return {"score": round(score_0_5, 2), "HHI": hhi}

@router.get("/moat/market_leadership")
async def moat_market_leadership(cik: str = Query(...), company_name: str = Query(...)):
    cik10 = pad_cik(cik)
    url = await latest_annual_filing_url(cik10)
    if not url:
        raise HTTPException(status_code=404, detail="No annual filing found")

    async with httpx.AsyncClient(headers={"User-Agent": SEC_UA}) as h:
        r = await h.get(url, timeout=60)
        r.raise_for_status()
        html = r.text

    section = extract_competition_section(html)

    resp = client.responses.parse(
        model=os.environ.get("OPENAI_MODEL","gpt-4o-2024-08-06"),
        input=[{"role":"system","content":SYSTEM},{"role":"user","content":section}],
        text_format=MarketLeadershipExtraction,
        temperature=0.1,
    )
    parsed: MarketLeadershipExtraction = resp.output_parsed

    scored = score_market_leadership(parsed.items)
    rationale_bits = []
    for it in parsed.items[:3]:
        bits = []
        if it.share_pct is not None: bits.append(f"{it.share_pct:.1f}%")
        if it.rank is not None: bits.append(f"rank {it.rank}")
        if it.region: bits.append(it.region)
        rationale_bits.append(f"{it.category}: " + ", ".join(bits))

    return {
        "key": "moat.market_leadership",
        "score": scored["score"],
        "signals": {
            "market_share_pct": (max([it.share_pct for it in parsed.items if it.share_pct is not None], default=None)),
            "rank": (min([it.rank for it in parsed.items if it.rank is not None], default=None)),
            "HHI": scored["HHI"],
            "phrases": parsed.phrases,
        },
        "rationale": "; ".join(rationale_bits)[:320],
        "citations": [url],
        "as_of": datetime.utcnow().date().isoformat()
    }
