"""Generic, config-driven corpus ingestion.

Design goal (SOW): adding a fifth statute must be a config + data drop, not a
code change. All statute-specific details live in ``corpus_config.json``:
the file name and the regex that marks the start of each article. This module
contains no hardcoded statute knowledge.

Run:  python -m app.corpus.ingest
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import config
from app.corpus.store import get_collection
from app.llm import embed


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or config.CORPUS_CONFIG
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _split_articles(text: str, pattern: str) -> list[tuple[str, str]]:
    """Split a statute into (article_number, article_text) by a start-of-article regex.

    The regex must contain one capture group for the article number.
    """
    regex = re.compile(pattern, re.MULTILINE)
    matches = list(regex.finditer(text))
    chunks: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        number = match.group(1)
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chunks.append((number, body))
    return chunks


def ingest(config_path: Path | None = None, *, reset: bool = True) -> dict[str, Any]:
    cfg = load_config(config_path)
    corpus_dir = config.CORPUS_CONFIG.parent
    # We supply our own embeddings via the shared wrapper, so no embedding fn here.
    collection = get_collection(reset=reset)

    total = 0
    per_law: dict[str, int] = {}
    for statute in cfg["statutes"]:
        law_name = statute["law_name"]
        short_name = statute.get("short_name", law_name)
        file_path = corpus_dir / statute["file"]
        if not file_path.exists():
            print(f"[ingest] WARNING: missing source file {file_path} — skipping {law_name}")
            continue

        text = file_path.read_text(encoding="utf-8")
        articles = _split_articles(text, statute["article_pattern"])
        if not articles:
            print(f"[ingest] WARNING: no articles matched in {file_path}")
            continue

        ids, documents, metadatas = [], [], []
        for number, body in articles:
            ids.append(f"{short_name}:art-{number}")
            documents.append(body)
            metadatas.append(
                {
                    "law_name": law_name,
                    "short_name": short_name,
                    "article_number": number,
                    "source_file": statute["file"],
                }
            )

        embeddings = embed(documents)
        collection.add(
            ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas
        )
        per_law[law_name] = len(ids)
        total += len(ids)
        print(f"[ingest] {law_name}: {len(ids)} articles")

    summary = {"collection": cfg.get("collection", "uae_statutes"), "total_chunks": total, "per_law": per_law}
    print(f"[ingest] done: {summary}")
    return summary


if __name__ == "__main__":
    ingest()
