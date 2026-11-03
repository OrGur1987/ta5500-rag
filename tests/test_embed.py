import io
import urllib.error

import numpy as np
import pandas as pd
import pytest

from rag import config, embed

MODEL = config.model()


def fake_embed(calls):
    def run(texts, model):
        calls.append(list(texts))
        return np.eye(MODEL["dim"], dtype=np.float32)[[len(t) % MODEL["dim"] for t in texts]]
    return run


def write_chunks(path, texts, tokenizer=None):
    df = pd.DataFrame({"chunk_id": [f"c{i}" for i in range(len(texts))], "embed_text": texts})
    config.write_parquet(df, path, {"tokenizer": tokenizer or MODEL["tokenizer"]})


def test_only_changed_chunks_are_embedded_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(embed, "embed", fake_embed(calls))
    chunks, out = tmp_path / "chunks.parquet", tmp_path / "emb.parquet"

    write_chunks(chunks, ["אחד", "שניים", "שלושה"])
    embed.embed_chunks(chunks, out)
    assert sum(map(len, calls)) == 3

    calls.clear()
    embed.embed_chunks(chunks, out)
    assert calls == []

    write_chunks(chunks, ["אחד", "שניים שונה", "שלושה"])
    embed.embed_chunks(chunks, out)
    assert calls == [["שניים שונה"]]


def test_embeddings_carry_their_provenance_and_are_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(embed, "embed", fake_embed([]))
    chunks, out = tmp_path / "chunks.parquet", tmp_path / "emb.parquet"
    write_chunks(chunks, ["אחד"])
    embed.embed_chunks(chunks, out)
    meta = config.read_meta(out)
    assert (meta["model"], meta["dim"], meta["tokenizer"]) == (MODEL["name"], MODEL["dim"], MODEL["tokenizer"])
    assert np.isclose(np.linalg.norm(pd.read_parquet(out)["embedding"].iloc[0]), 1)


def test_chunks_built_with_another_tokenizer_are_refused(tmp_path):
    chunks = tmp_path / "chunks.parquet"
    write_chunks(chunks, ["אחד"], tokenizer="other/tokenizer")
    with pytest.raises(ValueError, match="tokenizer"):
        embed.embed_chunks(chunks, tmp_path / "emb.parquet")


def test_load_index_refuses_embeddings_that_no_longer_match(tmp_path, monkeypatch):
    monkeypatch.setattr(embed, "embed", fake_embed([]))
    chunks, out = tmp_path / "chunks.parquet", tmp_path / "emb.parquet"
    write_chunks(chunks, ["אחד", "שניים"])
    embed.embed_chunks(chunks, out)
    df, matrix = embed.load_index(chunks, out)
    assert list(df.index) == ["c0", "c1"] and matrix.shape == (2, MODEL["dim"])

    write_chunks(chunks, ["אחד", "שניים שונה"])
    with pytest.raises(ValueError, match="rerun"):
        embed.load_index(chunks, out)


def test_query_vectors_are_cached_by_text(monkeypatch):
    calls = []
    monkeypatch.setattr(embed, "embed", fake_embed(calls))
    embed.embed_query.cache_clear()
    first = embed.embed_query("שאלה", "m")
    assert embed.embed_query("שאלה", "m") is first and len(calls) == 1


class FakeUrlopen:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def __call__(self, req, timeout):
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("error, attempts", [
    (urllib.error.URLError("connection refused"), 2),  # retried once
    (TimeoutError("timed out"), 1),  # a stalled server is not asked twice
    (urllib.error.HTTPError("u", 500, "bad", {}, io.BytesIO(b"too large")), 1),  # a rejection will not change
])
def test_server_failures_raise_embedding_error(monkeypatch, error, attempts):
    urlopen = FakeUrlopen(error)
    monkeypatch.setattr(embed.urllib.request, "urlopen", urlopen)
    monkeypatch.setitem(config.SERVER, "retry_delay_s", 0)
    with pytest.raises(embed.EmbeddingError):
        embed.embed(["x"], "m")
    assert urlopen.calls == attempts
