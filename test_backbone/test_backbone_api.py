import os, csv, json, re
from typing import Any, Dict, Iterable, List, Tuple, Optional

import pytest
import requests

# --- Config -----------------------------------------------------------------

CSV_FILE = os.environ.get("TICKERS_CSV", "/var/www/site/current/test_backbone/tickers_100.csv")

# Point to port 8000 by default (your working curl target)
BASE_URL = os.environ.get(
    "COMPANY_BACKBONE_URL",
    "http://localhost:8000/api/backbone"
)
TIMEOUT_S = 15

# --- Utils ------------------------------------------------------------------

def normalize(s: Optional[str]) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()

def get_json(query: str) -> Dict[str, Any]:
    """Call your backbone: GET /api/backbone?company_name=<query> on :8000."""
    r = requests.get(BASE_URL, params={"company_name": query}, timeout=TIMEOUT_S)
    r.raise_for_status()  # will raise if not 2xx
    data = r.json()
    if not isinstance(data, dict):
        raise AssertionError(f"Expected dict JSON, got {type(data)} from {r.url}")
    return data

def load_cases() -> Iterable[Tuple[str, str]]:
    with open(CSV_FILE, newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            yield row["ticker"].strip(), row["expected_name_substring"].strip()

def extract_symbols(payload: Dict[str, Any]) -> List[str]:
    """
    Returns symbols from either schema:
      - flat: {"ticker":"AAPL"} or {"symbols":["AAPL", ...]}
      - nested: {"backbone":{"ticker":"AAPL","symbols":[...]}}
    """
    syms: List[str] = []
    if isinstance(payload.get("ticker"), str):
        syms.append(payload["ticker"])
    if isinstance(payload.get("symbols"), list):
        syms.extend([s for s in payload["symbols"] if isinstance(s, str)])

    bb = payload.get("backbone")
    if isinstance(bb, dict):
        if isinstance(bb.get("ticker"), str):
            syms.append(bb["ticker"])
        if isinstance(bb.get("symbols"), list):
            syms.extend([s for s in bb["symbols"] if isinstance(s, str)])

    # dedupe case-insensitively
    out, seen = [], set()
    for s in syms:
        k = normalize(s)
        if k and k not in seen:
            out.append(s)
            seen.add(k)
    return out

def extract_canonical_name(payload: Dict[str, Any]) -> str:
    """
    Returns name from either schema:
      - flat: {"name":"Apple Inc."}
      - nested: {"backbone":{"canonical_name":"Apple Inc."}} or {"backbone":{"name":"..."}}
    """
    bb = payload.get("backbone")
    if isinstance(bb, dict):
        if isinstance(bb.get("canonical_name"), str):
            return bb["canonical_name"]
        if isinstance(bb.get("name"), str):
            return bb["name"]
    if isinstance(payload.get("name"), str):
        return payload["name"]
    return ""

# --- Tests ------------------------------------------------------------------

def test_smoke_endpoint_reachable_for_aapl():
    data = get_json("AAPL")
    assert isinstance(data, dict)
    name = extract_canonical_name(data)
    syms = extract_symbols(data)
    assert name or syms, f"Missing both name and symbols in payload: {json.dumps(data)[:400]}"

@pytest.mark.parametrize("ticker, name_sub", list(load_cases()))
def test_lookup_by_ticker_returns_correct_company(ticker: str, name_sub: str):
    data = get_json(ticker)

    syms_norm = {normalize(s) for s in extract_symbols(data)}
    assert normalize(ticker) in syms_norm, (
        f"Ticker '{ticker}' not present in symbols {syms_norm} "
        f"(payload={json.dumps(data)[:400]})"
    )

    name = extract_canonical_name(data)
    assert normalize(name_sub) in normalize(name), (
        f"Expected name to contain '{name_sub}', got '{name}' "
        f"(payload={json.dumps(data)[:400]})"
    )

def test_accuracy_threshold():
    rows = list(load_cases())
    ok = 0
    for ticker, name_sub in rows:
        try:
            data = get_json(ticker)
        except Exception:
            continue
        syms_norm = {normalize(s) for s in extract_symbols(data)}
        name = extract_canonical_name(data)
        if normalize(ticker) in syms_norm and normalize(name_sub) in normalize(name):
            ok += 1
    assert ok >= int(0.95 * len(rows)), f"Accuracy {ok}/{len(rows)} below 95% threshold"
