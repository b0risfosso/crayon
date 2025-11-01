# models.py
from __future__ import annotations
from typing import List
from pydantic import BaseModel, Field

try:
    # Pydantic v2
    from pydantic import field_validator as _validator
except Exception:
    # Pydantic v1 fallback
    from pydantic import validator as _validator  # type: ignore


class PictureItem(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    picture: str = Field(..., min_length=6)
    function: str = Field(..., min_length=6)


class PicturesResponse(BaseModel):
    vision: str = Field(..., min_length=1)
    pictures: List[PictureItem]

    @_validator("pictures")
    def _ensure_reasonable_length(cls, v):
        if not (1 <= len(v) <= 20):
            raise ValueError("pictures should contain a reasonable number of items (1–20).")
        return v


__all__ = ["PictureItem", "PicturesResponse"]
