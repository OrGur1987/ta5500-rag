"""Shared settings (from models.yml) and parquet helpers that keep provenance metadata."""
import json
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RAG_DATA_DIR") or ROOT / "local_resources")  # built artifacts: nodes, chunks, embeddings
_CFG = yaml.safe_load((ROOT / "models.yml").read_text(encoding="utf-8"))
CHUNKING = _CFG["chunking"]
RETRIEVAL: dict = _CFG["retrieval"]
LABELS = _CFG["labels"]
SERVER = _CFG["server"]
BASE_URL = os.environ.get("RAG_EMBED_BASE_URL") or SERVER["base_url"]
META_KEY = b"pipeline"


def model(name: str | None = None) -> dict:
    """Registry entry of `name` (default: the active model), including its key as `name`."""
    name = name or _CFG["active"]
    return {"name": name, **_CFG["models"][name]}


def write_parquet(df: pd.DataFrame, path: Path, meta: dict) -> None:
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table.replace_schema_metadata({**(table.schema.metadata or {}), META_KEY: json.dumps(meta).encode()}), path)


def read_meta(path: Path) -> dict:
    raw = (pq.read_schema(path).metadata or {}).get(META_KEY)
    return json.loads(raw) if raw else {}
