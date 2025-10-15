from pydantic import BaseModel
from typing import List

class Core(BaseModel):
    title: str
    description: str

class ContextAnchors(BaseModel):
    users: List[str]
    jobs_to_be_done: List[str]
    must_haves: List[str]
    guardrails: List[str]

class Atoms(BaseModel):
    commands: List[str]
    constraints: List[str]
    personas: List[str]
    methods: List[str]
    anti_atoms: List[str]

class RitualAtomizerOutput(BaseModel):
    core: Core
    context_anchors: ContextAnchors
    atoms: Atoms