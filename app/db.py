"""SQLite job index with a growing canonical skill vocabulary.

The database is built by the "index" flow (scrape + standardized profile per job)
and queried by the "match" flow (shortlist by skill overlap before any LLM scoring).

Tables:
  jobs        — one row per YC job (deduped by job id), with its standardized profile
  skills      — the living canonical skill vocabulary (name is unique, case-insensitive)
  job_skills  — which canonical skills each job has (for fast overlap queries)

Only software-engineering jobs are stored.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from .config import settings
from .models import JobPosting, JobProfile

_JOB_ID_RE = re.compile(r"/jobs/(\d+)")

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
    summary TEXT,
    raw_text TEXT,
    source TEXT,
    role_category TEXT,
    seniority TEXT,
    is_software_engineering INTEGER,
    first_seen_at TEXT,
    last_profiled_at TEXT
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE COLLATE NOCASE,
    category TEXT
);
CREATE TABLE IF NOT EXISTS job_skills (
    job_id TEXT,
    skill_id INTEGER,
    weight REAL DEFAULT 1.0,
    PRIMARY KEY (job_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_role ON jobs(role_category);
CREATE INDEX IF NOT EXISTS idx_jobs_experience ON jobs(min_experience_years);
CREATE INDEX IF NOT EXISTS idx_jobs_remote ON jobs(remote);
CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country);
CREATE INDEX IF NOT EXISTS idx_job_skills_skill ON job_skills(skill_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Migration: add job_skills.weight to databases created before importance weighting.
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(job_skills)")]
    if "weight" not in cols:
        conn.execute("ALTER TABLE job_skills ADD COLUMN weight REAL DEFAULT 1.0")
        conn.commit()
    return conn


_IMPORTANCE_WEIGHT = {"core": 1.0, "secondary": 0.4}
_ROLE_BOOST = 1.6  # multiplier when a job's role_category matches the résumé's


def _job_id(url: str) -> str | None:
    match = _JOB_ID_RE.search(url or "")
    return match.group(1) if match else (url or None)


def get_all_skill_names() -> list[str]:
    """The current canonical vocabulary, fed to the LLM so it reuses existing names."""
    conn = _connect()
    try:
        return [row["name"] for row in conn.execute("SELECT name FROM skills ORDER BY name")]
    finally:
        conn.close()


def counts() -> tuple[int, int]:
    conn = _connect()
    try:
        jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        skills = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        return jobs, skills
    finally:
        conn.close()


def _get_or_create_skill_id(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    row = conn.execute("SELECT id FROM skills WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO skills (name, category) VALUES (?, 'skill')", (name,))
    return cur.lastrowid


def index_jobs(pairs: list[tuple[JobPosting, JobProfile]]) -> tuple[int, int]:
    """Upsert software-engineering jobs + their canonical skills. Returns (indexed, new)."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        existing = {row["job_id"] for row in conn.execute("SELECT job_id FROM jobs")}
        indexed = 0
        new = 0
        for job, profile in pairs:
            if not profile.is_software_engineering:
                continue
            jid = _job_id(job.url)
            if not jid:
                continue
            is_new = jid not in existing
            conn.execute(
                """
                INSERT INTO jobs (job_id, url, title, company, location, country, remote,
                    min_experience_label, min_experience_years, salary, equity, summary,
                    raw_text, source, role_category, seniority, is_software_engineering,
                    first_seen_at, last_profiled_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    url=excluded.url, title=excluded.title, company=excluded.company,
                    location=excluded.location, country=excluded.country, remote=excluded.remote,
                    min_experience_label=excluded.min_experience_label,
                    min_experience_years=excluded.min_experience_years, salary=excluded.salary,
                    equity=excluded.equity, summary=excluded.summary, raw_text=excluded.raw_text,
                    source=excluded.source, role_category=excluded.role_category,
                    seniority=excluded.seniority, is_software_engineering=1,
                    last_profiled_at=excluded.last_profiled_at
                """,
                (
                    jid, job.url, job.title, job.company, job.location, job.country, job.remote,
                    job.min_experience, job.min_experience_years, job.salary, job.equity,
                    job.summary, job.raw_text, job.source, profile.role_category,
                    profile.seniority, now, now,
                ),
            )
            # Replace this job's skill links with the freshly profiled set, keeping
            # the strongest weight if the same skill appears more than once.
            conn.execute("DELETE FROM job_skills WHERE job_id = ?", (jid,))
            weights: dict[int, float] = {}
            for tag in profile.skills:
                name = tag.name.strip()
                if not name:
                    continue
                sid = _get_or_create_skill_id(conn, name)
                weight = _IMPORTANCE_WEIGHT.get(tag.importance, 1.0)
                weights[sid] = max(weights.get(sid, 0.0), weight)
            for sid, weight in weights.items():
                conn.execute(
                    "INSERT OR IGNORE INTO job_skills (job_id, skill_id, weight) VALUES (?, ?, ?)",
                    (jid, sid, weight),
                )
            indexed += 1
            new += 1 if is_new else 0
        conn.commit()
        return indexed, new
    finally:
        conn.close()


def shortlist(
    skill_names: list[str],
    limit: int,
    role_categories: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Rank stored SWE jobs by a weighted skill-overlap score. (job_id, score).

    Score = sum over shared skills of (job's importance weight x skill IDF), where
    rarer skills (in fewer jobs) weigh more. Jobs whose role_category matches the
    résumé's get a boost.
    """
    import math

    conn = _connect()
    try:
        if not skill_names:
            return []
        placeholders = ",".join("?" for _ in skill_names)
        skill_ids = [
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM skills WHERE name IN ({placeholders}) COLLATE NOCASE",
                skill_names,
            )
        ]
        if not skill_ids:
            return []

        total_swe = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE is_software_engineering = 1"
        ).fetchone()[0] or 1

        id_ph = ",".join("?" for _ in skill_ids)
        # How many SWE jobs each résumé-skill appears in → IDF (rarer = higher).
        freq = {
            r["skill_id"]: r["n"]
            for r in conn.execute(
                f"""
                SELECT js.skill_id AS skill_id, COUNT(DISTINCT js.job_id) AS n
                FROM job_skills js JOIN jobs j ON j.job_id = js.job_id
                WHERE js.skill_id IN ({id_ph}) AND j.is_software_engineering = 1
                GROUP BY js.skill_id
                """,
                skill_ids,
            )
        }
        idf = {sid: math.log((total_swe + 1) / (1 + n)) + 0.1 for sid, n in freq.items()}

        rows = conn.execute(
            f"""
            SELECT js.job_id AS job_id, js.skill_id AS skill_id, js.weight AS weight,
                   j.role_category AS role_category
            FROM job_skills js JOIN jobs j ON j.job_id = js.job_id
            WHERE js.skill_id IN ({id_ph}) AND j.is_software_engineering = 1
            """,
            skill_ids,
        ).fetchall()

        role_set = {r.lower() for r in (role_categories or [])}
        scores: dict[str, float] = {}
        role_of: dict[str, str] = {}
        for r in rows:
            jid = r["job_id"]
            role_of[jid] = r["role_category"] or ""
            scores[jid] = scores.get(jid, 0.0) + (r["weight"] or 1.0) * idf.get(r["skill_id"], 0.1)

        for jid in scores:
            if role_set and (role_of.get(jid, "").lower() in role_set):
                scores[jid] *= _ROLE_BOOST

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [(jid, round(score, 3)) for jid, score in ranked]
    finally:
        conn.close()


def load_jobs(job_ids: list[str]) -> list[JobPosting]:
    """Rebuild JobPostings (with skills as tags) for the given ids, in that order."""
    if not job_ids:
        return []
    conn = _connect()
    try:
        placeholders = ",".join("?" for _ in job_ids)
        rows = {
            r["job_id"]: r
            for r in conn.execute(
                f"SELECT * FROM jobs WHERE job_id IN ({placeholders})", job_ids
            )
        }
        skills_by_job: dict[str, list[str]] = {}
        for r in conn.execute(
            f"""
            SELECT js.job_id AS job_id, s.name AS name
            FROM job_skills js JOIN skills s ON s.id = js.skill_id
            WHERE js.job_id IN ({placeholders})
            """,
            job_ids,
        ):
            skills_by_job.setdefault(r["job_id"], []).append(r["name"])

        jobs: list[JobPosting] = []
        for jid in job_ids:
            r = rows.get(jid)
            if not r:
                continue
            jobs.append(
                JobPosting(
                    title=r["title"],
                    company=r["company"],
                    location=r["location"] or "Unknown",
                    country=r["country"] or "Unknown",
                    summary=r["summary"] or "",
                    salary=r["salary"] or "",
                    equity=r["equity"] or "",
                    url=r["url"] or "",
                    source=r["source"] or "yc",
                    raw_text=r["raw_text"] or "",
                    tags=sorted(skills_by_job.get(jid, [])),
                    min_experience=r["min_experience_label"] or "",
                    min_experience_years=r["min_experience_years"],
                    remote=r["remote"] or "",
                )
            )
        return jobs
    finally:
        conn.close()
