# backend/backbone_api.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from .backbone_resolver import build_backbone
from .backbone_models import CompanyBackbone

router = APIRouter()

@router.get("/backbone", response_model=CompanyBackbone)
async def get_backbone(company_name: str = Query(..., min_length=1)):
    try:
        bb = await build_backbone(company_name.strip())
        return bb
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
