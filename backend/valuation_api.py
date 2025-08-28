# backend/valuation_api.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
import httpx, datetime as dt, os

import yfinance as yf

router = APIRouter()
SEC_CF_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
UA = os.environ.get("SEC_USER_AGENT", "CompanyEval/1.0 (b@fantasiagenesis.com)")

# ---------- Helpers ----------
def pad_cik(cik: str) -> str:
    digits = "".join(ch for ch in cik if ch.isdigit())
    if not digits:
        raise HTTPException(status_code=400, detail="Invalid CIK")
    return f"{int(digits):010d}"

async def fetch_companyfacts(cik10: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as h:
        r = await h.get(SEC_CF_URL.format(cik=cik10), timeout=30)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="CIK not found at SEC")
        r.raise_for_status()
        return r.json()

def qpoints(fact: Dict[str, Any], unit: str) -> List[Dict[str, Any]]:
    units = (fact.get("units") or {}).get(unit, [])
    qs = [x for x in units if (x.get("fp") or "").startswith("Q")]
    qs.sort(key=lambda x: x.get("end"))
    return qs

def gaap_block(cf: Dict[str, Any]) -> Dict[str, Any]:
    return (cf.get("facts") or {}).get("us-gaap", {})

def eps_quarterly_points(cf: Dict[str, Any]) -> List[Dict[str, Any]]:
    gaap = gaap_block(cf)
    # Prefer Diluted EPS; unit in companyfacts is "USD/shares"
    for key in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
        if key in gaap:
            pts = qpoints(gaap[key], "USD/shares")
            if pts:
                return pts
    # Fallback: compute EPS = NetIncome / DilutedShares (quarterly)
    ni = gaap.get("NetIncomeLoss")
    sh = gaap.get("WeightedAverageNumberOfDilutedSharesOutstanding") or gaap.get("WeightedAverageDilutedSharesOutstanding")
    if not ni or not sh:
        return []
    ni_pts = qpoints(ni, "USD")
    sh_pts = qpoints(sh, "shares")
    if len(ni_pts) != len(sh_pts) or len(ni_pts) == 0:
        # try align by end date
        by_end = {p["end"]: p for p in sh_pts}
        out = []
        for p in ni_pts:
            q = by_end.get(p["end"])
            if q and q.get("val"):
                out.append({"end": p["end"], "val": float(p.get("val", 0.0)) / float(q["val"])})
        return out
    return [{"end": ni_pts[i]["end"], "val": float(ni_pts[i]["val"]) / float(sh_pts[i]["val"])} for i in range(len(ni_pts))]

def ttm_eps_now(cf: Dict[str, Any]) -> Optional[float]:
    pts = eps_quarterly_points(cf)
    if len(pts) < 4: return None
    return float(sum(p.get("val", 0.0) for p in pts[-4:]))

def ttm_eps_at_or_before(cf: Dict[str, Any], date_iso: str) -> Optional[float]:
    pts = eps_quarterly_points(cf)
    if len(pts) < 4: return None
    # keep quarters whose end <= date
    xs = [p for p in pts if p.get("end") and p["end"] <= date_iso]
    if len(xs) < 4: return None
    return float(sum(p.get("val", 0.0) for p in xs[-4:]))

def latest_price(symbol: str) -> Dict[str, Any]:
    t = yf.Ticker(symbol)
    info = t.fast_info
    price = float(info["last_price"]) if "last_price" in info else None
    if price is None:
        hist = t.history(period="5d")
        if hist.empty: raise HTTPException(status_code=404, detail="No price data")
        price = float(hist["Close"].dropna().iloc[-1])
    return {"price": price, "currency": getattr(t, "fast_info", {}).get("currency")}

def price_on_or_before(symbol: str, date_iso: str) -> Dict[str, Any]:
    # Pull a window before the date and pick last available close <= date
    t = yf.Ticker(symbol)
    # 370d window to be safe around holidays
    hist = t.history(end=date_iso, period="370d", auto_adjust=False)
    if hist.empty:
        raise HTTPException(status_code=404, detail="No historical price data")
    # All rows are <= date_iso by construction; take last
    close = float(hist["Close"].dropna().iloc[-1])
    as_of = str(hist.index[-1].date())
    return {"price": close, "as_of": as_of, "currency": getattr(t, "fast_info", {}).get("currency")}

def today_iso() -> str:
    return dt.datetime.utcnow().date().isoformat()

# ---------- Endpoints ----------
@router.get("/valuation/pe_ttm_current")
async def pe_ttm_current(symbol: str = Query(...), cik: str = Query(...)):
    cik10 = pad_cik(cik)
    cf = await fetch_companyfacts(cik10)
    eps = ttm_eps_now(cf)
    if eps is None or abs(eps) < 1e-9:
        return {"symbol": symbol.upper(), "cik": cik10, "as_of": today_iso(),
                "price": None, "eps_ttm": eps, "pe_ttm": None,
                "notes": "TTM EPS unavailable or ~0", "citations":[SEC_CF_URL.format(cik=cik10)]}
    p = latest_price(symbol)
    return {"symbol": symbol.upper(), "cik": cik10, "as_of": today_iso(),
            "price": p["price"], "currency": p["currency"],
            "eps_ttm": eps, "pe_ttm": p["price"]/eps,
            "citations":[SEC_CF_URL.format(cik=cik10)]}

@router.get("/valuation/pe_ttm_at_date")
async def pe_ttm_at_date(symbol: str = Query(...), cik: str = Query(...), date: str = Query(..., description="YYYY-MM-DD")):
    cik10 = pad_cik(cik)
    cf = await fetch_companyfacts(cik10)
    eps = ttm_eps_at_or_before(cf, date)
    if eps is None or abs(eps) < 1e-9:
        return {"symbol": symbol.upper(), "cik": cik10, "as_of": date,
                "price": None, "eps_ttm": eps, "pe_ttm": None,
                "notes": "Insufficient EPS data for TTM at/before date",
                "citations":[SEC_CF_URL.format(cik=cik10)]}
    p = price_on_or_before(symbol, date)
    return {"symbol": symbol.upper(), "cik": cik10, "as_of": p["as_of"],
            "price": p["price"], "currency": p["currency"],
            "eps_ttm": eps, "pe_ttm": p["price"]/eps,
            "citations":[SEC_CF_URL.format(cik=cik10)]}

@router.get("/valuation/pe_ttm_minus_1y")
async def pe_ttm_minus_1y(symbol: str = Query(...), cik: str = Query(...)):
    target = (dt.datetime.utcnow().date() - dt.timedelta(days=365)).isoformat()
    return await pe_ttm_at_date(symbol=symbol, cik=cik, date=target)

@router.get("/valuation/pe_ttm_minus_2y")
async def pe_ttm_minus_2y(symbol: str = Query(...), cik: str = Query(...)):
    target = (dt.datetime.utcnow().date() - dt.timedelta(days=730)).isoformat()
    return await pe_ttm_at_date(symbol=symbol, cik=cik, date=target)

# --------- Forward P/E (model fallback: roll forward TTM EPS using YoY growth) ---------
def yoy_ttm_eps_growth(cf: Dict[str, Any]) -> Optional[float]:
    # TTM now vs TTM one year ago
    today = dt.datetime.utcnow().date().isoformat()
    eps_now = ttm_eps_now(cf)
    eps_1y = ttm_eps_at_or_before(cf, (dt.datetime.utcnow().date() - dt.timedelta(days=365)).isoformat())
    if eps_now is None or eps_1y is None or eps_1y == 0:
        return None
    return (eps_now / eps_1y) - 1.0

def forward_eps_from_yoy(cf: Dict[str, Any], years: int) -> Optional[float]:
    eps_now = ttm_eps_now(cf)
    g = yoy_ttm_eps_growth(cf)
    if eps_now is None or g is None:
        return None
    # clamp extreme growth to +/-100% to reduce blowups
    g = max(min(g, 1.0), -1.0)
    return eps_now * ((1.0 + g) ** years)

@router.get("/valuation/pe_forward_1y")
async def pe_forward_1y(symbol: str = Query(...), cik: str = Query(...)):
    """
    Forward-1Y P/E using model fallback:
    EPS_FY+1 ≈ TTM_EPS_now * (1 + YoY_TTM_EPS_growth).
    """
    cik10 = pad_cik(cik)
    cf = await fetch_companyfacts(cik10)
    eps_fwd = forward_eps_from_yoy(cf, 1)
    p = latest_price(symbol)
    return {
        "symbol": symbol.upper(), "cik": cik10, "as_of": today_iso(),
        "price": p["price"], "currency": p["currency"],
        "eps_forward_1y": eps_fwd, "pe_forward_1y": (p["price"]/eps_fwd) if (eps_fwd and abs(eps_fwd) > 1e-9) else None,
        "method": "model_yoy_roll_forward", "confidence": 0.35,
        "notes": "Consensus not integrated; using YoY TTM EPS growth as proxy.",
        "citations":[SEC_CF_URL.format(cik=cik10)]
    }

@router.get("/valuation/pe_forward_2y")
async def pe_forward_2y(symbol: str = Query(...), cik: str = Query(...)):
    cik10 = pad_cik(cik)
    cf = await fetch_companyfacts(cik10)
    eps_fwd = forward_eps_from_yoy(cf, 2)
    p = latest_price(symbol)
    return {
        "symbol": symbol.upper(), "cik": cik10, "as_of": today_iso(),
        "price": p["price"], "currency": p["currency"],
        "eps_forward_2y": eps_fwd, "pe_forward_2y": (p["price"]/eps_fwd) if (eps_fwd and abs(eps_fwd) > 1e-9) else None,
        "method": "model_yoy_roll_forward", "confidence": 0.3,
        "notes": "Consensus not integrated; using YoY TTM EPS growth as proxy.",
        "citations":[SEC_CF_URL.format(cik=cik10)]
    }
