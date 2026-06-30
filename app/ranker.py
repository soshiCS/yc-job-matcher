from __future__ import annotations

import math
import re

from .models import JobPosting, PrefilteredJob
from .resume_parser import extract_skill_keywords


TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.#-]+")

# Generic words that carry no signal about role fit; excluded from token overlap so
# long, keyword-dense descriptions don't drown out meaningful matches.
STOPWORDS = {
    "the", "and", "for", "with", "you", "our", "are", "will", "your", "that", "this",
    "have", "from", "who", "what", "all", "can", "but", "not", "they", "their", "them",
    "work", "working", "team", "teams", "build", "building", "company", "role", "job",
    "looking", "join", "help", "experience", "years", "year", "engineer", "engineering",
    "engineers", "software", "developer", "development", "candidate", "candidates",
    "remote", "onsite", "hybrid", "full", "time", "fulltime", "across", "into", "out",
    "about", "more", "than", "while", "where", "when", "how", "why", "per", "via",
    "etc", "able", "want", "need", "like", "well", "also", "new", "get", "make", "use",
    "using", "world", "people", "problem", "problems", "product", "products", "platform",
}


def normalize_country(country: str) -> str:
    value = country.strip().lower()
    mapping = {
        "us": "us",
        "usa": "us",
        "united states": "us",
        "canada": "canada",
        "ca": "canada",
        "uk": "uk",
        "united kingdom": "uk",
        "england": "uk",
        "remote": "remote",
        "any": "any",
        "all": "any",
    }
    return mapping.get(value, value)


def location_matches(job: JobPosting, requested_country: str) -> bool:
    requested = normalize_country(requested_country)
    if requested in {"any", "all"}:
        return True

    haystack = f"{job.location} {job.country} {job.summary} {job.raw_text}".lower()

    if requested == "us":
        return any(term in haystack for term in [" us", "united states", "remote (us)", "usa", ", us"])
    if requested == "canada":
        return "canada" in haystack or "toronto" in haystack or "vancouver" in haystack
    if requested == "uk":
        return "united kingdom" in haystack or " uk" in haystack or "london" in haystack
    if requested == "remote":
        return "remote" in haystack

    return requested in haystack



def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]



def _meaningful_tokens(text: str) -> set[str]:
    return {t for t in tokenize(text) if len(t) > 2 and t not in STOPWORDS}


def cheap_prefilter(
    resume_text: str,
    jobs: list[JobPosting],
    requested_country: str,
    top_n: int,
) -> list[PrefilteredJob]:
    resume_token_set = _meaningful_tokens(resume_text)
    resume_skills = set(extract_skill_keywords(resume_text))

    scored: list[PrefilteredJob] = []

    for job in jobs:
        combined_job_text = " ".join(
            [job.title, job.company, job.location, job.summary, job.salary, job.equity, job.raw_text, " ".join(job.tags)]
        )
        job_token_set = _meaningful_tokens(combined_job_text)
        job_skills = set(extract_skill_keywords(combined_job_text)) | set(t.lower() for t in job.tags)
        title_lower = job.title.lower()

        # Length-normalized overlap of meaningful tokens, so a long, keyword-dense
        # description can't outscore a genuinely closer match just by being verbose.
        shared = resume_token_set & job_token_set
        token_overlap_score = len(shared) / (math.sqrt(len(job_token_set)) + 1.0)

        matched_keywords = sorted(skill for skill in resume_skills if skill in job_skills)
        skill_overlap_score = len(matched_keywords) * 2.0

        # A résumé skill appearing in the *title* is a strong role-fit signal — far
        # more telling than the same word buried in a long description.
        title_skill_hits = sum(1 for skill in resume_skills if skill in title_lower)
        title_bonus = title_skill_hits * 3.0

        ownership_bonus = 2.0 if "founding" in title_lower and any(
            term in resume_text.lower() for term in ["founding", "startup", "owned", "ownership"]
        ) else 0.0

        location_match = location_matches(job, requested_country)

        brevity_penalty = 0.0 if len(job.summary) > 20 else -0.5

        heuristic = (
            skill_overlap_score
            + title_bonus
            + token_overlap_score * 1.5
            + ownership_bonus
            + brevity_penalty
        )
        heuristic = round(max(0.0, heuristic), 3)

        scored.append(
            PrefilteredJob(
                job=job,
                heuristic_score=heuristic,
                matched_keywords=matched_keywords,
                location_match=location_match,
            )
        )

    scored.sort(key=lambda item: item.heuristic_score, reverse=True)
    return scored[:top_n]
