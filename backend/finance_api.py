from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
import httpx, datetime as dt, os

router = APIRouter()
SEC_CF_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
UA = os.environ.get("SEC_USER_AGENT", "CompanyEval/1.0 (b@fantasiagenesis.com)")

def _pad_cik(cik: str) -> str:
    digits = "".join(ch for ch in cik if ch.isdigit())
    if not digits:
        raise HTTPException(status_code=400, detail="Invalid CIK")
    return f"{int(digits):010d}"

async def _fetch_companyfacts(cik10: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as h:
        r = await h.get(SEC_CF_URL.format(cik=cik10), timeout=30)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="CIK not found at SEC")
        r.raise_for_status()
        return r.json()

def _qpoints(fact: Dict[str, Any], unit: str) -> List[Dict[str, Any]]:
    units = (fact.get("units") or {}).get(unit, [])
    qs = [x for x in units if (x.get("fp") or "").startswith("Q")]
    qs.sort(key=lambda x: x.get("end"))
    return qs

def _rev_fact(gaap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Try common revenue concepts in order
    for key in [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "SalesRevenueServicesNet",
        "SalesRevenueGoodsNet",
        "Revenue",
    ]:
        if key in gaap:
            return gaap[key]
    return None

@router.get("/finance/revenue_ttm")
async def revenue_ttm(cik: str = Query(...)):
    cik10 = _pad_cik(cik)
    cf = await _fetch_companyfacts(cik10)
    gaap = (cf.get("facts") or {}).get("us-gaap", {})
    fact = _rev_fact(gaap)
    if not fact:
        return {"cik": cik10, "value_usd": None, "as_of": None, "currency": "USD",
                "notes": "No revenue line in companyfacts", "citations":[SEC_CF_URL.format(cik=cik10)]}
    qs = _qpoints(fact, "USD")
    if len(qs) < 4:
        return {"cik": cik10, "value_usd": None, "as_of": None, "currency": "USD",
                "notes": "Insufficient quarterly points (<4)", "citations":[SEC_CF_URL.format(cik=cik10)]}
    last4 = qs[-4:]
    val = float(sum(p.get("val", 0.0) for p in last4))
    as_of = last4[-1]["end"]
    return {"cik": cik10, "value_usd": val, "as_of": as_of, "currency": "USD",
            "citations":[SEC_CF_URL.format(cik=cik10)]}

@router.get("/finance/revenue_ttm_series_17q")
async def revenue_ttm_series_17q(cik: str = Query(...)):
    cik10 = _pad_cik(cik)
    cf = await _fetch_companyfacts(cik10)
    gaap = (cf.get("facts") or {}).get("us-gaap", {})
    fact = _rev_fact(gaap)
    if not fact:
        return {"cik": cik10, "series": [], "cagr_4y": None, "currency":"USD",
                "notes":"No revenue line", "citations":[SEC_CF_URL.format(cik=cik10)]}
    qs = _qpoints(fact, "USD")
    if len(qs) < 20:  # need ~20 quarters to build 17 TTM points
        return {"cik": cik10, "series": [], "cagr_4y": None, "currency":"USD",
                "notes":"Need >=20 quarters for 17Q TTM", "citations":[SEC_CF_URL.format(cik=cik10)]}

    def qlabel(iso_end: str) -> str:
        y, m = int(iso_end[:4]), int(iso_end[5:7])
        q = (m-1)//3 + 1
        return f"{y}-Q{q}"

    series = []
    for i in range(3, len(qs)):
        win = qs[i-3:i+1]
        ttm = float(sum(p.get("val", 0.0) for p in win))
        series.append({"quarter": qlabel(qs[i]["end"]), "ttm_rev": ttm, "currency":"USD"})

    cagr_4y = None
    if len(series) >= 17:
        a = series[-17]["ttm_rev"]
        b = series[-1]["ttm_rev"]
        if a > 0 and b > 0:
            cagr_4y = (b/a)**(1/4) - 1

    return {"cik": cik10, "series": series[-17:], "cagr_4y": cagr_4y, "currency":"USD",
            "citations":[SEC_CF_URL.format(cik=cik10)]}
