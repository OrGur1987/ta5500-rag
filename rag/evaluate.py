"""Scores retrieval on eval_questions.yml in three modes: plain similarity, location detected from text, explicit filters.

Usage: python -m rag.evaluate [-v] [--model KEY] [-k 5] [--mode text] [--category listing heading] [--fresh]

Query vectors are kept in `.eval_cache.pkl`, so a rerun needs the embedding server only for new or edited questions.
"""
import argparse
import pickle
from collections import Counter, defaultdict

import numpy as np
import yaml

from rag import config, retrieval
from rag import embed as embedding
from rag.retrieval import Result, Retriever

CACHE = config.ROOT / ".eval_cache.pkl"
MODES = ("baseline", "text", "filters")


def cache_embeddings(fresh: bool) -> None:
    """Makes the embedding call remember its vectors on disk, keyed by model and text."""
    store = {} if fresh or not CACHE.exists() else pickle.loads(CACHE.read_bytes())
    real = embedding.embed

    def embed(texts: list[str], model: str) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if (model, t) not in store]
        if missing:
            store.update(zip(((model, t) for t in missing), real(missing, model)))
            CACHE.write_bytes(pickle.dumps(store))
        return np.vstack([store[(model, t)] for t in texts])

    embedding.embed = retrieval.embed = embed


def run_question(ret: Retriever, q: dict, mode: str, k: int) -> tuple[list[str], Result | None]:
    """Chunk ids best first, and the search result (None for the plain-similarity baseline)."""
    if mode == "baseline":
        return list(ret.scores(q["question"]).nlargest(k).index), None
    if mode == "text":
        result = ret.search(q["question"])
    elif "filters" in q:
        result = ret.search(q["question"], **q["filters"])
    else:  # oracle: the area of the gold record, as the GIS layer would give it
        areas = sorted({a for g in q["gold"] if isinstance(a := ret.chunks["area"].get(ret.owner[g]), str)})
        result = ret.search(q["question"], areas=areas or None)
    return [h["chunk_id"] for h in result.hits], result


def evaluate(model_key: str | None, k: int, verbose: bool, modes: tuple[str, ...], categories: list[str] | None, fresh: bool) -> None:
    cache_embeddings(fresh)
    ret = Retriever(config.DATA_DIR, model_key)
    questions = yaml.safe_load((config.ROOT / "eval_questions.yml").read_text(encoding="utf-8"))["questions"]
    questions = [q for q in questions if not categories or q["category"] in categories]
    print(f"model={ret.m['name']} k={k}")
    for mode in modes:
        stats = defaultdict(lambda: defaultdict(list))
        bands, depths = defaultdict(list), Counter()
        for q in questions:
            got, result = run_question(ret, q, mode, k)
            if result:
                bands["no_answer" if not q["gold"] else "answerable"].append(result.band)
                depths.update(h["depth"] for h in result.hits if h["via"] == "ref")
            if not q["gold"]:
                continue
            gold = {ret.owner[g] for g in q["gold"]}
            ranks = [got.index(g) + 1 for g in gold if g in got]
            s = stats[q["category"]]
            s["hit"].append(float(len(ranks) == len(gold)))
            s["recall"].append(len(ranks) / len(gold))
            s["mrr"].append(1 / min(ranks) if ranks else 0.0)
            s["size"].append(len(got))
            s["tokens"].append(int(ret.chunks.loc[got, "tokens"].sum()))
            if q.get("via_ref"):
                s["via"].append(float({ret.owner[v] for v in q["via_ref"]} <= set(got)))
            if verbose and len(ranks) < len(gold):
                print(f"  [{mode}] MISS {q['id']} missing={sorted(gold - set(got))} got={got[:8]}")
        print(f"\n-- {mode}")
        for cat, s in stats.items():
            print(f"{cat:10s} n={len(s['hit']):2d} Hit={np.mean(s['hit']):.2f} recall={np.mean(s['recall']):.2f} MRR={np.mean(s['mrr']):.2f}"
                  f" chunks={np.mean(s['size']):.1f} tokens={np.mean(s['tokens']):.0f}"
                  + (f" via_reached={np.mean(s['via']):.2f}" if s["via"] else ""))
        for name, values in bands.items():
            print(f"{name:10s} bands: " + ", ".join(f"{b}={values.count(b)}" for b in ("high", "medium", "low")))
        if depths:
            print("reference hits by depth:", dict(sorted(depths.items())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="registry key in models.yml (default: active)")
    ap.add_argument("-k", type=int, default=5, help="chunks returned by the baseline")
    ap.add_argument("-v", "--verbose", action="store_true", help="print misses")
    ap.add_argument("--mode", nargs="+", choices=MODES, default=MODES, help="modes to run (default: all)")
    ap.add_argument("--category", nargs="+", help="only these question categories")
    ap.add_argument("--fresh", action="store_true", help="ignore and rebuild the cached query vectors")
    args = ap.parse_args()
    evaluate(args.model, args.k, args.verbose, tuple(args.mode), args.category, args.fresh)
