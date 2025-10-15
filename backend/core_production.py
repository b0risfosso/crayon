# core_production.py
from flask import Flask, request, jsonify
from openai import OpenAI
import os
from core_production_prompts import ritual_atomizer_system_prompt, ritual_atomizer_user_prompt
from core_production_models import RitualAtomizerOutput

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))



def run_ritual_atomizer(core_title: str, core_description: str):
    system_prompt = ritual_atomizer_system_prompt.format(
        core_title=core_title, core_description=core_description
    )
    user_prompt = ritual_atomizer_user_prompt.format(
        core_title=core_title, core_description=core_description
    )

    response = client.responses.parse(
        model="gpt-5-mini-2025-08-07",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text_format=RitualAtomizerOutput,
    )

    return resp.output_parsed

@app.route("/api/run_ritual_atomizer", methods=["POST"])
def run_atomizer_api():
    data = request.get_json()
    core_title = data.get("core_title", "")
    core_description = data.get("core_description", "")

    if not core_title or not core_description:
        return jsonify({"error": "Missing core_title or core_description"}), 400

    result = run_ritual_atomizer(core_title, core_description)
    return jsonify({"result": result})

