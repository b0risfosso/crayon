# backend/evaluate_api.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import Any, Dict, List, Optional
import asyncio

# Backbone + metrics you already built
from backend.backbone_resolver import build_backbone
from backend.valuation_api import (
    pe_ttm_current, pe_ttm_minus_1y, pe_ttm_minus_2y,
    pe_forward_1y, pe_forward_2y,
)
from backend.finance_api import revenue_ttm, revenue_ttm_series_17q
from backend.moat_market_leadership import moat_market_leadership

router = APIRouter()

def _ok(d: Any) -> bool:
    return d is not None and isinstance(d, dict)

@router.get("/evaluate")
async def evaluate_company(
    company_name: str = Query(...),
    region_hint: Optional[str] = Query(None, description="e.g., 'United States'"),
    use_wiki: bool = Query(True, description="Allow Wikipedia fallback for market share")
):
    errors: List[str] = []

    # 1) Resolve backbone (IDs, aliases, tickers, etc.)
    try:
        bb = await build_backbone(company_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"backbone failed: {e}")

    company = {
        "canonical_name": getattr(bb, "canonical_name", company_name),
        "aliases": getattr(bb, "aliases", []) or [],
        "country": getattr(bb, "country", None),
        "headquarters_city": getattr(bb, "headquarters_city", None),
        "official_website": getattr(bb, "official_website", None),
        "domain": getattr(bb, "domain", None),
        "wikidata_id": getattr(bb, "wikidata_id", None),
        "wikipedia_url": getattr(bb, "wikipedia_url", None),
        "isin": getattr(bb, "isin", None),
        "lei": getattr(bb, "lei", None),
        "cik": getattr(bb, "cik", None),
        "is_public": getattr(bb, "is_public", False),
        "tickers": [t.dict() if hasattr(t, "dict") else t for t in (getattr(bb, "tickers", []) or [])],
        "confidence": getattr(bb, "confidence", None),
        "notes": getattr(bb, "notes", []) or [],
    }

    # Convenience
    cik = company["cik"]
    symbol = company["tickers"][0]["symbol"] if (company["tickers"] and "symbol" in company["tickers"][0]) else None

    # 2) Fire tasks in parallel (skip ones we can’t compute)
    tasks = []

    if symbol and cik:
        tasks += [
            pe_ttm_current(symbol=symbol, cik=cik),
            pe_ttm_minus_1y(symbol=symbol, cik=cik),
            pe_ttm_minus_2y(symbol=symbol, cik=cik),
            pe_forward_1y(symbol=symbol, cik=cik),
            pe_forward_2y(symbol=symbol, cik=cik),
            revenue_ttm(cik=cik),
            revenue_ttm_series_17q(cik=cik),
        ]
    elif cik:
        tasks += [
            revenue_ttm(cik=cik),
            revenue_ttm_series_17q(cik=cik),
        ]

    # Always try moat.market_leadership (it has its own fallbacks)
    tasks.append(
        moat_market_leadership(
            cik=cik or "",
            company_name=company["canonical_name"] or company_name,
            use_wiki=use_wiki,
            region_hint=region_hint
        )
    )

    # Run them concurrently (they're async callables)
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 3) Pack into “version: 1 / metrics”
    metrics: List[Dict[str, Any]] = []

    def add_metric(obj: Any, expect_key: Optional[str] = None):
        if isinstance(obj, Exception):
            errors.append(str(obj))
            return
        if not _ok(obj):  # skip Nones etc.
            return
        k = obj.get("key") or expect_key
        if k:
            metrics.append({"key": k, **obj})
        else:
            # Normalize valuation/finance to your schema keys
            if "pe_ttm" in obj:
                metrics.append({"key": "valuation.pe_ttm_current", **obj})
            elif obj.get("as_of") and ("eps_ttm" in obj) and obj.get("price") and obj.get("pe_ttm") is None:
                # still add, even if EPS missing
                metrics.append({"key": "valuation.pe_ttm_current", **obj})
            elif "eps_forward_1y" in obj:
                metrics.append({"key": "valuation.pe_forward_1y", **obj})
            elif "eps_forward_2y" in obj:
                metrics.append({"key": "valuation.pe_forward_2y", **obj})
            elif "series" in obj and "cagr_4y" in obj:
                metrics.append({"key": "finance.revenue_ttm_series_17q", **obj})
            elif "value_usd" in obj:
                metrics.append({"key": "finance.revenue_ttm", **obj})
            else:
                metrics.append(obj)

    for r in results:
        add_metric(r)

    return {
        "version": 1,
        "company": company,
        "metrics": metrics,
        "errors": errors or None
    }
