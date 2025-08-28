# backend/wiki_market_generalized.py
from __future__ import annotations
from typing import Optional, List, Dict, Any, Tuple
import re
import httpx
import pandas as pd
from bs4 import BeautifulSoup

WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "CompanyEval/1.0 (market-share-parser)"}

# Column synonym sets
VENDOR_COLS = [
    "Vendor","Company","Brand","Manufacturer","Provider","Service","Network","Platform","Developer","Firm","Publisher","Vendor/Brand"
]
SHARE_COLS  = ["Share","Market share","Share %","Market Share","%","Share (%)","Share percent"]
UNITS_COLS  = ["Units","Shipments","Subscribers","Volume","Sales","Revenue","Installed base","Users","Market size"]

# Keywords to steer the search (extend as needed)
DEFAULT_TOPIC_KEYWORDS = [
    "market share", "by market share", "by shipments", "by subscribers",
    "vendor share", "brand share", "share by vendor", "shipments by vendor",
]

# Basic region keywords; you can pass a hint too
REGION_KEYWORDS = ["United States","US","U.S.","North America","Europe","EU","China","India","Japan","Global","Worldwide"]

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _clean_vendor(v: str) -> str:
    v = _norm(v)
    v = re.sub(r"\b(incorporated|inc\.?|corp\.?|corporation|ltd\.?|limited|plc|s\.?a\.?|n\.?v\.?|ag)\b", "", v, flags=re.I)
    v = re.sub(r"[\u00AE\u2122®™]", "", v)  # ® ™
    return _norm(v)

def wiki_search_pages(queries: List[str], limit: int = 6) -> List[str]:
    """Return candidate enwiki page URLs by search."""
    urls = []
    with httpx.Client(headers=HEADERS, timeout=20) as h:
        for q in queries:
            r = h.get(WIKI_SEARCH, params={
                "action": "query", "list": "search", "format": "json",
                "utf8": 1, "srlimit": limit, "srsearch": q
            })
            r.raise_for_status()
            for hit in r.json().get("query", {}).get("search", []):
                title = hit.get("title")
                if title:
                    urls.append(f"https://en.wikipedia.org/wiki/{title.replace(' ','_')}")
    # de-dup preserving order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out[:limit]

def _read_tables_with_captions(url: str) -> List[Tuple[pd.DataFrame, str]]:
    """Return list of (DataFrame, caption_text)."""
    with httpx.Client(headers=HEADERS, timeout=30) as h:
        r = h.get(url)
        r.raise_for_status()
        html = r.text
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    out = []
    for t in tables:
        try:
            df_list = pd.read_html(str(t))
        except Exception:
            continue
        if not df_list: 
            continue
        df = df_list[0]
        cap = t.find("caption")
        cap_text = _norm(cap.get_text()) if cap else ""
        out.append((df, cap_text))
    return out

def _find_col_idx(cols: List[str], candidates: List[str]) -> Optional[int]:
    cols_norm = [c.strip().lower() for c in cols]
    cands = [c.lower() for c in candidates]
    # prefer exact-ish contains, then loose contains
    for i, c in enumerate(cols_norm):
        if any(cc == c for cc in cands): return i
    for i, c in enumerate(cols_norm):
        if any(cc in c for cc in cands): return i
    return None

def _coerce_percent(series: pd.Series) -> pd.Series:
    s = series.astype(str)
    s = s.str.replace("%","", regex=False)
    s = s.str.replace(",","", regex=False)
    s = s.str.extract(r"([-+]?[0-9]*\.?[0-9]+)")[0]
    return pd.to_numeric(s, errors="coerce")

def _coerce_number(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(",","", regex=False)
    s = s.str.extract(r"([-+]?[0-9]*\.?[0-9]+)")[0]
    return pd.to_numeric(s, errors="coerce")

def _score_table(df: pd.DataFrame, caption: str, region_hint: Optional[str]) -> float:
    cols = [str(c) for c in df.columns]
    v = _find_col_idx(cols, VENDOR_COLS)
    s = _find_col_idx(cols, SHARE_COLS)
    u = _find_col_idx(cols, UNITS_COLS)
    score = 0.0
    if v is not None: score += 2.0
    if s is not None: score += 2.0
    if u is not None: score += 1.0
    # recency hint in column names or caption
    if any(re.search(r"20\d{2}|Q[1-4]\s*20\d{2}", str(c), re.I) for c in cols) or re.search(r"20\d{2}", caption):
        score += 0.5
    # region match
    if region_hint and re.search(re.escape(region_hint), caption, re.I):
        score += 0.8
    return score

def pick_best_market_table(url: str, region_hint: Optional[str]) -> Optional[Tuple[pd.DataFrame, str, Dict[str,int]]]:
    candidates = _read_tables_with_captions(url)
    best = None
    best_score = 0.0
    best_cols = {}
    for df, cap in candidates:
        cols = [str(c) for c in df.columns]
        v = _find_col_idx(cols, VENDOR_COLS)
        s = _find_col_idx(cols, SHARE_COLS)
        u = _find_col_idx(cols, UNITS_COLS)
        if v is None or (s is None and u is None):
            continue
        sc = _score_table(df, cap, region_hint)
        if sc > best_score:
            best = (df, cap)
            best_score = sc
            best_cols = {"v": v, "s": s, "u": u}
    if not best:
        return None
    return (best[0], best[1], best_cols)

def normalize_market_table(df: pd.DataFrame, col_map: Dict[str,int]) -> pd.DataFrame:
    v_idx, s_idx, u_idx = col_map["v"], col_map["s"], col_map["u"]
    out = pd.DataFrame()
    out["vendor"] = df.iloc[:, v_idx].astype(str).map(_clean_vendor)
    if s_idx is not None:
        out["share"] = _coerce_percent(df.iloc[:, s_idx])
    else:
        out["share"] = None
    if u_idx is not None:
        out["units"] = _coerce_number(df.iloc[:, u_idx])
    else:
        out["units"] = None

    # Drop headers/footers and non-sense rows
    out = out[~out["vendor"].str.fullmatch("", na=False)]
    out = out[~out["vendor"].str.contains("total|overall|sum|subtotal|world|worldwide", case=False, na=False)]
    out = out[~out["vendor"].str.contains("others|other", case=False, na=False)]
    out = out.dropna(subset=["vendor"])
    # Compute share if missing but units exist
    if "share" in out and out["share"].isna().all() and "units" in out and out["units"].notna().sum() >= 3:
        total = out["units"].sum()
        if total and total > 0:
            out["share"] = out["units"] / total * 100.0
    out = out.dropna(subset=["share"])
    out = out[out["share"] >= 0]
    # Consolidate duplicates (e.g., brand variants)
    out = out.groupby("vendor", as_index=False).agg({"share":"sum"})
    # Sort desc by share
    out = out.sort_values("share", ascending=False).reset_index(drop=True)
    return out

def compute_hhi(shares: List[float]) -> float:
    # Using % shares (0..100), HHI = sum(s_i^2)
    return float(sum((s)**2 for s in shares))

def wiki_market_share_generalized(
    company_name: str,
    aliases: Optional[List[str]] = None,
    industry_hints: Optional[List[str]] = None,
    region_hint: Optional[str] = None,
    extra_queries: Optional[List[str]] = None,
    search_limit: int = 6
) -> Optional[Dict[str, Any]]:
    """
    Returns dict with keys:
      { 'url', 'region', 'period_hint', 'share_table': DataFrame-like records,
        'company_share', 'company_rank', 'hhi' }
    or None if nothing usable found.
    """
    aliases = aliases or []
    industry_hints = industry_hints or []
    # Build search queries
    base_terms = [company_name] + aliases + industry_hints
    query_bundles = []
    for bt in base_terms:
        for kw in DEFAULT_TOPIC_KEYWORDS:
            q = f"{bt} {kw}"
            if region_hint: q += f" {region_hint}"
            query_bundles.append(q)
    if extra_queries:
        query_bundles.extend(extra_queries)

    urls = wiki_search_pages(query_bundles, limit=search_limit)
    # Also try a generic topic page if company-specific pages fail
    topic_terms = (industry_hints or [company_name])
    for t in topic_terms:
        urls += wiki_search_pages([f"{t} market share", f"List of {t} by market share"], limit=2)

    # De-dup
    seen, cand_urls = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); cand_urls.append(u)

    for url in cand_urls[:search_limit]:
        pick = pick_best_market_table(url, region_hint)
        if not pick:
            continue
        df, caption, col_map = pick
        norm = normalize_market_table(df, col_map)
        if norm.empty:
            continue
        # period hint from caption or column headers
        period_hint = None
        cap = caption or ""
        m = re.search(r"(20\d{2})(?:[/\-–](Q[1-4]))?", cap)
        if m:
            period_hint = m.group(0)
        else:
            # look in original columns
            headers = " ".join(map(str, df.columns))
            m2 = re.search(r"(20\d{2})(?:[/\-–](Q[1-4]))?", headers)
            if m2: period_hint = m2.group(0)

        # compute vendor share/rank for the company
        targets = [company_name.lower()] + [a.lower() for a in aliases]
        row = None
        for t in targets:
            cand = norm[norm["vendor"].str.lower().str.contains(re.escape(t))]
            if not cand.empty:
                row = cand.iloc[0]
                break
        company_share = float(row["share"]) if row is not None else None
        company_rank  = int(norm.index[norm["vendor"] == row["vendor"]][0] + 1) if row is not None else None

        # HHI
        hhi = compute_hhi(norm["share"].tolist())

        return {
            "url": url,
            "region": region_hint,
            "period_hint": period_hint,
            "share_table": norm.to_dict(orient="records"),
            "company_share": company_share,
            "company_rank": company_rank,
            "hhi": hhi,
        }
    return None
