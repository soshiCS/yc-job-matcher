"""SQLite persistence for evaluated jobs.

Only software-engineering jobs that the LLM actually evaluated are stored. Each job
is keyed by its YC job id and upserted, so re-running never creates duplicates — a
job seen again just has its evaluation refreshed (its `first_seen_at` is preserved).

Stored columns include everything we hand the LLM plus queryable fields (years of
experience, location, remote, interview probability, …) with indexes for fast
filtering.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from .config import settings
from .models import RankedJob

_JOB_ID_RE = re.compile(r"/jobs/(\d+)")

# Column order is the single source of truth for the upsert.
COLUMNS = [
    "job_id",
    "url",
    "title",
    "company",
    "location",
    "country",
    "remote",
    "min_experience_label",
    "min_experience_years",
    "salary",
    "equity",
    "skills",  # JSON array
    "summary",
    "raw_text",  # full text handed to the LLM
    "source",
    "is_software_engineering",
    "interview_probability",
    "confidence",
    "fit_summary",
    "strengths",  # JSON array
    "gaps",  # JSON array
    "reasoning",  # JSON array
    "should_apply",
    "resume_hash",
    "first_seen_at",
    "last_evaluated_at",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    url TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    country TEXT,
    remote TEXT,
    min_experience_label TEXT,
    min_experience_years INTEGER,
    salary TEXT,
    equity TEXT,
    skills TEXT,
    summary TEXT,
    raw_text TEXT,
    source TEXT,
    is_software_engineering INTEGER,
    interview_probability REAL,
    confidence REAL,
    fit_summary TEXT,
    strengths TEXT,
    gaps TEXT,
    reasoning TEXT,
    should_apply INTEGER,
    resume_hash TEXT,
    first_seen_at TEXT,
    last_evaluated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_experience ON jobs(min_experience_years);
CREATE INDEX IF NOT EXISTS idx_jobs_remote ON jobs(remote);
CREATE INDEX IF NOT EXISTS idx_jobs_probability ON jobs(interview_probability);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country);
"""

# Everything except the key and first_seen_at is refreshed on conflict.
_UPDATE_COLUMNS = [c for c in COLUMNS if c not in ("job_id", "first_seen_at")]
_UPSERT_SQL = (
    f"INSERT INTO jobs ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in COLUMNS)}) "
    f"ON CONFLICT(job_id) DO UPDATE SET "
    f"{', '.join(f'{c}=excluded.{c}' for c in _UPDATE_COLUMNS)}"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(_SCHEMA)
    return conn


def _job_id(url: str) -> str | None:
    match = _JOB_ID_RE.search(url or "")
    return match.group(1) if match else (url or None)


def _row(ranked: RankedJob, resume_hash: str, now: str) -> dict | None:
    job, ev = ranked.job, ranked.llm_evaluation
    job_id = _job_id(job.url)
    if job_id is None or ev is None:
        return None
    return {
        "job_id": job_id,
        "url": job.url,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "country": job.country,
        "remote": job.remote,
        "min_experience_label": job.min_experience,
        "min_experience_years": job.min_experience_years,
        "salary": job.salary,
        "equity": job.equity,
        "skills": json.dumps(job.tags),
        "summary": job.summary,
        "raw_text": job.raw_text,
        "source": job.source,
        "is_software_engineering": 1 if ev.is_software_engineering else 0,
        "interview_probability": ev.interview_probability,
        "confidence": ev.confidence,
        "fit_summary": ev.fit_summary,
        "strengths": json.dumps(ev.strengths),
        "gaps": json.dumps(ev.gaps),
        "reasoning": json.dumps(ev.reasoning),
        "should_apply": 1 if ev.should_apply else 0,
        "resume_hash": resume_hash,
        "first_seen_at": now,
        "last_evaluated_at": now,
    }


def save_evaluated_jobs(ranked_jobs: list[RankedJob], resume_hash: str) -> tuple[int, int]:
    """Upsert evaluated software-engineering jobs. Returns (saved, newly_inserted)."""
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        r
        for ranked in ranked_jobs
        if ranked.llm_evaluation and ranked.llm_evaluation.is_software_engineering
        for r in [_row(ranked, resume_hash, now)]
        if r is not None
    ]
    if not rows:
        return (0, 0)

    conn = _connect()
    try:
        existing = {row[0] for row in conn.execute("SELECT job_id FROM jobs")}
        new = sum(1 for r in rows if r["job_id"] not in existing)
        conn.executemany(
            _UPSERT_SQL, [tuple(r[c] for c in COLUMNS) for r in rows]
        )
        conn.commit()
        return (len(rows), new)
    finally:
        conn.close()
