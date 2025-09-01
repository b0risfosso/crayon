#!/usr/bin/env python3
# parse_company_universe.py
import json, sys, argparse, csv
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------- Core loaders ---------
def load_json(path: str) -> Dict[str, Any]:
    if path == "-" or path == "":
        data = sys.stdin.read()
        return json.loads(data)
    return json.loads(Path(path).read_text(encoding="utf-8"))

# --------- Validation (lightweight) ---------
def expect_keys(d: dict, keys: List[str], where: str):
    missing = [k for k in keys if k not in d]
    if missing:
        raise ValueError(f"Missing keys {missing} in {where}")

def validate_universe(doc: Dict[str, Any]) -> None:
    expect_keys(doc, ["as_of", "universe"], "root")
    if not isinstance(doc["universe"], list):
        raise ValueError("`universe` must be a list")
    for i, tier in enumerate(doc["universe"], 1):
        expect_keys(tier, ["tier", "label", "companies"], f"tier[{i}]")
        if not isinstance(tier["companies"], list):
            raise ValueError(f"tier[{i}].companies must be a list")
        for j, co in enumerate(tier["companies"], 1):
            expect_keys(co, ["name", "status", "sector"], f"tier[{i}].companies[{j}]")

# --------- Normalizers ---------
def join_list(v: Optional[List[Any]]) -> str:
    if not v:
        return ""
    return "; ".join(map(str, v))

def get(d: Dict[str, Any], *keys, default="") -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

# --------- Extraction ---------
def extract_tiers(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for t in doc["universe"]:
        out.append({
            "as_of": doc.get("as_of", ""),
            "tier": t.get("tier", ""),
            "label": t.get("label", ""),
            "criteria": t.get("criteria", ""),
            "why": t.get("why", ""),
            "num_companies": len(t.get("companies", [])),
        })
    return out

def extract_companies(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for t in doc["universe"]:
        for co in t.get("companies", []):
            rows.append({
                "as_of": doc.get("as_of", ""),
                "tier": t.get("tier", ""),
                "tier_label": t.get("label", ""),
                "name": co.get("name", ""),
                "status": co.get("status", ""),
                "sector": co.get("sector", ""),
                # Public-specific fields
                "ticker": get(co, "ticker"),
                "market_cap_range": get(co, "market_cap_range"),
                # Pre-IPO fields
                "ipo_signal": get(co, "ipo_signal"),
                # Signal lists (flatten to '; ' joined)
                "fragility_signals": join_list(get(co, "fragility_signals", default=[])),
                "fragility_watchpoints": join_list(get(co, "fragility_watchpoints", default=[])),
                "moat_signals": join_list(get(co, "moat_signals", default=[])),
                "highlights": join_list(get(co, "highlights", default=[])),
            })
    return rows

# --------- Filtering ---------
def filter_rows(rows: List[Dict[str, Any]],
                tier: Optional[int],
                status: Optional[str],
                name_contains: Optional[str]) -> List[Dict[str, Any]]:
    def ok(r):
        if tier is not None and int(r.get("tier") or -1) != tier:
            return False
        if status and r.get("status", "").lower() != status.lower():
            return False
        if name_contains and name_contains.lower() not in r.get("name", "").lower():
            return False
        return True
    return [r for r in rows if ok(r)]

# --------- CSV helpers ---------
def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

# --------- Pretty summary ---------
def print_summary(doc: Dict[str, Any], filtered_companies: List[Dict[str, Any]]) -> None:
    print(f"As of: {doc.get('as_of', '')}")
    tiers = extract_tiers(doc)
    print("\nTiers:")
    for t in tiers:
        print(f"  - Tier {t['tier']}: {t['label']}  (companies: {t['num_companies']})")
    print("\nCompanies (filtered view):")
    for r in filtered_companies:
        line = f"  • [{r['tier']}] {r['name']} — {r['sector']} — {r['status']}"
        extras = []
        for k in ("ticker","market_cap_range","ipo_signal"):
            if r.get(k):
                extras.append(f"{k}={r[k]}")
        if extras:
            line += "  (" + ", ".join(extras) + ")"
        print(line)

# --------- CLI ---------
def main():
    ap = argparse.ArgumentParser(
        description="Parse the Company Universe JSON, validate it, pretty-print, and export CSVs."
    )
    ap.add_argument("input", help="Path to JSON file (or '-' for stdin)")
    ap.add_argument("--tiers-csv", metavar="PATH", help="Write tiers table to CSV")
    ap.add_argument("--companies-csv", metavar="PATH", help="Write companies table to CSV")
    ap.add_argument("--filter-tier", type=int, help="Only include companies from this tier number")
    ap.add_argument("--filter-status", choices=["private","pre-IPO","public"],
                    help="Only include companies with this status")
    ap.add_argument("--filter-name-contains", help="Substring filter on company name")
    ap.add_argument("--json-out", metavar="PATH", help="Write filtered companies as JSON")
    ap.add_argument("--no-summary", action="store_true", help="Skip summary printing")
    args = ap.parse_args()

    doc = load_json(args.input)
    validate_universe(doc)

    tiers = extract_tiers(doc)
    companies = extract_companies(doc)
    companies_f = filter_rows(companies, args.filter_tier, args.filter_status, args.filter_name_contains)

    # Outputs
    if not args.no_summary:
        print_summary(doc, companies_f)

    if args.tiers_csv:
        write_csv(args.tiers_csv, tiers)
        print(f"\nWrote tiers CSV → {args.tiers_csv}")

    if args.companies_csb := args.companies_csv:
        write_csv(args.companies_csb, companies_f)
        print(f"Wrote companies CSV → {args.companies_csb}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(companies_f, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote filtered companies JSON → {args.json_out}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
