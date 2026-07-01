"""Prune jobs that no longer exist on YC (removed / filled) from the index.

For each stored job it fetches the public detail page and classifies:
  - "alive"   : 200 with a job payload -> keep
  - "gone"    : 404/410, or redirected away, or 200 with no job payload -> remove
  - "unknown" : timeout / 5xx / network error -> KEEP (never delete on a blip)

Only "gone" jobs are removed. No LLM calls — just cheap HTTP checks.
"""

from __future__ import annotations

import html
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
from bs4 import BeautifulSoup

from . import db

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _job_status(client: httpx.Client, job_id: str) -> str:
    url = f"https://www.workatastartup.com/jobs/{job_id}"
    try:
        r = client.get(url)
    except Exception:
        return "unknown"  # transient — keep the job
    if r.status_code in (404, 410):
        return "gone"
    if r.status_code != 200:
        return "unknown"  # 5xx/403/etc — don't delete on a maybe-temporary error
    if f"/jobs/{job_id}" not in str(r.url):
        return "gone"  # redirected away from the job page
    root = BeautifulSoup(r.text, "html.parser").find(attrs={"data-page": True})
    if not root:
        return "gone"
    try:
        job = json.loads(html.unescape(root.get("data-page"))).get("props", {}).get("job")
    except Exception:
        return "unknown"
    return "alive" if job else "gone"


def prune_removed_jobs(dry_run: bool = False, max_workers: int = 8) -> dict:
    job_ids = sorted(db.all_job_ids())
    if not job_ids:
        return {"checked": 0, "alive": 0, "gone": [], "unknown": 0, "removed": 0}

    with httpx.Client(
        headers=HEADERS, follow_redirects=True, timeout=20
    ) as client:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            statuses = list(pool.map(lambda j: _job_status(client, j), job_ids))

    by_status = dict(zip(job_ids, statuses))
    gone = [j for j, s in by_status.items() if s == "gone"]
    alive = sum(1 for s in statuses if s == "alive")
    unknown = sum(1 for s in statuses if s == "unknown")

    removed = 0
    if gone and not dry_run:
        removed = db.delete_jobs(gone)

    return {
        "checked": len(job_ids),
        "alive": alive,
        "gone": gone,
        "unknown": unknown,
        "removed": removed,
    }
