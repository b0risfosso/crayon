# backend/consensus_adapter.py
from typing import Optional, Tuple, Dict
import os, datetime as dt

# TODO: wire your actual SDK / API here
PROVIDER = os.environ.get("CONSENSUS_PROVIDER", "none")

def get_consensus_eps(symbol: str) -> Optional[Tuple[float, float, Dict]]:
    """
    Return (eps_fy1, eps_fy2, meta) or None if unavailable.
    meta can include provider, as_of, fiscal_years.
    """
    if PROVIDER == "none":
        return None
    # Example sketch (pseudo-code):
    # data = your_provider.fetch_estimates(symbol, fields=["eps_fy1","eps_fy2","fy1_year","fy2_year"])
    # return (data.eps_fy1, data.eps_fy2, {"provider": "FactSet", "as_of": data.as_of, "fy1": data.fy1_year, "fy2": data.fy2_year})
    return None
