# import stuff
from __future__ import annotations
import argparse, hashlib, json, os, sys
from datetime import datetime, UTC
from typing import List

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

import fitz  # PyMuPDF
import json
import random


def print_obj(obj, i=1, params=["description", "inputs", "outputs", "laws"]):
    print(f"{i} - ")
    for param in params:
        print(f"{param}: {obj[param]}")
    

def parse_func_resp(resp, obj_name="artifacts", params=["description", "inputs", "outputs", "laws"]):
    args_raw = resp.choices[0].message.tool_calls[0].function.arguments
    raws = json.loads(args_raw)[obj_name]

    obj_lst = []
    for i, obj in enumerate(raws, 1):
        print_obj(obj, i, params)
        obj_lst.append(obj)
    
    return obj_lst

# be free of objectives. be free of money. just play. 
def get_source(text, title, n=20000):
    n = 20000  # length of section in characters
    max_index = len(text) - n

    # Ensure we don't go out of bounds
    if max_index <= 0:
        book_section = text  # fallback: use the whole text
    else:
        index = random.randint(0, max_index)
        book_section = text[index : index + n]

    src = {}
    src["title"] = title
    src["text"] = book_section

    return src

def artifact_as_string(artifact):
    return f"ARTIFACT:\n{artifact["name"]}\n\nDESCRIPTION:\n{artifact["description"]}\n\nINPUTS:\n{artifact["inputs"]}\n\nOUTPUTS:\n{artifact["outputs"]}\n\nLAWS:\n{artifact["laws"]}"



# artifact structure
# ── LLM function spec (updated) ─────────────────────────────────────────--
ART_FUNC = {
    "type": "function",
    "function": {
        "name": "artifact_generator",
        "description": "Generate artifact based on the following objective; machine, material, software, theory, workflow, principle, etc. \
            Include artifact name, description, inputs and outputs, and the laws that the artifact operates under.",
        "parameters": {
            "type": "object",
            "properties": {
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "name", "description", "inputs", "outputs", "laws"
                        ],
                        "properties": {
                            "name":  {"type": "string"},
                            "description": {"type": "string"},
                            "inputs": {"type": "string"},
                            "outputs": {"type": "string"},
                            "laws": {"type":"string"}
                        }
                    }
                }
            },
            "required": ["artifacts"]
        }
    }
}


SYS_PROMPT = (
    "You are a design engineer. Using ONLY the text and the objective, "
    "propose up to {k} concrete artifacts. Each artifact must include:\n"
    "• tool_anchor (machine, material, software, theory, workflow, principle, etc.)\n"
    "• a description of the artifact\n"
    "• the inputs and outputs of the artifact\n"
    "• the laws which the artifact operates under.\n"
    #"• how this artifact achieves the objective.\n"
    # "Return JSON via function schema." 
    )




library = "/Users/b/fantasiagenesis/crayon/library"

load_dotenv()
client = OpenAI()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")


# List all files in the library (sorted for consistent ordering)
books = sorted(os.listdir(library))

# Select the second book (index 1 because indexing starts at 0)
library_text = {}
for book in books:
    book_path = os.path.join(library, book)
    # Read the content of the second book
    # Read text from the PDF
    with fitz.open(book_path) as doc:
        book_text = ""
        for page in doc:
            book_text += page.get_text()

    book_title = book.removesuffix(".pdf")
    library_text[book_title] = book_text

for text in library_text.keys():
    print(text)


obj_existing = "What are the artifacts that exist/are in use based on the following text?"
obj = obj_existing
sys_msg = SYS_PROMPT.format(k=3)
print(sys_msg)
src = get_source(book_text, "Gene Cloning and DNA Analysis")
user_msg = f"OBJECTIVE:\n{obj}\n\nTITLE:\n{src["title"]}\nTEXT:\n{src["text"]}"
print(user_msg)


resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role":"system","content":sys_msg}, {"role":"user","content":user_msg}],
        tools=[ART_FUNC], tool_choice="auto", temperature=0.5
)


args_raw = resp.choices[0].message.tool_calls[0].function.arguments
raws = json.loads(args_raw)["artifacts"]

print(raws)

library_artifacts = {}

for title, text in library.items():   # underscore = “throw-away” loop var
    if not text or not text.strip():
        pass
    else:                                 
        for _ in range(5): 
            src = get_source(text, title)                  # ➜ {'title': ..., 'text': ...}

            sys_msg = SYS_PROMPT.format(k=3)
            user_msg = (
                f"OBJECTIVE:\n{obj}\n\n"
                f"TITLE:\n{src['title']}\n"
                f"TEXT:\n{src['text']}"
            )

            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user",   "content": user_msg},
                ],
                tools=[ART_FUNC],
                tool_choice="auto",
                temperature=0.5,
            )

            arts = parse_func_resp(resp)                   # ➜ list of artifacts
            library_artifacts.setdefault(title, []).extend(arts)


    
