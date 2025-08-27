from __future__ import annotations
import re, time
from typing import Optional, Dict, Any, List
import httpx
from fastapi import APIRouter, HTTPException, Query
import os

router = APIRouter()
SEC_CF_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
UA = os.environ.get("SEC_USER_AGENT", "CompanyEval/1.0 (b@fantasiagenesis.com)")

# tiny in-memory cache (avoid SEC rate limits)
_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}
_TTL = 300

def _pad_cik(cik: str) -> str:
    digits = re.sub(r"\D", "", cik)
    if not digits:
        raise HTTPException(status_code=400, detail="Invalid CIK")
    return f"{int(digits):010d}"

async def _fetch_companyfacts(cik10: str) -> Dict[str, Any]:
    now = time.time()
    if (c := _CACHE.get(cik10)) and now - c[0] < _TTL:
        return c[1]
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as h:
        r = await h.get(SEC_CF_URL.format(cik=cik10), timeout=30)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="CIK not found at SEC")
        r.raise_for_status()
        data = r.json()
    _CACHE[cik10] = (now, data)
    return data

@router.get("/edgar/companyfacts")
async def edgar_companyfacts(cik: str = Query(..., description="CIK (any format)")):
    cik10 = _pad_cik(cik)
    return await _fetch_companyfacts(cik10)
