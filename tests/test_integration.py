import urllib.request
from collections import defaultdict

import pytest
import yaml

from rag import config
from rag.evaluate import run_question
from rag.retrieval import Retriever

pytestmark = pytest.mark.integration
QUESTIONS = config.ROOT / "eval_questions.yml"


@pytest.fixture(scope="module")
def retriever():
    model = config.model()
    if not (config.DATA_DIR / f"embeddings_{model['name']}.parquet").exists():
        pytest.skip("artifacts are not built (run preprocess.py)")
    try:
        urllib.request.urlopen(f"{config.BASE_URL}/models", timeout=2)
    except OSError:
        pytest.skip("embedding server is not reachable")
    ret = Retriever(config.DATA_DIR)
    normal = config.SERVER["timeout_s"]
    config.SERVER["timeout_s"] = 60  # the first request may have to load the model
    try:
        ret.warm_up()
    finally:
        config.SERVER["timeout_s"] = normal
    return ret


@pytest.fixture(scope="module")
def hit_rates(retriever):
    """Share of questions per category whose gold chunks are all returned, for text and filters mode."""
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    rates = {}
    for mode in ("text", "filters"):
        hits = defaultdict(list)
        for q in (q for q in questions if q["gold"]):
            got, _ = run_question(retriever, q, mode, k=5)
            hits[q["category"]].append({retriever.owner[g] for g in q["gold"]} <= set(got))
        rates[mode] = {cat: sum(v) / len(v) for cat, v in hits.items()}
    return rates


@pytest.mark.parametrize("category", ["general", "zone", "cross_ref"])
def test_questions_with_the_location_in_the_text_are_answerable(hit_rates, category):
    assert hit_rates["text"][category] >= 0.95


@pytest.mark.parametrize("category", ["gis", "gis_multi"])
def test_plan_border_filters_return_the_right_records_and_sections(hit_rates, category):
    assert hit_rates["filters"][category] >= 0.95


def test_questions_the_plan_cannot_answer_are_graded_low(retriever):
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    bands = [retriever.search(q["question"]).band for q in questions if not q["gold"]]
    assert set(bands) == {"low"}
