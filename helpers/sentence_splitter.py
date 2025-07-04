import re
from typing import List

_SENT_BOUNDARY_RE = re.compile(
    r"""
        (?P<end>[.!?])      # sentence-ending punctuation
        (?:\s+|$)           # followed by whitespace or end of string
    """,
    re.VERBOSE,
)

_ABBREVS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.",
    "etc.", "e.g.", "i.e.", "fig.", "al.", "jan.", "feb.", "mar.",
    "apr.", "jun.", "jul.", "aug.", "sep.", "oct.", "nov.", "dec.",
}

def split_sentences(text: str) -> List[str]:
    """Return a list of sentences found in *text*."""
    sentences, start = [], 0
    for m in _SENT_BOUNDARY_RE.finditer(text):
        candidate = text[start : m.end("end")].strip()
        if any(candidate.lower().endswith(a) for a in _ABBREVS):
            continue          # abbreviation → keep scanning
        sentences.append(candidate)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences
