# backend/moat_market_leadership.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
import os, re, httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from openai import OpenAI
from datetime import datetime
from backend.wiki_market_generalized import wiki_market_share_generalized
from backend.backbone_resolver import build_backbone

router = APIRouter()
SEC_UA = os.environ.get("SEC_USER_AGENT", "CompanyEval/1.0 (b@fantasiagenesis.com)")
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"

# TOP: extend phrase detection targets
PHRASE_PATTERNS = [
    r"\bmarket leader\b",
    r"\blargest\b",
    r"\btop (?:player|provider|vendor)\b",
    r"\bleading\b",
    r"\bdominant\b",
]


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
    # Keep a text with newlines for easier heading heuristics
    text = soup.get_text("\n", strip=True)

    # Try to find “Item 1. Business” or “Competition” headings and grab a bigger window
    candidates = [
        r"ITEM\s+1\.\s+BUSINESS",
        r"\bCOMPETITION\b",
        r"\bMARKET\b",
        r"\bINDUSTR(?:Y|IES)\b",
    ]
    starts = [m.start() for pat in candidates for m in [re.search(pat, text, re.I)] if m]
    if not starts:
        return text[:20000]  # last resort: big slice

    start = min(starts)
    chunk = text[start:start+40000]  # take a generous window

    # Stop at next all-caps header-ish line after a minimum length
    m2 = re.search(r"\n[A-Z0-9 .,&/-]{8,}\n", chunk[3000:], re.M)
    if m2:
        chunk = chunk[:3000+m2.start()]

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
    "If numeric shares or ranks are present, capture them. "
    "Additionally, always capture any leadership phrases (e.g., 'market leader', 'largest', 'top', 'leading', 'dominant') "
    "with a short text_span quote even if no numbers are present."
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
async def moat_market_leadership(
    cik: str = Query(...),
    company_name: str = Query(...),
    use_wiki: bool = Query(False, description="Enable Wikipedia fallback if filings lack numeric share"),
    region_hint: str | None = Query(None, description="Optional region focus, e.g., 'United States'")
):
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

    # After parsed = resp.output_parsed
    if not parsed.phrases:
        found = []
        for pat in PHRASE_PATTERNS:
            if re.search(pat, section, re.I):
                found.append(pat.strip(r"\b").replace(r"(?:player|provider|vendor)","player/provider/vendor"))
        parsed.phrases = found[:5]

    # Make sure we have a citations list started with the filing URL
    citations = [url]

    # If the filing didn’t yield any numeric share/rank and user allows wiki, try generalized wiki fallback
    computed_hhi = None

    if use_wiki and not any(it.share_pct is not None for it in parsed.items):
        # get a few aliases to improve vendor matching (best-effort; ignore errors)
        aliases: list[str] = []
        try:
            bb = await build_backbone(company_name)
            if bb and bb.aliases:
                aliases = [a for a in bb.aliases if isinstance(a, str)]
            # also toss in the canonical name variant
            if bb and bb.canonical_name:
                aliases.append(bb.canonical_name)
        except Exception:
            pass

        wiki = wiki_market_share_generalized(
            company_name=company_name,
            aliases=aliases,
            industry_hints=[],        # you can feed Wikidata industries later; empty is fine
            region_hint=region_hint,  # e.g., "United States" or None for global/unspecified
        )

        if wiki and (wiki.get("company_share") is not None or wiki.get("company_rank") is not None):
            parsed.items.append(SharePoint(
                category=f"market share ({wiki.get('period_hint') or 'recent'})",
                region=wiki.get("region") or (region_hint or "unspecified"),
                share_pct=wiki.get("company_share"),
                rank=wiki.get("company_rank"),
                text_span=None,
                source_hint="Wikipedia"
            ))
            computed_hhi = wiki.get("hhi")
            if wiki.get("url"):
                citations.append(wiki["url"])

    scored = score_market_leadership(parsed.items)

    # If wiki computed an HHI, prefer that
    if computed_hhi is not None:
        scored["HHI"] = computed_hhi

    rationale_bits = []
    for it in parsed.items[:3]:
        bits = []
        if it.share_pct is not None: bits.append(f"{it.share_pct:.1f}%")
        if it.rank is not None: bits.append(f"rank {it.rank}")
        if it.region: bits.append(it.region)
        rationale_bits.append(f"{it.category}: " + ", ".join(bits))

    if (all(it.share_pct is None for it in parsed.items)) and parsed.phrases:
        base = min(2.0, 0.8 + 0.4*len(parsed.phrases))  # cap at 2/5
        scored["score"] = round(base, 2)


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
        "citations": citations,  # <— use this
        "as_of": datetime.utcnow().date().isoformat()
    }
