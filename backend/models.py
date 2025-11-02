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
    # NEW: optional focus string; model may return null or omit it entirely
    focus: Optional[str] = Field(default=None)
    pictures: List[PictureItem]

    @_validator("pictures")
    def _ensure_reasonable_length(cls, v):
        if not (1 <= len(v) <= 20):
            raise ValueError("pictures should contain a reasonable number of items (1–20).")
        return v



class FocusItem(BaseModel):
    dimension: str = Field(..., min_length=2, max_length=64)
    focus: str = Field(..., min_length=6)
    goal: str = Field(..., min_length=6)


class FocusesResponse(BaseModel):
    vision: str = Field(..., min_length=1)
    focuses: List[FocusItem]

    @_validator("focuses")
    def _ensure_reasonable_length(cls, v):
        if not (1 <= len(v) <= 24):
            raise ValueError("focuses should contain a reasonable number of items (1–24).")
        return v


__all__ = ["PictureItem", "PicturesResponse"]


# Try to export symbols if __all__ exists
try:
    __all__  # type: ignore
    __all__.extend(["FocusItem", "FocusesResponse"])  # type: ignore
except Exception:
    pass