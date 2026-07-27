"""Reproduction for issue #6 — duplicate embeddings on re-ingest.

Drives the real IngestionPipeline with the app's own components:
- rag.retriever.vector_store.VectorStore (ChromaDB persistent client)
- ingestion.embeddings.provider.MockEmbeddingProvider (deterministic, offline)
- core.database.AsyncSessionLocal (the app's real session factory)

Run from the repo root:
    .venv/bin/python scripts/repro_issue_6.py

Requires the docker-compose Postgres to be up (only for the session object;
no rows are read or written — that's part of the bug).
"""

import shutil

from core.database import AsyncSessionLocal
from ingestion.embeddings.provider import MockEmbeddingProvider
from ingestion.pipeline import IngestionPipeline
from rag.retriever.vector_store import VectorStore

CHROMA_DIR = "/tmp/repro6_chroma"
PROFILE = "11111111-1111-1111-1111-111111111111"

README = """# TaskTracker

A todo app built with FastAPI and React.

## Features

- Create, edit, and complete tasks
- Tag-based filtering and search
- Offline-first sync

## Setup

Run `make dev` and open localhost:3000.
"""


class CountingProvider(MockEmbeddingProvider):
    """Counts embed() calls to expose re-billing on re-ingest."""

    def __init__(self) -> None:
        self.calls = 0
        self.texts_embedded = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.texts_embedded += len(texts)
        return super().embed(texts)


def github_fetch(stars: int, pushed_at: str) -> dict:
    """Simulate the GitHub API response for the SAME repo at two points in time."""
    return {
        "name": "tasktracker",
        "description": "A todo app built with FastAPI and React",
        "language": "Python",
        "languages": {"Python": 6000, "TypeScript": 4000},
        "topics": ["fastapi", "react", "productivity"],
        "stargazers_count": stars,  # volatile
        "pushed_at": pushed_at,  # volatile
    }


def main() -> None:
    shutil.rmtree(CHROMA_DIR, ignore_errors=True)
    store = VectorStore(persist_dir=CHROMA_DIR)
    collection = store.get_collection("portfolio")
    provider = CountingProvider()
    session = AsyncSessionLocal()  # the app's real session object
    pipeline = IngestionPipeline(
        vector_db=collection, db_session=session, embedding_provider=provider
    )

    print("=" * 72)
    print("PART A — re-ingest the SAME README (byte-identical content)")
    print("=" * 72)
    r1 = pipeline.ingest_readme(PROFILE, "tasktracker", README)
    print(f"\n  1st ingest: skipped={r1.skipped}  chunk_count={r1.chunk_count}")
    calls_before = provider.calls
    r2 = pipeline.ingest_readme(PROFILE, "tasktracker", README)
    print(f"  2nd ingest: skipped={r2.skipped}  chunk_count={r2.chunk_count}")
    print(
        f"  -> expected skipped=True on 2nd call; embedding provider was "
        f"called {provider.calls - calls_before} more time(s) (re-billed)"
    )
    print(
        f"  -> vector count in Chroma: {collection.count()} "
        f"(flat only because chunk IDs collide and Chroma drops them)"
    )

    print()
    print("=" * 72)
    print("PART B — re-ingest the SAME REPO after a routine metadata re-fetch")
    print("        (star count 41 -> 42; nothing about the code changed)")
    print("=" * 72)
    b1 = pipeline.ingest_repo_metadata(PROFILE, github_fetch(41, "2026-07-14T10:00:00Z"))
    count_after_first = collection.count()
    b2 = pipeline.ingest_repo_metadata(PROFILE, github_fetch(42, "2026-07-21T09:00:00Z"))
    count_after_second = collection.count()
    print(f"\n  1st ingest: skipped={b1.skipped}  source_id={b1.source_id}")
    print(f"  2nd ingest: skipped={b2.skipped}  source_id={b2.source_id}")
    print("  -> same repo, two different source_ids (hash covers volatile fields)")
    print(
        f"  -> vector count after 1st: {count_after_first}, after 2nd: "
        f"{count_after_second}  (DUPLICATED)"
    )

    print()
    print("=" * 72)
    print("PART C — what retrieval now sees for a query about this repo")
    print("=" * 72)
    query_emb = MockEmbeddingProvider().embed(["python fastapi todo project"])[0]
    hits = store.query(query_emb, "portfolio", n_results=6)
    for h in hits:
        sid = h["metadata"].get("source_id", "?")
        print(f"  score={h['score']:.4f}  source={sid[:44]:44s}  text={h['text'][:40]!r}")
    repo_hits = [h for h in hits if h["metadata"].get("source_type") == "repo"]
    distinct_repo_sources = {h["metadata"]["source_id"] for h in repo_hits}
    print(
        f"\n  -> the single repo 'tasktracker' occupies {len(repo_hits)} of the "
        f"top {len(hits)} hits under {len(distinct_repo_sources)} different "
        f"source_ids (near-identical text, duplicated)"
    )

    print()
    print("SUMMARY")
    print(
        f"  embed() batches billed : {provider.calls} "
        f"(texts embedded: {provider.texts_embedded})"
    )
    print(f"  chroma vectors stored  : {collection.count()}")
    print(
        '  ingested_sources rows  : psql -c "select count(*) from '
        'ingested_sources;" -> 0 (recording is a placeholder)'
    )


if __name__ == "__main__":
    main()
