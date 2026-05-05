"""Tests for agent/vector_store.py.

All tests run offline — a FakeEmbeddingFunction is injected everywhere the real
sentence-transformer model would otherwise be downloaded.  A separate temp
directory is used for Chroma persistence so tests never touch db/chroma.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.vector_store import _make_document, find_similar, index_job, is_near_duplicate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _FakeEmbeddingFunction:
    """Deterministic stub that avoids downloading any ML model.

    Returns an 8-dimensional vector seeded by the character sum of each
    document, so texts with different content get meaningfully different vectors.

    Satisfies the chromadb EmbeddingFunction Protocol: __call__, name, is_legacy.
    """

    _DIM = 8

    @staticmethod
    def name() -> str:
        return "fake-embeddings"

    @staticmethod
    def is_legacy() -> bool:
        return False

    @staticmethod
    def default_space() -> str:
        return "l2"

    def embed_query(self, input):  # noqa: A002 — chromadb calls this for query texts
        return self(input)

    def __call__(self, input):  # noqa: A002 — matches chromadb EmbeddingFunction protocol
        result = []
        for text in input:
            seed = sum(ord(c) for c in text) % 100
            result.append([(seed + i) / 100.0 for i in range(self._DIM)])
        return result


@pytest.fixture()
def fake_ef():
    return _FakeEmbeddingFunction()



# ---------------------------------------------------------------------------
# _make_document
# ---------------------------------------------------------------------------

def test_make_document_includes_title_and_company():
    job = {"title": "Python Developer", "company": "Acme", "summary": "", "skills": []}
    doc = _make_document(1, job)
    assert "Python Developer" in doc
    assert "Acme" in doc


def test_make_document_includes_summary():
    job = {"title": "Dev", "company": None, "summary": "Build APIs", "skills": []}
    doc = _make_document(1, job)
    assert "Build APIs" in doc


def test_make_document_includes_skills_list():
    job = {"title": "Dev", "company": None, "summary": "", "skills": ["Python", "FastAPI"]}
    doc = _make_document(1, job)
    assert "Python" in doc
    assert "FastAPI" in doc


def test_make_document_handles_json_string_skills():
    """Skills stored in SQLite come back as a JSON string, not a list."""
    job = {"title": "Dev", "company": None, "summary": "", "skills": '["Go", "gRPC"]'}
    doc = _make_document(1, job)
    assert "Go" in doc
    assert "gRPC" in doc


def test_make_document_handles_missing_fields():
    doc = _make_document(99, {})
    # Should not raise; empty fields produce an empty-ish string.
    assert isinstance(doc, str)


# ---------------------------------------------------------------------------
# index_job
# ---------------------------------------------------------------------------

def test_index_job_adds_document(temp_chroma, fake_ef):
    job = {"title": "Backend Engineer", "company": "TechCorp", "summary": "Build REST APIs"}
    index_job(1, job, embedding_fn=fake_ef)

    # Verify Chroma actually stored the document.
    import chromadb
    client = chromadb.PersistentClient(path=str(temp_chroma))
    col = client.get_or_create_collection("jobs", embedding_function=fake_ef)
    assert col.count() == 1


def test_index_job_upsert_does_not_duplicate(temp_chroma, fake_ef):
    """Calling index_job twice with the same id should not create two entries."""
    job = {"title": "Dev", "company": "Corp", "summary": ""}
    index_job(1, job, embedding_fn=fake_ef)
    index_job(1, job, embedding_fn=fake_ef)

    import chromadb
    client = chromadb.PersistentClient(path=str(temp_chroma))
    col = client.get_or_create_collection("jobs", embedding_function=fake_ef)
    assert col.count() == 1


# ---------------------------------------------------------------------------
# find_similar
# ---------------------------------------------------------------------------

def test_find_similar_empty_store_returns_empty_list(temp_chroma, fake_ef):
    result = find_similar("Python developer", n=5, embedding_fn=fake_ef)
    assert result == []


def test_find_similar_returns_sqlite_rows(temp_db, temp_chroma, fake_ef):
    """After indexing a job, find_similar should return the full SQLite row."""
    job = {
        "title": "Python Backend Developer",
        "company": "Acme",
        "location": "Tel Aviv",
        "remote": False,
        "skills": ["Python", "Django"],
        "salary": None,
        "contact": "@acme",
        "summary": "Build scalable backend services.",
        "group": "tech-jobs",
        "timestamp": None,
    }

    # Insert into SQLite directly so we control the id.
    db_path = Path(temp_db)
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO jobs (title, company, location, remote, skills, salary, "
        "contact, summary, group_name, timestamp, seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        ("Python Backend Developer", "Acme", "Tel Aviv", False,
         json.dumps(["Python", "Django"]), None, "@acme",
         "Build scalable backend services.", "tech-jobs", None),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    index_job(row_id, job, embedding_fn=fake_ef)

    results = find_similar("Python backend", n=3, embedding_fn=fake_ef)
    assert len(results) == 1
    assert results[0]["title"] == "Python Backend Developer"
    assert results[0]["company"] == "Acme"


def test_find_similar_caps_results_at_n(temp_db, temp_chroma, fake_ef):
    """find_similar should return at most n results even if more are indexed."""
    db_path = Path(temp_db)
    conn = sqlite3.connect(db_path)

    for i in range(6):
        cur = conn.execute(
            "INSERT INTO jobs (title, company, summary, seen) VALUES (?, ?, ?, 0)",
            (f"Job {i}", "Corp", f"Description number {i}"),
        )
        index_job(cur.lastrowid, {"title": f"Job {i}", "summary": f"Description number {i}"}, embedding_fn=fake_ef)

    conn.commit()
    conn.close()

    results = find_similar("job description", n=3, embedding_fn=fake_ef)
    assert len(results) <= 3


def test_find_similar_skills_are_deserialized(temp_db, temp_chroma, fake_ef):
    """Skills should be returned as a list, not a raw JSON string."""
    db_path = Path(temp_db)
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO jobs (title, skills, seen) VALUES (?, ?, 0)",
        ("Dev", json.dumps(["Python", "FastAPI"])),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    index_job(row_id, {"title": "Dev", "skills": ["Python", "FastAPI"]}, embedding_fn=fake_ef)
    results = find_similar("Python developer", n=1, embedding_fn=fake_ef)

    assert isinstance(results[0]["skills"], list)
    assert "Python" in results[0]["skills"]


# ---------------------------------------------------------------------------
# store_tool integration
# ---------------------------------------------------------------------------

def test_store_job_calls_index_job(temp_db, temp_chroma):
    """store_job should call index_job after writing to SQLite."""
    from agent.tools.store_tool import store_job

    job = {
        "title": "Data Engineer",
        "company": "Pipes Inc",
        "summary": "ETL pipelines",
        "skills": ["Spark", "Airflow"],
        "group": "data-jobs",
    }

    # _try_index_job does a local `from agent.vector_store import index_job`,
    # so patching the attribute on the source module is the correct target.
    with patch("agent.vector_store.index_job") as mock_index:
        row_id = store_job(job)

    mock_index.assert_called_once()
    call_args = mock_index.call_args
    assert call_args[0][0] == row_id          # first positional arg is job_id
    assert call_args[0][1]["title"] == "Data Engineer"


def test_store_job_succeeds_even_if_index_fails(temp_db, temp_chroma):
    """A vector-store failure must not propagate to the caller."""
    from agent.tools.store_tool import store_job

    job = {"title": "QA Engineer", "company": "TestCo", "summary": ""}

    with patch("agent.vector_store.index_job", side_effect=RuntimeError("chroma down")):
        row_id = store_job(job)

    assert isinstance(row_id, int)
    assert row_id > 0


# ---------------------------------------------------------------------------
# is_near_duplicate
# ---------------------------------------------------------------------------

def test_is_near_duplicate_empty_store_returns_false(temp_chroma, fake_ef):
    """When the index is empty, nothing can be a duplicate."""
    job = {"title": "Python Developer", "company": "Acme", "summary": "Build APIs"}
    result = is_near_duplicate(job, embedding_fn=fake_ef)
    assert result is False


def test_is_near_duplicate_identical_text_returns_true(temp_db, temp_chroma, fake_ef):
    """A job identical to an indexed one should be flagged as a near-duplicate."""
    job = {"title": "Backend Engineer", "company": "TechCorp", "summary": "REST APIs"}

    # Insert into SQLite and index it.
    db_path = Path(temp_db)
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO jobs (title, company, summary, seen) VALUES (?, ?, ?, 0)",
        (job["title"], job["company"], job["summary"]),
    )
    index_job(cur.lastrowid, job, embedding_fn=fake_ef)
    conn.commit()
    conn.close()

    # The FakeEmbeddingFunction produces distance=0 for identical text, so any
    # positive threshold should match.
    result = is_near_duplicate(job, distance_threshold=0.3, embedding_fn=fake_ef)
    assert result is True


def test_is_near_duplicate_different_job_returns_false(temp_db, temp_chroma, fake_ef):
    """A job with sufficiently different text should not be flagged."""
    indexed_job = {"title": "Backend Engineer", "company": "TechCorp", "summary": "REST APIs"}

    db_path = Path(temp_db)
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO jobs (title, company, summary, seen) VALUES (?, ?, ?, 0)",
        (indexed_job["title"], indexed_job["company"], indexed_job["summary"]),
    )
    index_job(cur.lastrowid, indexed_job, embedding_fn=fake_ef)
    conn.commit()
    conn.close()

    # distance_threshold=0.0 means only a distance of exactly 0 (identical text)
    # counts as a duplicate, so any different text is safe.
    different_job = {"title": "Frontend Developer", "company": "WebCo", "summary": "React UI"}
    result = is_near_duplicate(different_job, distance_threshold=0.0, embedding_fn=fake_ef)
    assert result is False


def test_is_near_duplicate_error_is_non_fatal(temp_db, temp_chroma):
    """A Chroma error during duplicate check must not surface to the caller."""
    from agent.tools.store_tool import store_job

    job = {"title": "DevOps Engineer", "company": "Ops Inc", "summary": "CI/CD"}

    with patch("agent.vector_store.is_near_duplicate", side_effect=RuntimeError("chroma down")):
        row_id = store_job(job)

    # Error in the dedup check: job is stored normally.
    assert isinstance(row_id, int)
    assert row_id > 0


def test_store_job_skips_near_duplicate(temp_db, temp_chroma):
    """store_job returns None and skips the INSERT when a near-duplicate is detected."""
    from agent.tools.store_tool import store_job

    job = {"title": "ML Engineer", "company": "AI Co", "summary": "Train models"}

    with patch("agent.vector_store.is_near_duplicate", return_value=True):
        result = store_job(job)

    assert result is None
