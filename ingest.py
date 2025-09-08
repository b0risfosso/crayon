import sqlite3
import csv

db_path = "narratives_data.db"
csv_path = "narratives.csv"

# Connect to database
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Open CSV and insert rows
with open(csv_path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        title = row["title"].strip()
        description = row["description"].strip()
        cur.execute(
            "INSERT INTO narratives (title, description) VALUES (?, ?)",
            (title, description)
        )

# Commit and close
conn.commit()
conn.close()

print("Ingested narratives.csv into narratives table.")
