
import os, csv, re, json
from typing import Dict, Any, Iterable

import pytest

try:
    import requests
except Exception as e:
    requests = None

CSV_FILE = os.environ.get("TICKERS_CSV", "/var/www/site/current/test_backbone/tickers_100.csv")
BACKBONE_ENDPOINT = os.environ.get("BACKBONE_ENDPOINT", "http://localhost:5000/api/backbone")

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def get_json(query: str) -> Dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests not installed; pip install requests")
    r = requests.get(BACKBONE_ENDPOINT, params={"company_name": query}, timeout=12)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise AssertionError(f"Expected dict JSON, got: {type(data)}")
    return data

def load_cases() -> Iterable[tuple[str, str]]:
    with open(CSV_FILE, newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            yield row["ticker"].strip(), row["expected_name_substring"].strip()

def extract_symbols(backbone: Dict[str, Any]) -> set[str]:
    out = set()
    for tk in (backbone or {}).get("tickers", []) or []:
        sym = tk.get("symbol")
        if sym:
            out.add(normalize(sym))
    return out

def test_smoke_schema_and_csv():
    # Quick schema check on one well-known query
    data = get_json("AAPL")
    assert "query" in data, "missing top-level 'query'"
    assert "backbone" in data and isinstance(data["backbone"], dict), "missing 'backbone' object"
    bb = data["backbone"]
    for key in ["canonical_name", "aliases", "tickers"]:
        assert key in bb, f"missing backbone['{key}']"
    # CSV exists and has at least 50 cases
    rows = list(load_cases())
    assert len(rows) >= 50, f"expected >=50 rows, got {len(rows)}"

@pytest.mark.parametrize("ticker, name_sub", list(load_cases()))
def test_lookup_by_ticker_returns_correct_company(ticker: str, name_sub: str):
    # Query the API using the ticker as company_name; backbone should resolve to the correct entity.
    data = get_json(ticker)
    bb = data["backbone"]
    canon = normalize(bb.get("canonical_name"))
    aliases = [normalize(a) for a in (bb.get("aliases") or [])]
    syms = extract_symbols(bb)

    # 1) canonical_name contains expected substring
    assert normalize(name_sub) in canon, f"canonical_name '{bb.get('canonical_name')}' missing substring '{name_sub}'"

    # 2) tickers contains the queried ticker (case-insensitive)
    assert normalize(ticker) in syms, f"ticker '{ticker}' not found in backbone.tickers {sorted(syms)}"

def test_accuracy_threshold():
    rows = list(load_cases())
    ok = 0
    for ticker, name_sub in rows:
        try:
            data = get_json(ticker)
        except Exception:
            continue
        bb = data.get("backbone", {})
        canon = normalize(bb.get("canonical_name"))
        syms = extract_symbols(bb)
        if normalize(name_sub) in canon and normalize(ticker) in syms:
            ok += 1
    assert ok >= int(0.95 * len(rows)), f"Accuracy {ok}/{len(rows)} below 95% threshold"
