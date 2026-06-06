"""Retrieval over the ingested statute corpus.

Two access patterns:
  - semantic_search(query): top-k semantic retrieval for the Reviewer.
  - get_article(law, article): exact-text lookup for the Citation Verifier
    (Loop 4) so hallucinated citations can be rejected with the real text.
"""
from __future__ import annotations

from typing import Any, Optional

from app.corpus.store import get_collection
from app.llm import embed


def is_ingested() -> bool:
    try:
        return get_collection().count() > 0
    except Exception:
        return False


def semantic_search(query: str, top_k: int = 5, law: Optional[str] = None) -> list[dict[str, Any]]:
    collection = get_collection()
    if collection.count() == 0:
        return []
    query_vec = embed([query])[0]
    where = {"law_name": law} if law else None
    res = collection.query(query_embeddings=[query_vec], n_results=top_k, where=where)
    out: list[dict[str, Any]] = []
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({"text": doc, "metadata": meta, "distance": dist})
    return out


def get_article(law_short_name: str, article_number: str) -> Optional[dict[str, Any]]:
    collection = get_collection()
    try:
        res = collection.get(ids=[f"{law_short_name}:art-{article_number}"])
    except Exception:
        return None
    docs = res.get("documents", [])
    metas = res.get("metadatas", [])
    if not docs:
        return None
    return {"text": docs[0], "metadata": metas[0] if metas else {}}
