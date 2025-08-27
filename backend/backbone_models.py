# backend/backbone_models.py
from __future__ import annotations
from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl

class Ticker(BaseModel):
    symbol: str
    exchange_qid: Optional[str] = Field(None, description="Wikidata QID for the exchange, P414")
    note: Optional[str] = None

class CompanyBackbone(BaseModel):
    canonical_name: str
    aliases: List[str] = []
    country: Optional[str] = None
    headquarters_city: Optional[str] = None

    # Web & knowledge
    official_website: Optional[HttpUrl] = None
    domain: Optional[str] = None
    wikipedia_url: Optional[HttpUrl] = None
    wikidata_id: Optional[str] = None

    # Securities & registries
    is_public: bool = False
    tickers: List[Ticker] = []
    isin: Optional[str] = None
    cik: Optional[str] = None    # zero-padded 10-digit string
    lei: Optional[str] = None

    # Provenance
    confidence: float = 0.0
    notes: List[str] = []
