# Module 3 Journal

## Week 7 — Issue selection

**Issue link:** https://github.com/ascherj/pathreview/issues/6

**Issue title:** Duplicate embeddings generated when re-ingesting the same repository

**Tier:** [ ] Tier 1  [x] Tier 2  [ ] Tier 3

**Problem summary:**
PathReview's ingestion pipeline (`ingestion/pipeline.py`) is supposed to skip sources it has already processed, but both halves of its idempotency mechanism are stubs: `_record_ingested_source()` only writes a log line and never persists an `IngestedSource` row, and `_check_skip()` queries the ORM with the literal string `"IngestedSource"`, filters on a `source_id` column that doesn't even exist on the model, and silently swallows the resulting exception — so the skip check never skips. As a result, every re-upload of the same resume, README, or repo is re-parsed and re-embedded (paying the embedding provider again) and its vectors are re-written into ChromaDB; for repo metadata the dedup hash is computed over volatile fields like star counts, so "the same repo" always lands under new vector IDs and retrieval returns near-identical chunks with inflated scores. A successful fix makes ingestion idempotent: implement real bookkeeping on the `IngestedSource` model (`core/models/ingested_source.py`, plus an Alembic migration for the dedup key), wire `_check_skip()` and `_record_ingested_source()` to it, and hash only stable content — so re-ingesting an unchanged source becomes a recorded no-op with zero new embeddings.

**Branch name:** `fix/6-duplicate-embeddings-reingest`

**Setup confirmation:** [x] App runs locally at localhost:5173

**Cohort ledger:** [x] Issue added to cohort ledger

### Selection notes — working the "Is this issue right for me?" checklist

**Part 1 — Understanding the issue.** Paraphrase without looking: the pipeline never remembers what it has ingested, so re-ingesting anything duplicates vectors in the store and re-bills embeddings; the fix is to make ingestion idempotent via the `IngestedSource` table. The labels (`ingestion`, `bug`, `tier-2`) match what I found: the change spans `ingestion/pipeline.py`, `core/models/ingested_source.py`, an Alembic migration, and the batch-processor write path into ChromaDB. I located and read the referenced code and the bug is real — and actually deeper than the title says (details below). Done looks like a concrete before/after: today, ingesting the same README twice returns `skipped=False` twice and doubles the vector count; after the fix, the second call returns `skipped=True` with `skip_reason="Source already ingested"`, the vector count is unchanged, and exactly one `ingested_sources` row exists.

**Part 2 — Tier fit.** Tier 2 is right for me: I've contributed to large multi-module codebases before, and this issue requires understanding how the ingestion pipeline, the SQLAlchemy model layer, the migration history, and the vector-store writes interact — but nothing beyond that. I passed on Tier 1 (I want more than a one-file fix out of this module) and deliberately avoided Tier 3 candidates like #27 (stale embeddings across re-ingest) whose fixes sprawl into the RAG retrieval path, because a scope surprise in Week 9 has no safety net.

**Part 3 — Codebase readiness.** I read the specific functions, not just the files. `_check_skip()` (`ingestion/pipeline.py:283`) passes the *string* `"IngestedSource"` to `db_session.query()` inside a catch-all `except` that logs a warning and proceeds — this can never work, and it hides its own failure. `_record_ingested_source()` (`ingestion/pipeline.py:326`) says outright "This is a placeholder for actual database recording." The model in `core/models/ingested_source.py` has `content_hash` but no `source_id` column, so the intended query is incompatible with the schema as written — meaning the fix needs a small schema decision (unique `source_id` column vs. unique constraint on `profile_id` + `source_type` + `content_hash`), which is exactly the kind of design note I want in my PR. Repo ingestion builds its hash from `str(repo_data)` (`ingestion/pipeline.py:217`), which includes volatile fields — that's the "duplicate embeddings for the same repo" mechanism in the issue title. I read `tests/unit/test_batch_processor.py` end-to-end to learn the house patterns (mock embedding provider, mock vector DB, `@pytest.mark.unit`), and my rough plan needs no further lookup: add the dedup key + migration, implement record/check for real, normalize the repo hash over stable fields, and test that a second ingest is a no-op. One scoping note recorded up front: `review_service` currently uses its own placeholder ingestion, so nothing in the HTTP path calls `IngestionPipeline` yet — my fix and demo drive the pipeline directly, and wiring the service layer to it stays out of scope.

**Part 4 — Scope and time.** The issue lists no blockers and depends on no other open issue; because the pipeline has no production caller yet, the regression risk to other flows is low. Two other students have claimed it in the comments — claims are non-exclusive in this course and it's still among the least-crowded viable issues on the tracker (most Tier 1s have 5–14 claims), so I'm fine sharing it. Effort-wise the issue says 4–6 hours; I estimate ~6–8 including the migration, hash normalization, and tests, which fits the Week 8–9 window comfortably alongside my other commitments.

**Setup notes.** Environment note for anyone else on Apple Silicon without Docker Desktop: I ran the compose stack under Podman. The `chromadb/chroma:0.4.22` arm64 image crashes on boot (`AttributeError: np.float_ was removed in NumPy 2.0`) — I patched `chromadb/api/types.py` inside the image (`np.float_` → `np.float64`) and re-committed it under the same tag rather than touching `docker-compose.yml`. After that, `make setup` + `make run` work as documented and the app loads at localhost:5173.
