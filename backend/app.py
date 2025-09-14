import os
import openai
import sqlite3
from flask import Flask, jsonify, abort

app = Flask(__name__)

DB_PATH = "/var/www/site/data/narratives_data.db"  # keep consistent with your setup

openai.api_key = os.environ.get("OPENAI_API_KEY")
if not openai.api_key:
    # This is for local testing or if you want to hardcode for a quick test
    # REMOVE THIS LINE OR REPLACE WITH SECURE METHOD FOR PRODUCTION
    print("WARNING: OPENAI_API_KEY environment variable not set. Using a placeholder or hardcoded value for testing.")

SYSTEM_MESSAGE = """
You are an assistant trained to generate narrative dimensions for any given domain.
Each narrative dimension should have two parts:

1. A compressed, evocative description (1–2 sentences, almost like a thesis or proverb).
   - It should feel like a distilled truth or lens, e.g., "Energy is control. Empires rose with coal, oil wars redrew borders, battery supply chains shape the future."

2. A short list of concrete narrative targets that exist inside this dimension.
   - These are examples, subtopics, or arenas where stories can be developed, e.g., "geopolitics of oil/gas, rare earths, solar supply chains, energy security."

Output format:
[Number]. [Dimension Name] — [Thesis/Description]
   Narrative Targets: [list of 3–6 examples]

Generate 5–8 narrative dimensions unless otherwise requested.
"""

@app.route('/generate_narratives', methods=['POST'])
def generate_narratives():
    data = request.get_json()
    domain = data.get('domain')

    if not domain:
        return jsonify({"error": "Missing 'domain' in request body"}), 400

    user_message = f"Create narrative dimensions for the domain of {domain}."

    try:
        if not openai.api_key:
            return jsonify({"error": "OpenAI API key is not set. Please configure OPENAI_API_KEY."}), 500

        # Make the API call to OpenAI
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",  # You can choose a different model like "gpt-4"
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": user_message}
            ],
            max_tokens=1000, # Adjust as needed
            temperature=0.7 # Adjust for creativity (0.0-1.0)
        )

        # Extract the content from the response
        narrative_output = response.choices[0].message.content
        return jsonify({"narrative_dimensions": narrative_output})

    except openai.APIError as e:
        print(f"OpenAI API Error: {e}")
        return jsonify({"error": f"OpenAI API Error: {e.user_message}"}), 500
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


def _query_all(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON;")
    try:
        cur = con.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        con.close()


@app.get("/api/narratives")
def api_narratives():
    rows = _query_all("""
        SELECT id, title, created_at
        FROM narratives
        WHERE COALESCE(title,'') <> ''
        ORDER BY COALESCE(created_at, '') DESC, id DESC
    """)
    return jsonify(rows)


@app.get("/api/narratives/<int:narrative_id>/dimensions")
def api_narrative_dimensions(narrative_id: int):
    # Optional: 404 if the narrative doesn't exist
    exists = _query_all("SELECT 1 AS ok FROM narratives WHERE id = ? LIMIT 1", (narrative_id,))
    if not exists:
        abort(404, description="Narrative not found")

    dims = _query_all("""
        SELECT id, narrative_id, title, description, created_at
        FROM narrative_dimensions
        WHERE narrative_id = ?
        ORDER BY id ASC
    """, (narrative_id,))
    return jsonify(dims)
