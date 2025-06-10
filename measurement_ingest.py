# measurement_ingest.py
import pandas as pd, time, hashlib
from neo4j import GraphDatabase
from models import Measurement
from loader import driver, load_metrics  # if you generate new metrics ad-hoc

CSV_SCHEMA = ["artifact_id", "metric_name", "value", "unit", "timestamp"]  # epoch-ms

def ingest_csv(path: str):
    df = pd.read_csv(path)
    missing = [c for c in CSV_SCHEMA if c not in df.columns]
    if missing:
        raise ValueError(f"Missing cols: {missing}")

    with driver.session() as sess:
        for _, row in df.iterrows():
            # lookup Metric id
            res = sess.run(
                """
                MATCH (a:Artifact {id:$aid})-[:MEASURED_BY]->(k:Metric {name:$mname})
                RETURN k.id AS kid
                """,
                aid=row.artifact_id, mname=row.metric_name
            )
            rec = res.single()
            if not rec:
                print(f"⚠️  metric not found → skipping row {row.to_dict()}")
                continue
            kid = rec["kid"]
            mid = hashlib.md5((kid+str(row.timestamp)).encode()).hexdigest()[:10]
            meas = Measurement(
                id=f"{kid}_d{mid}",
                metric_id=kid,
                artifact_id=row.artifact_id,
                timestamp=int(row.timestamp),
                value=float(row.value)
            )
            sess.run(
                """
                MERGE (m:Measurement {id:$id})
                SET   m.value=$value,
                      m.timestamp=$timestamp
                WITH m
                MATCH (k:Metric {id:$kid})
                MERGE (k)<-[:DATA_OF]-(m)
                """,
                **meas.model_dump()
            )
    print("✅ ingest complete")
