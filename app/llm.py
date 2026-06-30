from __future__ import annotations

import json

from openai import OpenAI

from .config import settings
from .models import JobPosting, LLMJobEvaluation


SYSTEM_PROMPT = """
You are a seasoned, no-nonsense technical recruiter. For each role, give your honest,
candid judgment of the candidate's realistic chance of being invited to an interview,
based ONLY on the resume.

Screen each role the way a real recruiter would: how directly does the candidate's
actual, demonstrated experience — their domain, depth, stack, and seniority — match
what THIS role is really asking for? Judge each role on its own merits.

Be critical and discerning; do NOT inflate scores. A role outside the candidate's
demonstrated expertise should get a low chance even if a few skills loosely transfer
— "could probably learn it" or "adjacent background" is not a strong interview
signal. Reserve high scores for roles where the resume is a clear, direct match to
the core of the job. It is completely fine — and expected — for many roles to score
low; only the genuinely strong matches should rise to the top.

`interview_probability` is 0-100 (your honest estimate of that chance). As a rough
guide, not rigid buckets: 80+ = strong, direct match; ~50-70 = real but partial
match; below ~30 = weak or off-domain. Use your own judgment.

Also set `is_software_engineering`: true ONLY if the role is a hands-on software
engineering position whose primary day-to-day work is designing and writing
production software/code — e.g. backend, frontend, full-stack, mobile, systems,
platform, infrastructure, ML/AI, or security engineering. Set it false for roles
that are not primarily coding — e.g. developer advocacy / developer relations,
developer-experience (DX) or documentation-focused roles, design, product
management, sales, marketing, recruiting, or technical writing. Judge mainly from
the title, using the description to disambiguate.

Ground `strengths`, `gaps`, and `reasoning` in specifics from the resume and the
role. Set `should_apply` true only when you genuinely think it's worth the
candidate's time. Each evaluation must include the role's `index`. Return valid JSON
only.
""".strip()


EVAL_PROPERTIES = {
    "index": {"type": "integer"},
    "is_software_engineering": {"type": "boolean"},
    "interview_probability": {"type": "number", "minimum": 0, "maximum": 100},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "fit_summary": {"type": "string"},
    "strengths": {"type": "array", "items": {"type": "string"}},
    "gaps": {"type": "array", "items": {"type": "string"}},
    "reasoning": {"type": "array", "items": {"type": "string"}},
    "should_apply": {"type": "boolean"},
}

SCHEMA = {
    "name": "interview_chance_evaluations",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": EVAL_PROPERTIES,
                    "required": list(EVAL_PROPERTIES.keys()),
                },
            }
        },
        "required": ["evaluations"],
    },
    "strict": True,
}


# How many jobs to score per LLM call. Scoring is independent per job, so this only
# bounds prompt/output size — it does not change any individual score.
CHUNK_SIZE = 15


class LLMUnavailableError(Exception):
    pass


def _job_block(index: int, job: JobPosting) -> str:
    return f"""
[{index}] {job.title} @ {job.company}
Location: {job.location} ({job.country})
Salary: {job.salary} | Equity: {job.equity}
Tags: {', '.join(job.tags)}
Summary: {job.summary}
Details: {job.raw_text[:3000]}
""".strip()


class JobFitLLM:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise LLMUnavailableError("OPENAI_API_KEY is missing.")
        self.client = OpenAI(api_key=settings.openai_api_key)
        # Accumulated token usage across all calls on this instance.
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def evaluate_jobs(
        self, resume_text: str, jobs: list[JobPosting]
    ) -> list[LLMJobEvaluation]:
        """Score every job (each independently), returned aligned to `jobs` order.

        Jobs are scored in chunks of CHUNK_SIZE so a large window stays within safe
        prompt/output limits. Scoring is independent per job, so chunk boundaries do
        not affect any individual score.
        """
        results: list[LLMJobEvaluation | None] = []
        for start in range(0, len(jobs), CHUNK_SIZE):
            results.extend(self._score_chunk(resume_text, jobs[start : start + CHUNK_SIZE]))
        return results

    def _score_chunk(
        self, resume_text: str, jobs: list[JobPosting]
    ) -> list[LLMJobEvaluation]:
        if not jobs:
            return []

        blocks = "\n\n".join(_job_block(i, job) for i, job in enumerate(jobs))
        user_prompt = f"""
Candidate resume:
{resume_text[:18000]}

Roles to score ({len(jobs)} total) — score each one independently:

{blocks}
""".strip()

        response = self.client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA["name"],
                    "schema": SCHEMA["schema"],
                    "strict": SCHEMA["strict"],
                }
            },
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.calls += 1

        parsed = json.loads(response.output_text)
        by_index: dict[int, LLMJobEvaluation] = {}
        for item in parsed.get("evaluations", []):
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(jobs):
                by_index[idx] = LLMJobEvaluation(**item)

        # Align to input order; any role the model omitted simply has no evaluation.
        return [by_index.get(i) for i in range(len(jobs))]
