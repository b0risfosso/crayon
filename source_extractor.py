# extractor.py
from openai import OpenAI
from pydantic import BaseModel
import os
import json
from models import Source

# Load OpenAI API key (set OPENAI_API_KEY in your env)
client = OpenAI()


# Extraction prompt template
EXTRACT_PROMPT = """
You are an expert data extractor. Given this paper metadata, extract a JSON object with these fields:

- id: string (keep the given id)
- topic: string (keep the given topic)
- type: string (set to "paper")
- url: string (keep the given url)
- year: integer (publication year)
- credibility: float between 0 and 1 (estimate quality: 1.0 = top-tier, 0.5 = mid, 0.1 = dubious)

Paper metadata:

TITLE: {title}
SUMMARY: {summary}
YEAR: {year}
URL: {url}

JSON:
"""

def clean_json_string(s: str) -> str:
    # Remove ```json ... ```
    if s.startswith("```json"):
        s = s[len("```json"):].strip()
    if s.startswith("```"):
        s = s[len("```"):].strip()
    if s.endswith("```"):
        s = s[:-3].strip()
    return s


def extract_source(paper_dict) -> Source:
    prompt = EXTRACT_PROMPT.format(
        title=paper_dict["title"],
        summary=paper_dict["summary"],
        year=paper_dict["year"],
        url=paper_dict["url"]
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=300
    )
    json_str = response.choices[0].message.content.strip()
    json_str = clean_json_string(json_str)
    print("Cleaned JSON:\n", json_str)
    print("Raw LLM output:\n", json_str)

    # Safe parse
    data = json.loads(json_str)
    # Insert paper ID and topic back in
    data["title"] = paper_dict["title"]
    data["id"] = paper_dict["id"]
    data["topic"] = paper_dict["topic"]
    data["type"] = "paper"
    data["summary"] = paper_dict["summary"]

    # Validate
    source = Source(**data)
    return source

test = """
if __name__ == "__main__":
    # Example: run from Scout result
    from scout import arxiv_search
    papers = arxiv_search("heart morphogenesis", max_results=1)
    paper = papers[0]
    print(f"\nExtracting Source for: {paper['title']}\n")

    source = extract_source(paper)
    print("\nValidated Source object:\n", source.model_dump())
"""