from __future__ import annotations

import json

from openai import OpenAI

from .config import settings
from .models import JobPosting, JobProfile, LLMJobEvaluation, ResumeProfile
from .taxonomy import ROLE_CATEGORIES, SENIORITY_LEVELS


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


PROFILE_SYSTEM = """
You profile startup roles and résumés into a STANDARDIZED form for a job database, so
they can be matched by overlap later.

Classify:
- role_category: exactly one of {roles}.
- seniority: exactly one of {levels} (infer from title/wording; default "mid").
- skills: the concrete technologies, tools, and technical specialties this role
  involves — canonical names (e.g. "Kubernetes", "Distributed Systems", "JVM",
  "React"). Include real specialties (e.g. "Compilers", "Bytecode Engineering",
  "Consensus Algorithms"). Focus on the ~8-15 most DEFINING skills for the role — do
  not enumerate every minor tool. Exclude vague soft skills ("communication",
  "teamwork") and generic filler.

Mark each skill's importance:
- "core": central to the role — explicitly required, in the title, or the main work.
  Reserve this for the genuinely defining skills, not every listed tool.
- "secondary": helpful/nice-to-have, mentioned in passing, or one of many optional
  tools.

SKILL VOCABULARY RULE (critical): here is the existing canonical skill set already in
the database:
{vocabulary}
When a skill you identify means the same as one already in this set — even if worded
differently — output the EXACT existing name (e.g. if "Java Virtual Machine" exists,
return that for "JVM"). Only introduce a NEW name when none of the existing ones mean
the same thing. This keeps the vocabulary consistent across jobs and résumés.
""".strip()

JOB_PROFILE_SYSTEM = (
    PROFILE_SYSTEM
    + """

You are given a numbered list of roles. For EACH role return its `index`, plus
`is_software_engineering` (true only for hands-on coding roles — backend, frontend,
fullstack, systems, ML/AI, data, infra, mobile, security, embedded; false for dev
advocacy, DX/docs, design, PM, sales, marketing, recruiting), `role_category`,
`seniority`, and `skills`. Return valid JSON only."""
)

RESUME_PROFILE_SYSTEM = (
    PROFILE_SYSTEM
    + """

Profile the candidate from their résumé. Return `role_categories` (1-3 categories from
the list that genuinely fit the candidate), `seniority`, and `skills` (the candidate's
demonstrated technologies/specialties). Return valid JSON only."""
)

_ROLE_ENUM = {"type": "string", "enum": ROLE_CATEGORIES}
_LEVEL_ENUM = {"type": "string", "enum": SENIORITY_LEVELS}

JOB_PROFILE_SCHEMA = {
    "name": "job_profiles",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "profiles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "index": {"type": "integer"},
                        "is_software_engineering": {"type": "boolean"},
                        "role_category": _ROLE_ENUM,
                        "seniority": _LEVEL_ENUM,
                        "skills": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "name": {"type": "string"},
                                    "importance": {
                                        "type": "string",
                                        "enum": ["core", "secondary"],
                                    },
                                },
                                "required": ["name", "importance"],
                            },
                        },
                    },
                    "required": [
                        "index", "is_software_engineering", "role_category",
                        "seniority", "skills",
                    ],
                },
            }
        },
        "required": ["profiles"],
    },
    "strict": True,
}

RESUME_PROFILE_SCHEMA = {
    "name": "resume_profile",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "role_categories": {"type": "array", "items": _ROLE_ENUM},
            "seniority": _LEVEL_ENUM,
            "skills": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["role_categories", "seniority", "skills"],
    },
    "strict": True,
}


def _vocabulary_block(known_skills: list[str]) -> str:
    return ", ".join(known_skills) if known_skills else "(empty — none yet)"


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

    def _complete(self, system: str, user: str, schema_obj: dict) -> dict:
        response = self.client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_obj["name"],
                    "schema": schema_obj["schema"],
                    "strict": schema_obj["strict"],
                }
            },
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.calls += 1
        return json.loads(response.output_text)

    def extract_job_profiles(
        self, jobs: list[JobPosting], known_skills: list[str]
    ) -> list[JobProfile]:
        """Standardized, résumé-independent profile per job (chunked), aligned to input."""
        system = JOB_PROFILE_SYSTEM.format(
            roles=", ".join(ROLE_CATEGORIES),
            levels=", ".join(SENIORITY_LEVELS),
            vocabulary=_vocabulary_block(known_skills),
        )
        out: list[JobProfile] = []
        for start in range(0, len(jobs), CHUNK_SIZE):
            chunk = jobs[start : start + CHUNK_SIZE]
            blocks = "\n\n".join(_job_block(i, job) for i, job in enumerate(chunk))
            user = f"Roles to profile ({len(chunk)} total):\n\n{blocks}"
            parsed = self._complete(system, user, JOB_PROFILE_SCHEMA)
            by_index: dict[int, JobProfile] = {}
            for item in parsed.get("profiles", []):
                idx = item.get("index")
                if isinstance(idx, int) and 0 <= idx < len(chunk):
                    by_index[idx] = JobProfile(
                        **{k: v for k, v in item.items() if k != "index"}
                    )
            # A role the model omitted is treated as non-SWE (so it isn't indexed).
            out.extend(
                by_index.get(i, JobProfile(is_software_engineering=False))
                for i in range(len(chunk))
            )
        return out

    def extract_resume_profile(
        self, resume_text: str, known_skills: list[str]
    ) -> ResumeProfile:
        """Standardized candidate profile in the same vocabulary as jobs."""
        system = RESUME_PROFILE_SYSTEM.format(
            roles=", ".join(ROLE_CATEGORIES),
            levels=", ".join(SENIORITY_LEVELS),
            vocabulary=_vocabulary_block(known_skills),
        )
        user = f"Candidate résumé:\n{resume_text[:18000]}"
        parsed = self._complete(system, user, RESUME_PROFILE_SCHEMA)
        return ResumeProfile(**parsed)
