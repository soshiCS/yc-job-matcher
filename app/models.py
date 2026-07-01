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


class SkillTag(BaseModel):
    name: str
    importance: str = "core"  # "core" (required/central) | "secondary" (nice-to-have)


class JobProfile(BaseModel):
    """Standardized, résumé-independent profile of a job (for the DB index)."""

    is_software_engineering: bool = True
    role_category: str = "other"
    seniority: str = ""
    skills: List[SkillTag] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    """Standardized profile of a candidate, in the same vocabulary as jobs."""

    role_categories: List[str] = Field(default_factory=list)
    seniority: str = ""
    skills: List[str] = Field(default_factory=list)


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


class IndexResponse(BaseModel):
    scraped: int
    indexed: int
    new: int
    skipped_non_swe: int
    total_matching_companies: Optional[int] = None
    db_total_jobs: int
    db_total_skills: int
    jobs: List[JobBrief] = Field(default_factory=list)
    cost: Optional[RunCost] = None
    notes: List[str] = Field(default_factory=list)


class MatchResponse(BaseModel):
    db_total_jobs: int
    shortlist_size: int
    resume_skills: List[str] = Field(default_factory=list)
    ranked_jobs: List[RankedJob] = Field(default_factory=list)
    fetched_jobs: List[JobBrief] = Field(default_factory=list)
    cost: Optional[RunCost] = None
    notes: List[str] = Field(default_factory=list)
