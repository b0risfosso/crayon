def assert_loader_fields(model_cls, cypher_keys: set, node_name: str):
    model_keys = set(model_cls.model_fields.keys())
    missing = cypher_keys - model_keys
    extra   = model_keys - cypher_keys - {"last_seen", "stale"}

    if missing:
        raise ValueError(
            f"[{node_name}] Loader expects keys missing from model: {missing}"
        )
    if extra:
        print(f"[WARN] {node_name} model defines fields not saved to Neo4j: {extra}")
