# load_json.py
import json
from models import Source
from loader import load_source

with open("sources.json") as f:
    raw = json.load(f)
    for entry in raw:
        s = Source(**entry)
        load_source(s)
