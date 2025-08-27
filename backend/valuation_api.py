from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
import httpx, datetime as dt
import os

router = APIRouter()
SEC_CF_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
UA = os.environ.get("SEC_USER_AGENT", "CompanyEval/1.0 (b@fantasiagenesis.com)")

async def fetch_companyfacts(cik10: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as h:
        r = await h.get(SEC_CF_URL.format(cik=cik10), timeout=30)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="CIK not found at SEC")
        r.raise_for_status()
        return r.json()

def pad_cik(cik: str) -> str:
    return f"{int(''.join(ch for ch in cik if ch.isdigit())):010d}"

def _quarterly_points(fact: Dict[str, Any], unit_key: str) -> List[Dict[str, Any]]:
    """Return quarterly data points sorted by end date asc."""
    units = (fact.get("units") or {}).get(unit_key, []) if fact else []
    q = [x for x in units if (x.get("fp") or "").startswith("Q")]
    q.sort(key=lambda x: x.get("end"))
    return q

def ttm_eps_from_companyfacts(cf: Dict[str, Any]) -> Optional[float]:
    gaap = (cf.get("facts") or {}).get("us-gaap", {})
    # Prefer Diluted EPS, else Basic
    for key, unit in (("EarningsPerShareDiluted", "USD/shares"), ("EarningsPerShareBasic", "USD/shares")):
        if key in gaap:
            pts = _quarterly_points(gaap[key], unit)
            if len(pts) >= 4:
                last4 = pts[-4:]
                return float(sum(p.get("val", 0.0) for p in last4))
    # Fallback: NetIncomeLoss / WeightedAverageDilutedSharesOutstanding
    ni = gaap.get("NetIncomeLoss")
    sh = gaap.get("WeightedAverageNumberOfDilutedSharesOutstanding" ) or gaap.get("WeightedAverageDilutedSharesOutstanding")
    if ni and sh:
        ni_pts = _quarterly_points(ni, "USD")
        sh_pts = _quarterly_points(sh, "shares")
        if len(ni_pts) >= 4 and len(sh_pts) >= 4:
            ni_sum = float(sum(p.get("val", 0.0) for p in ni_pts[-4:]))
            sh_avg = float(sum(p.get("val", 0.0) for p in sh_pts[-4:]) / 4.0)
            if sh_avg > 0:
                return ni_sum / sh_avg
    return None

import yfinance as yf

def latest_price(symbol: str) -> float:
    t = yf.Ticker(symbol)
    info = t.fast_info
    if "last_price" in info:
        return float(info["last_price"])
    hist = t.history(period="5d")
    if hist.empty:
        raise HTTPException(status_code=404, detail="No price")
    return float(hist["Close"].dropna().iloc[-1])

@router.get("/valuation/pe_ttm_current")
async def pe_ttm_current(symbol: str = Query(...), cik: str = Query(...)):
    cik10 = pad_cik(cik)
    cf = await fetch_companyfacts(cik10)
    eps_ttm = ttm_eps_from_companyfacts(cf)
    if eps_ttm is None or abs(eps_ttm) < 1e-6:
        return {"symbol": symbol.upper(), "cik": cik10, "pe_ttm": None, "eps_ttm": eps_ttm, "notes": "TTM EPS unavailable or ~0"}
    price = latest_price(symbol)
    return {
        "symbol": symbol.upper(),
        "cik": cik10,
        "as_of": dt.datetime.utcnow().date().isoformat(),
        "price": price,
        "eps_ttm": eps_ttm,
        "pe_ttm": price / eps_ttm
    }
