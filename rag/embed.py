"""Embeds chunk text through the embedding server; vectors are keyed by chunk_id and a hash of the embedded text."""
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from rag import config

BATCH = 32
_SLOTS = threading.BoundedSemaphore(config.SERVER["max_concurrent"])
# the plan writes מרבי and רח"ק, users also write מירבי and רחק / רח״ק; the model reads such variants as different words (not מרחק, distance)
VARIANTS = [(re.compile("מירב"), "מרב"),
            (re.compile(r"(?<![א-ת])([הוכלבש]{0,2})רח[\"'׳״`]?ק(?![א-ת])"), r'\1רח"ק')]


class EmbeddingError(RuntimeError):
    """The embedding server did not return vectors."""


def embed(texts: list[str], model: str) -> np.ndarray:
    """Normalized vectors for `texts`; one retry on a refused or reset connection, none after a timeout."""
    srv = config.SERVER
    req = urllib.request.Request(
        f"{config.BASE_URL}/embeddings",
        data=json.dumps({"model": model, "input": texts}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with _SLOTS:
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=srv["timeout_s"]) as resp:
                    data = sorted(json.load(resp)["data"], key=lambda d: d["index"])
                break
            except urllib.error.HTTPError as e:
                raise EmbeddingError(f"embedding server rejected the request ({e.code}): {e.read()[:200]!r}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                timed_out = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
                if attempt == 2 or timed_out:
                    raise EmbeddingError(f"embedding server at {config.BASE_URL} did not answer: {e}") from e
                time.sleep(srv["retry_delay_s"])
    vecs = np.array([d["embedding"] for d in data], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def normalize_for_embedding(text: str) -> str:
    """Maps spelling variants to the plan's spelling; applied to chunks and queries alike, never to the shown text."""
    for pattern, replacement in VARIANTS:
        text = pattern.sub(replacement, text)
    return text


@lru_cache(maxsize=1024)
def embed_query(text: str, model: str) -> np.ndarray:
    """Cached vector of one query; repeated questions (e.g. generic plan-border ones) skip the server."""
    vec = embed([normalize_for_embedding(text)], model)[0]
    vec.flags.writeable = False
    return vec


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def embed_chunks(chunks_path: Path, out: Path, model_key: str | None = None) -> None:
    """Writes `out` with one vector per chunk; vectors whose chunk id and text are unchanged are reused."""
    m = config.model(model_key)
    chunk_meta = config.read_meta(chunks_path)
    if chunk_meta.get("tokenizer") != m["tokenizer"]:
        raise ValueError(f"chunks were built with tokenizer {chunk_meta.get('tokenizer')!r}, model needs {m['tokenizer']!r}")

    df = pd.read_parquet(chunks_path)
    texts = (m["doc_prefix"] + df["embed_text"].map(normalize_for_embedding)).tolist()
    hashes = [text_hash(t) for t in texts]

    cached = {}
    if out.exists() and config.read_meta(out).get("model") == m["name"]:
        prev = pd.read_parquet(out)
        if "text_hash" in prev:
            cached = {(c, h): v for c, h, v in zip(prev["chunk_id"], prev["text_hash"], prev["embedding"])}
    todo = [i for i, key in enumerate(zip(df["chunk_id"], hashes)) if key not in cached]

    fresh = {}
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        fresh.update(zip(batch, embed([texts[i] for i in batch], m["name"])))
    matrix = np.vstack([fresh[i] if i in fresh else cached[(c, hashes[i])] for i, c in enumerate(df["chunk_id"])])
    if matrix.shape != (len(df), m["dim"]):
        raise ValueError(f"got vectors of shape {matrix.shape}, expected ({len(df)}, {m['dim']})")

    config.write_parquet(
        pd.DataFrame({"chunk_id": df["chunk_id"], "text_hash": hashes, "embedding": list(matrix)}),
        out,
        {"model": m["name"], "tokenizer": m["tokenizer"], "dim": m["dim"], "normalized": True,
         "doc_prefix": m["doc_prefix"], "query_prefix": m["query_prefix"]},
    )
    print(f"{len(df)} vectors ({len(todo)} embedded, {len(df) - len(todo)} reused) -> {out}")


def load_index(chunks_path: Path, emb_path: Path) -> tuple[pd.DataFrame, np.ndarray]:
    """Chunks indexed by chunk_id and their vectors in the same order; refuses stale embeddings."""
    df = pd.read_parquet(chunks_path)
    emb = pd.read_parquet(emb_path).set_index("chunk_id").reindex(df["chunk_id"])
    prefix = config.read_meta(emb_path)["doc_prefix"]
    expected = [text_hash(prefix + normalize_for_embedding(t)) for t in df["embed_text"]]
    if emb["text_hash"].tolist() != expected:
        raise ValueError(f"{emb_path.name} does not match {chunks_path.name}; rerun the embeddings stage")
    return df.set_index("chunk_id"), np.vstack(emb["embedding"])
