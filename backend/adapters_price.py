from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import yfinance as yf
import pandas as pd

router = APIRouter()

@router.get("/prices/latest")
def latest_price(symbol: str = Query(..., min_length=1)):
    try:
        t = yf.Ticker(symbol)
        # try fast path first
        info = t.fast_info  # has last_price when available
        price = float(info["last_price"]) if "last_price" in info else None
        if price is None:
            hist = t.history(period="5d")
            if hist.empty:
                raise HTTPException(status_code=404, detail="No price data")
            price = float(hist["Close"].dropna().iloc[-1])
        ccy = getattr(t, "fast_info", {}).get("currency", None)
        return {"symbol": symbol.upper(), "price": price, "currency": ccy}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Price fetch failed: {e}")

@router.get("/prices/at")
def price_at(
    symbol: str = Query(..., min_length=1),
    date: str = Query(..., description="YYYY-MM-DD (trading day; will use last available before date if not)")
):
    try:
        t = yf.Ticker(symbol)
        hist = t.history(start=date, end=date, auto_adjust=False)
        if hist.empty:
            # get a window and backfill
            hist = t.history(end=date, period="10d", auto_adjust=False)
        if hist.empty:
            raise HTTPException(status_code=404, detail="No historical price data")
        close = float(hist["Close"].dropna().iloc[-1])
        return {"symbol": symbol.upper(), "as_of": str(hist.index[-1].date()), "price": close}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Price fetch failed: {e}")
