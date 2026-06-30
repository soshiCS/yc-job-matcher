from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .db import save_evaluated_jobs
from .llm import JobFitLLM, LLMUnavailableError
from .models import JobBrief, MatchResponse, RankedJob, RunCost
from .ranker import cheap_prefilter
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


@app.post("/api/match", response_model=MatchResponse)
async def match_jobs(
    resume: UploadFile = File(...),
    country: str = Form(...),
    top_n: int = Form(...),
    max_jobs_to_fetch: int = Form(...),
    role: str = Form("Engineering"),
    experience_levels: List[str] = Form(default=[]),
    remote_levels: List[str] = Form(default=[]),
    start_index: int = Form(0),
):
    if top_n < 1 or top_n > 25:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 25")
    if max_jobs_to_fetch < 1 or max_jobs_to_fetch > settings.max_fetched_jobs:
        raise HTTPException(
            status_code=400,
            detail=f"max_jobs_to_fetch must be between 1 and {settings.max_fetched_jobs}",
        )
    if start_index < 0:
        raise HTTPException(status_code=400, detail="start_index must be 0 or greater")
    if role.strip().lower() not in ROLE_FACET:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of: {', '.join(sorted(ROLE_FACET))}",
        )
    invalid_levels = [lvl for lvl in experience_levels if lvl not in EXPERIENCE_FACET]
    if invalid_levels:
        raise HTTPException(
            status_code=400,
            detail=f"experience_levels must be from: {', '.join(EXPERIENCE_FACET)}",
        )
    invalid_remote = [lvl for lvl in remote_levels if lvl not in REMOTE_FACET]
    if invalid_remote:
        raise HTTPException(
            status_code=400,
            detail=f"remote_levels must be from: {', '.join(REMOTE_FACET)}",
        )

    try:
        content = await resume.read()
        resume_text = extract_resume_text(resume.filename or "resume.pdf", content)
    except ResumeParsingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    notes: list[str] = []
    notes.append(f"Parsed resume: {len(resume_text)} characters extracted.")
    print(f"\n[match] resume parsed: {len(resume_text)} chars; preview:\n{resume_text[:400]!r}")

    # Logged-in browser search: role, location, and experience are all applied
    # server-side by YC, so the returned jobs are already the filtered set.
    try:
        jobs = await run_in_threadpool(
            search_jobs,
            role=role,
            country=country,
            experience_levels=experience_levels,
            remote_levels=remote_levels,
            limit=max_jobs_to_fetch,
            offset=start_index,
        )
    except WaaSAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Authenticated search failed: {exc}"
        ) from exc

    filters = [f"role '{role}'", f"country '{country}'"]
    if experience_levels:
        filters.append(f"experience {experience_levels}")
    if remote_levels:
        filters.append(f"remote {remote_levels}")
    window = f"jobs {start_index}–{start_index + len(jobs)}"
    notes.append(
        f"Authenticated YC search applied {', '.join(filters)} server-side; "
        f"got {len(jobs)} matching jobs ({window})."
    )
    location_filtered_jobs = jobs

    # Surface the full scraped pool (titles + links) so the selection is auditable.
    fetched_jobs = [
        JobBrief(title=j.title, company=j.company, location=j.location, url=j.url)
        for j in location_filtered_jobs
    ]
    print(f"\n[match] scraped {len(fetched_jobs)} jobs:")
    for j in fetched_jobs:
        print(f"  - {j.title} @ {j.company} | {j.location} | {j.url}")

    # Evaluate the WHOLE scraped window (the LLM scores each job independently, in
    # chunks). top_n only slices how many results are displayed.
    prefiltered = cheap_prefilter(
        resume_text=resume_text,
        jobs=location_filtered_jobs,
        requested_country=country,
        top_n=len(location_filtered_jobs),
    )

    scored_jobs: list[RankedJob] = [
        RankedJob(
            job=item.job,
            heuristic_score=item.heuristic_score,
            matched_keywords=item.matched_keywords,
            llm_evaluation=None,
        )
        for item in prefiltered
    ]

    cost: RunCost | None = None
    try:
        llm = JobFitLLM()
        evaluations = llm.evaluate_jobs(resume_text, [item.job for item in scored_jobs])
        for ranked, evaluation in zip(scored_jobs, evaluations):
            ranked.llm_evaluation = evaluation
        cost = _compute_cost(llm)
        notes.append(f"LLM scored {len(scored_jobs)} jobs independently.")
    except LLMUnavailableError as exc:
        notes.append(f"LLM evaluation skipped: {exc}")
    except Exception as exc:
        notes.append(f"LLM evaluation failed and was skipped: {exc}")

    # Drop roles the LLM judged to be non-software-engineering (dev advocacy, DX,
    # design, PM, etc.) so they never compete for a rank.
    kept = [
        s for s in scored_jobs
        if s.llm_evaluation is None or s.llm_evaluation.is_software_engineering
    ]
    dropped = len(scored_jobs) - len(kept)
    if dropped:
        notes.append(f"Excluded {dropped} non-software-engineering role(s).")
    scored_jobs = kept

    # Persist the evaluated SWE jobs (dedup by job id; refreshes on re-evaluation).
    resume_hash = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()[:16]
    try:
        saved, new = save_evaluated_jobs(scored_jobs, resume_hash)
        if saved:
            notes.append(f"Saved {saved} SWE jobs to database ({new} new, {saved - new} updated).")
    except Exception as exc:
        notes.append(f"Database save skipped: {exc}")

    scored_jobs.sort(
        key=lambda item: (
            item.llm_evaluation.interview_probability if item.llm_evaluation else -1,
            item.heuristic_score,
        ),
        reverse=True,
    )
    ranked_jobs = scored_jobs[:top_n]

    return MatchResponse(
        requested_country=country,
        max_jobs_to_fetch=max_jobs_to_fetch,
        top_n_sent_to_llm=len(scored_jobs),
        total_jobs_found=len(jobs),
        total_jobs_after_country_filter=len(location_filtered_jobs),
        ranked_jobs=ranked_jobs,
        fetched_jobs=fetched_jobs,
        cost=cost,
        notes=notes,
    )


def _compute_cost(llm: JobFitLLM) -> RunCost:
    total_tokens = llm.input_tokens + llm.output_tokens
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
        total_tokens=total_tokens,
        estimated_usd=estimated_usd,
    )
    price_str = f"${estimated_usd}" if estimated_usd is not None else "n/a (set rates)"
    print(
        f"[match] cost: {cost.llm_calls} LLM calls, "
        f"{cost.input_tokens} in + {cost.output_tokens} out tokens, est {price_str}"
    )
    return cost
