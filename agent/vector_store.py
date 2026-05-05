"""Vector store — index jobs in ChromaDB for semantic similarity search.

Each job is embedded as a text document when it is stored.  ``find_similar``
queries the index and returns the closest matches as full job dicts (fetched
from SQLite by id), in the same shape that ``query_jobs`` returns.

The embedding function is injectable so tests can pass a ``FakeEmbeddingFunction``
instead of downloading the real sentence-transformer model.

Environment variables:
  CHROMA_DB_PATH — path to the ChromaDB persistence directory (default: db/chroma).
  JOBS_DB_PATH   — path to the SQLite database (shares the value from store_tool).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "jobs"

DEFAULT_CHROMA_PATH = Path(__file__).resolve().parents[1] / "db" / "chroma"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "jobs.db"

# Checked once at import time so the warning only fires when the module is loaded.
try:
    import chromadb  # noqa: F401
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False
    logger.warning(
        "chromadb is not installed — vector search unavailable. "
        "Run: pip install chromadb"
    )


# ---------------------------------------------------------------------------
# Path helpers (read env vars on every call so tests can monkeypatch them)
# ---------------------------------------------------------------------------

def _chroma_path() -> Path:
    return Path(os.environ.get("CHROMA_DB_PATH", DEFAULT_CHROMA_PATH))


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_document(job_id: int, job: dict) -> str:
    """Build the plain-text string that will be embedded for a job.

    Combines title, company, summary, and skills into one sentence so the
    embedding captures the full semantic meaning of the posting.
    """
    skills = job.get("skills") or []
    if isinstance(skills, str):
        # Already JSON-serialised (read back from the DB).
        try:
            skills = json.loads(skills)
        except Exception:
            skills = [skills]

    skills_str = ", ".join(str(s) for s in skills) if skills else ""

    parts: list[str] = [
        job.get("title") or "",
        f"at {job.get('company')}" if job.get("company") else "",
        job.get("summary") or "",
    ]
    if skills_str:
        parts.append(f"Skills: {skills_str}")

    return " — ".join(p for p in parts if p)


def _get_collection(embedding_fn=None):
    """Return (or create) the Chroma collection, optionally with a custom embedding function."""
    import chromadb as _chromadb

    path = _chroma_path()
    path.mkdir(parents=True, exist_ok=True)
    client = _chromadb.PersistentClient(path=str(path))

    kwargs: dict[str, Any] = {}
    if embedding_fn is not None:
        kwargs["embedding_function"] = embedding_fn

    return client.get_or_create_collection(_COLLECTION_NAME, **kwargs)


def _fetch_jobs_by_ids(ids: list[str]) -> list[dict]:
    """Fetch full job rows from SQLite for the given Chroma IDs, preserving similarity order."""
    if not ids:
        return []

    db = _db_path()
    if not db.exists():
        return []

    placeholders = ",".join("?" * len(ids))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT id, title, company, location, remote, skills, "
            f"salary, contact, summary, group_name, timestamp, seen, created_at "
            f"FROM jobs WHERE id IN ({placeholders})",
            [int(i) for i in ids],
        ).fetchall()
    finally:
        conn.close()

    # Chroma returns ids in similarity order; re-sort to match that order.
    id_to_row = {str(row["id"]): dict(row) for row in rows}
    ordered = [id_to_row[i] for i in ids if i in id_to_row]

    # Deserialise skills JSON strings back to lists.
    for job in ordered:
        if isinstance(job.get("skills"), str):
            try:
                job["skills"] = json.loads(job["skills"])
            except Exception:
                job["skills"] = []

    return ordered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reindex_all(*, embedding_fn=None) -> int:
    """Upsert every job in the SQLite database into the Chroma collection.

    Safe to run multiple times — Chroma upserts by id, so re-indexing is
    idempotent and never creates duplicates.  Jobs that are already in the
    index are simply refreshed.

    Args:
        embedding_fn: optional embedding function override (for tests).

    Returns:
        The number of jobs processed.
    """
    if not _CHROMA_AVAILABLE:
        logger.warning("chromadb is not installed — reindex_all() is a no-op.")
        return 0

    db = _db_path()
    if not db.exists():
        return 0

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, title, company, summary, skills FROM jobs"
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        job = dict(row)
        if isinstance(job.get("skills"), str):
            try:
                job["skills"] = json.loads(job["skills"])
            except Exception:
                job["skills"] = []
        index_job(job["id"], job, embedding_fn=embedding_fn)

    logger.info("reindex_all: indexed %d jobs", len(rows))
    return len(rows)


def index_job(job_id: int, job: dict, *, embedding_fn=None) -> None:
    """Embed a job and upsert it into the Chroma collection.

    Safe to call multiple times with the same job_id — Chroma upserts by id,
    so re-indexing never creates duplicates.

    Args:
        job_id: the SQLite row id of the stored job.
        job: the job dict (title, company, summary, skills, …).
        embedding_fn: optional Chroma-compatible embedding function override.
                      Pass a FakeEmbeddingFunction in tests to avoid downloading
                      the sentence-transformer model.
    """
    if not _CHROMA_AVAILABLE:
        return

    document = _make_document(job_id, job)
    collection = _get_collection(embedding_fn)
    collection.upsert(ids=[str(job_id)], documents=[document])


def is_near_duplicate(
    job: dict,
    distance_threshold: float = 0.3,
    *,
    embedding_fn=None,
) -> bool:
    """Return True if job is a near-duplicate of an already-indexed job.

    Builds the same document text as index_job, queries Chroma for the single
    closest neighbour, and compares the L2 distance to ``distance_threshold``.
    For unit-normalised embeddings (the default model) an L2 distance of 0.3
    corresponds to roughly 95 % cosine similarity.

    Returns False when the index is empty or chromadb is not installed, so the
    first job of any text is never suppressed.

    Args:
        job: the candidate job dict (title, company, summary, skills, …).
        distance_threshold: L2 distance below which the job is considered a
            duplicate.  Lower values require higher similarity.
        embedding_fn: optional embedding function override (same as index_job).
    """
    if not _CHROMA_AVAILABLE:
        return False

    # A title-only document (no company, summary, or skills) is too generic to
    # distinguish unique postings — "Full Stack Engineer" would falsely match any
    # other job with the same title.  Skip the check unless at least one of the
    # differentiating fields is present.
    has_content = bool(
        job.get("company") or job.get("summary") or job.get("skills")
    )
    if not has_content:
        return False

    collection = _get_collection(embedding_fn)
    if collection.count() == 0:
        return False

    text = _make_document(0, job)
    results = collection.query(
        query_texts=[text],
        n_results=1,
        include=["distances"],
    )
    distances: list[float] = results.get("distances", [[]])[0]
    if not distances:
        return False

    return distances[0] <= distance_threshold


def find_similar(text: str, n: int = 5, *, embedding_fn=None) -> list[dict]:
    """Return up to n jobs most semantically similar to text.

    Queries the Chroma collection for the nearest neighbours, then fetches
    the full rows from SQLite and returns them in similarity order.

    Args:
        text: the query string to compare against indexed jobs.
        n: how many results to return (capped at 10).
        embedding_fn: optional embedding function override (same as index_job).

    Returns:
        List of job dicts with the same keys as query_jobs(): id, title,
        company, location, remote, skills, salary, contact, summary,
        group_name, timestamp, seen, created_at.
        Returns [] if the index is empty or chromadb is not installed.
    """
    if not _CHROMA_AVAILABLE:
        return []

    n = max(1, min(n, 10))
    collection = _get_collection(embedding_fn)

    total = collection.count()
    if total == 0:
        return []

    results = collection.query(query_texts=[text], n_results=min(n, total))
    ids: list[str] = results.get("ids", [[]])[0]
    return _fetch_jobs_by_ids(ids)
