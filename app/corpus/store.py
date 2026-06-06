"""Single source for the ChromaDB client and collection handle.

Both ingestion and retrieval go through here so there is exactly one
PersistentClient per process (Chroma requirement) and collection handles are
always fetched fresh by name — avoiding stale handles after a reset/re-ingest.
"""
from __future__ import annotations

import json

import chromadb

from app import config

_client = None


def get_client():
    global _client
    if _client is None:
        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    return _client


def collection_name() -> str:
    try:
        with open(config.CORPUS_CONFIG, "r", encoding="utf-8") as fh:
            return json.load(fh).get("collection", "uae_statutes")
    except Exception:
        return "uae_statutes"


def get_collection(reset: bool = False):
    client = get_client()
    name = collection_name()
    if reset:
        try:
            client.delete_collection(name)
        except Exception:
            pass
    return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
