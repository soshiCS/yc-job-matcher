from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .config import settings
from .llm import JobFitLLM, LLMUnavailableError
from .prune import prune_removed_jobs
from .models import IndexResponse, JobBrief, MatchResponse, RankedJob, RunCost
from .resume_parser import ResumeParsingError, extract_resume_text
from .yc_browser import (
    EXPERIENCE_FACET,
    REMOTE_FACET,
    ROLE_FACET,
    WaaSAuthError,
    search_jobs,
)


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="YC Job Matcher")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/prune")
async def prune():
    """Remove jobs no longer live on YC (filled/removed) from the database."""
    result = await run_in_threadpool(prune_removed_jobs, False)
    total_jobs, total_skills = db.counts()
    return {
        "checked": result["checked"],
        "alive": result["alive"],
        "removed": result["removed"],
        "unknown": result["unknown"],
        "db_total_jobs": total_jobs,
        "db_total_skills": total_skills,
    }


def _validate_filters(role: str, experience_levels: list[str], remote_levels: list[str]):
    if role.strip().lower() not in ROLE_FACET:
        raise HTTPException(
            status_code=400, detail=f"role must be one of: {', '.join(sorted(ROLE_FACET))}"
        )
    bad_exp = [lvl for lvl in experience_levels if lvl not in EXPERIENCE_FACET]
    if bad_exp:
        raise HTTPException(
            status_code=400,
            detail=f"experience_levels must be from: {', '.join(EXPERIENCE_FACET)}",
        )
    bad_remote = [lvl for lvl in remote_levels if lvl not in REMOTE_FACET]
    if bad_remote:
        raise HTTPException(
            status_code=400,
            detail=f"remote_levels must be from: {', '.join(REMOTE_FACET)}",
        )


@app.post("/api/index", response_model=IndexResponse)
async def index_jobs(
    country: str = Form("US"),
    role: str = Form("Engineering"),
    experience_levels: List[str] = Form(default=[]),
    remote_levels: List[str] = Form(default=[]),
    max_jobs_to_fetch: int = Form(40),
):
    """Scrape NEW jobs from YC, profile each into a standardized form, and store them.

    Résumé-independent. Resumes automatically: it skips any job already in the
    database (server-side memory) and collects up to `max_jobs_to_fetch` genuinely
    new jobs — grabbing fresh top postings first, then going deeper.
    """
    if max_jobs_to_fetch < 1 or max_jobs_to_fetch > settings.max_fetched_jobs:
        raise HTTPException(
            status_code=400,
            detail=f"max_jobs_to_fetch must be between 1 and {settings.max_fetched_jobs}",
        )
    _validate_filters(role, experience_levels, remote_levels)

    notes: list[str] = []
    known_ids = db.all_job_ids()
    try:
        jobs = await run_in_threadpool(
            search_jobs,
            role=role,
            country=country,
            experience_levels=experience_levels,
            remote_levels=remote_levels,
            limit=max_jobs_to_fetch,
            exclude_ids=known_ids,
        )
    except WaaSAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {exc}") from exc

    notes.append(
        f"Found {len(jobs)} new jobs (skipped {len(known_ids)} already indexed while scrolling)."
    )
    print(f"\n[index] {len(jobs)} new jobs; {len(known_ids)} already known")

    cost: RunCost | None = None
    indexed = new = 0
    profiles = []
    try:
        llm = JobFitLLM()
        known = db.get_all_skill_names()
        profiles = llm.extract_job_profiles(jobs, known)
        indexed, new = db.index_jobs(list(zip(jobs, profiles)))
        cost = _compute_cost(llm, "index")
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Indexing failed: {exc}") from exc

    skipped = sum(1 for p in profiles if not p.is_software_engineering)
    notes.append(f"Indexed {indexed} software-engineering jobs ({new} new); skipped {skipped} non-SWE.")
    total_jobs, total_skills = db.counts()
    notes.append(f"Database now holds {total_jobs} jobs and {total_skills} skills.")

    saved_briefs = [
        JobBrief(title=j.title, company=j.company, location=j.location, url=j.url)
        for j, p in zip(jobs, profiles)
        if p.is_software_engineering
    ]
    return IndexResponse(
        scraped=len(jobs),
        indexed=indexed,
        new=new,
        skipped_non_swe=skipped,
        db_total_jobs=total_jobs,
        db_total_skills=total_skills,
        jobs=saved_briefs,
        cost=cost,
        notes=notes,
    )


@app.post("/api/match", response_model=MatchResponse)
async def match_jobs(
    resume: UploadFile = File(...),
    top_n: int = Form(8),
    shortlist_size: int = Form(10),
):
    """Match a résumé against the saved jobs: profile the résumé, shortlist by skill
    overlap (SQL, no LLM), then LLM-evaluate only the shortlist."""
    if top_n < 1 or top_n > 25:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 25")
    if shortlist_size < 1 or shortlist_size > 50:
        raise HTTPException(status_code=400, detail="shortlist_size must be between 1 and 50")

    try:
        content = await resume.read()
        resume_text = extract_resume_text(resume.filename or "resume.pdf", content)
    except ResumeParsingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total_jobs, _ = db.counts()
    if total_jobs == 0:
        raise HTTPException(
            status_code=400,
            detail="No jobs in the database yet. Run 'Index jobs' first.",
        )

    notes: list[str] = [f"Parsed resume: {len(resume_text)} characters extracted."]
    print(f"\n[match] resume parsed: {len(resume_text)} chars")

    cost: RunCost | None = None
    llm: JobFitLLM | None = None
    try:
        llm = JobFitLLM()
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 1) Profile the résumé (1 LLM call) into the same skill vocabulary.
    known = db.get_all_skill_names()
    profile = llm.extract_resume_profile(resume_text, known)
    notes.append(
        f"Résumé profile: {profile.seniority or 'n/a'}, roles {profile.role_categories or '[]'}, "
        f"{len(profile.skills)} skills."
    )
    print(f"[match] resume skills: {profile.skills}")

    # 2) Shortlist from the DB by weighted skill overlap (pure SQL — no LLM per job).
    shortlisted = db.shortlist(
        profile.skills, limit=shortlist_size, role_categories=profile.role_categories
    )
    shortlist_ids = [jid for jid, _score in shortlisted]
    candidate_jobs = db.load_jobs(shortlist_ids)
    notes.append(
        f"Shortlisted {len(candidate_jobs)} of {total_jobs} saved jobs by skill overlap."
    )

    fetched_jobs = [
        JobBrief(title=j.title, company=j.company, location=j.location, url=j.url)
        for j in candidate_jobs
    ]

    # 3) LLM-evaluate ONLY the shortlist (1 call) for interview probability.
    ranked_jobs: list[RankedJob] = []
    if candidate_jobs:
        resume_skill_set = {s.lower() for s in profile.skills}
        evaluations = llm.evaluate_jobs(resume_text, candidate_jobs)
        for job, ev in zip(candidate_jobs, evaluations):
            matched = [s for s in job.tags if s.lower() in resume_skill_set]
            ranked_jobs.append(
                RankedJob(job=job, heuristic_score=0.0, matched_keywords=matched, llm_evaluation=ev)
            )
        ranked_jobs.sort(
            key=lambda r: r.llm_evaluation.interview_probability if r.llm_evaluation else -1,
            reverse=True,
        )
        ranked_jobs = ranked_jobs[:top_n]
        notes.append(f"LLM evaluated the {len(candidate_jobs)}-job shortlist.")

    cost = _compute_cost(llm, "match")

    return MatchResponse(
        db_total_jobs=total_jobs,
        shortlist_size=len(candidate_jobs),
        resume_skills=profile.skills,
        ranked_jobs=ranked_jobs,
        fetched_jobs=fetched_jobs,
        cost=cost,
        notes=notes,
    )


def _compute_cost(llm: JobFitLLM, label: str) -> RunCost:
    in_rate = settings.openai_input_cost_per_1m
    out_rate = settings.openai_output_cost_per_1m
    estimated_usd = None
    if in_rate or out_rate:
        estimated_usd = round(
            llm.input_tokens / 1_000_000 * in_rate
            + llm.output_tokens / 1_000_000 * out_rate,
            6,
        )
    cost = RunCost(
        model=settings.openai_model,
        llm_calls=llm.calls,
        input_tokens=llm.input_tokens,
        output_tokens=llm.output_tokens,
        total_tokens=llm.input_tokens + llm.output_tokens,
        estimated_usd=estimated_usd,
    )
    price = f"${estimated_usd}" if estimated_usd is not None else "n/a (set rates)"
    print(
        f"[{label}] cost: {cost.llm_calls} LLM calls, "
        f"{cost.input_tokens} in + {cost.output_tokens} out tokens, est {price}"
    )
    return cost
