"""Fixed, controlled vocabularies for standardized job/résumé profiles.

These are intentionally small and stable so a job's category and a résumé's
category always line up (unlike free-form skills, which grow dynamically).
"""

from __future__ import annotations

ROLE_CATEGORIES = [
    "backend",
    "frontend",
    "fullstack",
    "systems",          # compilers, runtimes, OS, low-level, performance
    "ml_ai",
    "data",
    "infra_devops",     # SRE, platform, cloud infra
    "mobile",
    "security",
    "embedded_robotics",
    "other",
]

SENIORITY_LEVELS = [
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "lead",
]
