from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class JobPosting(BaseModel):
    title: str
    company: str
    location: str = "Unknown"
    country: str = "Unknown"
    summary: str = ""
    salary: str = ""
    equity: str = ""
    url: str = ""
    source: str = "yc"
    raw_text: str = ""
    tags: List[str] = Field(default_factory=list)
    min_experience: str = ""
    min_experience_years: Optional[int] = None
    remote: str = ""


class PrefilteredJob(BaseModel):
    job: JobPosting
    heuristic_score: float
    matched_keywords: List[str] = Field(default_factory=list)
    location_match: bool = False


class LLMJobEvaluation(BaseModel):
    is_software_engineering: bool = True
    interview_probability: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    fit_summary: str
    strengths: List[str]
    gaps: List[str]
    reasoning: List[str]
    should_apply: bool


class RankedJob(BaseModel):
    job: JobPosting
    heuristic_score: float
    matched_keywords: List[str]
    llm_evaluation: Optional[LLMJobEvaluation] = None


class JobBrief(BaseModel):
    title: str
    company: str = ""
    location: str = "Unknown"
    url: str = ""


class RunCost(BaseModel):
    model: str
    llm_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    # None when per-token rates aren't configured (so we can't price it).
    estimated_usd: Optional[float] = None


class MatchResponse(BaseModel):
    requested_country: str
    max_jobs_to_fetch: int
    top_n_sent_to_llm: int
    total_jobs_found: int
    total_jobs_after_country_filter: int
    ranked_jobs: List[RankedJob]
    fetched_jobs: List[JobBrief] = Field(default_factory=list)
    cost: Optional[RunCost] = None
    notes: List[str] = Field(default_factory=list)
