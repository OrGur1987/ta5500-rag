"""A tiny synthetic plan index for tests of the retrieval module and the service."""
import numpy as np
import pandas as pd

from rag import config, embed, retrieval
from rag.retrieval import Retriever

HOTEL, MIXED, NEW_HUB = "3.7.2", "3.4.1", "3.3.5"


def ref(target, mandatory):
    return {"raw": f"סעיף {target}", "target": target, "status": "internal", "mandatory": mandatory}


def chunk(cid, nodes, text, kind="clause", area=None, area_name=None, zone=None, section=None, refs=()):
    return {"chunk_id": cid, "node_ids": nodes, "section_id": section or nodes[0], "kind": kind, "binding": True,
            "heading_path": ["פרק 3 – אזורי יעוד"], "text": text, "embed_text": text, "tokens": 50,
            "refs_out": list(refs), "page_start": 30, "page_end": 30,
            "district": "רובע 7" if area else None, "area": area, "area_name": area_name, "zone": zone}


CHUNKS = [
    chunk("3.3.5", ["3.3.5", "3.3.5(א)"], "3.3.5. מוקד תעסוקה חדש – הוראות", section="3.3.5",
          refs=[ref("3.1.2", True), ref("4.4.2", False), ref("5.3.2", False)]),
    chunk("3.1.2", ["3.1.2", "3.1.2(א)"], "3.1.2. חישוב שטחי בנייה", section="3.1.2"),
    chunk("3.1.2[2]", ["3.1.2[2]"], "שורה בטבלת החישוב", kind="record", section="3.1.2"),
    chunk("4", ["4"], "פרק 4 – הוראות כלליות להכנת תכניות", kind="chapter"),
    chunk("4.2", ["4.2"], "4.2. דיור בהישג יד – הוראות", kind="section"),
    chunk("3.3", ["3.3", "3.3.1"], "3.3. אזורי תעסוקה\n3.3.1. כללי"),
    chunk("5.4", ["5.4", "5.4(1)"], "5.4. טבלה\n(1) הערה לטבלה", kind="section"),
    chunk("3.4", ["3.4", "3.4.1"], "3.4.1. אזור מעורב"),
    chunk("3.7.2", ["3.7.2"], "3.7.2. אזור מוטה מלונאות"),
    chunk("3.10", ["3.10", "3.10(1)", "3.11"], "3.10. אזור פעילות מיוחד\n3.11. אזור לתכנון בעתיד"),
    chunk("3.8", ["3.8", "3.8.1"], "3.8. תחבורה וניידות\n3.8.1. כללי"),
    chunk("3.8.2", ["3.8.2"], "3.8.2. דרכים"),
    chunk("3.1.2(ב)", ["3.1.2(ב)", "3.1.2(ג)"], "(ב) סעיף קטן\n(ג) סעיף קטן נוסף"),
    chunk("4.4.2", ["4.4.2"], "4.4.2. שימור"),
    chunk("5.3.2", ["5.3.2"], "5.3.2. התחדשות עירונית"),
    chunk("5.4[1]", ["5.4[1]"], "[רובע 7 > 703 מבואות יפו > מוקד תעסוקה חדש]", kind="record",
          area="703", area_name="מבואות יפו", zone="מוקד תעסוקה חדש", section="5.4"),
    chunk("5.4[2]", ["5.4[2]"], "[רובע 7 > 703 מבואות יפו > 703 ה' אזור מעורב]", kind="record",
          area="703", area_name="מבואות יפו", zone="703 ה' אזור מעורב", section="5.4"),
    chunk("5.4[3]", ["5.4[3]"], "[רובע 5 > 502 כרם התימנים > אזור מוטה מלונאות]", kind="record",
          area="502", area_name="כרם התימנים", zone="אזור מוטה מלונאות", section="5.4"),
    chunk("5.4[4]", ["5.4[4]"], "[רובע 7 > 703 מבואות יפו > ר-83 רחוב בן צבי]", kind="record",
          area="703", area_name="מבואות יפו", zone="ר-83 רחוב בן צבי", section="5.4"),
    chunk("5.4[5]", ["5.4[5]"], "רח' בן צבי", kind="record",
          area="502", area_name="כרם התימנים", zone="רח' בן צבי", section="5.4"),
    chunk("5.4[6]", ["5.4[6]"], "[רובע 7 > 703 מבואות יפו > מרקם בנוי לשימור]", kind="record",
          area="703", area_name="מבואות יפו", zone="מרקם בנוי לשימור", section="5.4"),
    chunk("5.4[7]", ["5.4[7]"], "[רובע 5 > 502 כרם התימנים > 502 ב' מרקם בנוי לשימור]", kind="record",
          area="502", area_name="כרם התימנים", zone="502 ב' מרקם בנוי לשימור", section="5.4"),
    chunk("5.4[8]", ["5.4[8]"], "[רובע 7 > 703 מבואות יפו > 703 ו' מרקם חדש]", kind="record",
          area="703", area_name="מבואות יפו", zone="703 ו' מרקם חדש", section="5.4"),
]
NODES = pd.DataFrame({
    "id": [NEW_HUB, MIXED, HOTEL, "3.6.2", "3.3", "3.3.1", "3.4", "3.4.2", "3.7", "3.10", "3.11", "4", "4.2", "3.8", "3.8.1", "3.8.2"],
    "kind": ["subsection"] * 4 + ["section", "subsection", "section", "subsection", "section", "section", "section", "chapter", "section",
                                "section", "subsection", "subsection"],
    "children": [[]] * 11 + [["4.2"], [], [], [], []],
    "text": [""] * 12 + ["הוראות דיור", "", "", ""],
    "title": ["מוקד תעסוקה חדש", "אזור מעורב", "אזור מוטה מלונאות", "ים", "אזורי תעסוקה", "כללי", "עירוב שימושים", "ציר מעורב",
              "מלונאות", "אזור פעילות מיוחד", "אזור לתכנון בעתיד", "הוראות כלליות להכנת תכניות", "דיור בהישג יד",
              "תחבורה וניידות", "כללי", "דרכים"]})


def make(tmp_path, monkeypatch, weights, **settings):
    """Index whose chunks are orthogonal unit vectors, so a query's similarity to a chunk is its weight."""
    df = pd.DataFrame(CHUNKS)
    df.to_parquet(tmp_path / "chunks.parquet")
    NODES.to_parquet(tmp_path / "nodes.parquet")
    eye = np.eye(len(df) + 1, dtype=np.float32)  # the extra axis is a direction no chunk has
    config.write_parquet(
        pd.DataFrame({"chunk_id": df["chunk_id"], "text_hash": [embed.text_hash(t) for t in df["embed_text"]],
                      "embedding": list(eye[:len(df)])}),
        tmp_path / f"embeddings_{config.model()['name']}.parquet", {"doc_prefix": ""})
    query = np.zeros(len(df) + 1, dtype=np.float32)
    for cid, w in weights.items():
        query[len(df) if cid is None else list(df["chunk_id"]).index(cid)] = w
    query /= np.linalg.norm(query)
    monkeypatch.setattr(retrieval, "embed_query", lambda text, model: query)
    monkeypatch.setattr(retrieval, "embed", lambda texts, model: np.zeros((len(texts), len(df) + 1), dtype=np.float32))  # row names link to nothing
    for key, value in {"clause_k": 1, **settings}.items():
        monkeypatch.setitem(config.RETRIEVAL, key, value)
    return Retriever(tmp_path)
