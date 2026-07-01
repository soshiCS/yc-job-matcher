"""Authenticated, browser-driven access to workatastartup.com.

The public company search (the only surface that filters by years of experience)
runs entirely client-side in the browser against a logged-in Algolia session that
cannot be replicated with plain HTTP. So for the "authenticated" source we drive a
real headless Chromium that reuses a saved login session.

Flow:
  1. `login()` opens a visible browser once; you log in by hand; the session is
     saved to a local `storage_state` JSON file.
  2. `search_jobs(...)` launches headless, reuses that session, runs the real
     filtered search, and returns the matching job ids (hydrated elsewhere).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .models import JobPosting

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_STATE_PATH = Path(
    os.getenv("WAAS_STORAGE_STATE", str(PROJECT_ROOT / ".waas_state.json"))
)

BASE_COMPANIES_URL = "https://www.workatastartup.com/companies"
HOME_URL = "https://www.workatastartup.com/"

# Maps our experience buckets to workatastartup's `minExperience` facet values.
EXPERIENCE_FACET = {"0-1": "0", "1-3": "1", "3-6": "3", "6+": "6"}

# Maps our remote choices to workatastartup's `remote` facet values (multi-select).
REMOTE_FACET = {"remote-ok": "yes", "remote-only": "only", "not-remote": "no"}

# Maps our role labels to workatastartup's `role` codes.
ROLE_FACET = {
    "engineering": "eng",
    "design": "design",
    "product": "product",
    "science": "science",
    "recruiting": "recruiting",
    "operations": "operations",
    "sales": "sales",
    "marketing": "marketing",
    "legal": "legal",
    "finance": "finance",
}

# Maps our country choices to workatastartup `locations` facet values.
LOCATION_FACET = {
    "us": "US",
    "usa": "US",
    "united states": "US",
    "canada": "Canada",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "france": "France",
    "germany": "Germany",
}


class WaaSAuthError(Exception):
    """Raised when there is no usable logged-in session."""


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise WaaSAuthError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright


def is_logged_in(context) -> bool:
    """Logged-in users can load /companies without being bounced to the homepage.

    Checks in a throwaway page so it never disturbs whatever page the user is on.
    """
    check = context.new_page()
    try:
        check.goto(
            BASE_COMPANIES_URL + "?layout=list-compact&sortBy=keyword",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        check.wait_for_timeout(1500)
        return "/companies" in check.url and check.get_by_text("Log In").count() == 0
    finally:
        check.close()


def login() -> Path:
    """Open a visible browser, let the user log in by hand, then save the session.

    We deliberately do NOT navigate the user's page while they log in — we just
    wait for them to press Enter in the terminal, then verify and save.
    """
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        print(
            "\n=== workatastartup.com login ===\n"
            "1. In the browser window that just opened, click 'Log In' and sign in.\n"
            "2. Once you're fully logged in (you can see the company directory),\n"
            "   come back here and press Enter.\n"
        )
        input("Press Enter after you have logged in... ")

        try:
            logged_in = is_logged_in(context)
        except Exception:
            logged_in = False

        if not logged_in:
            print(
                "\nWarning: could not confirm a logged-in session. Saving anyway —\n"
                "if searches come back empty, run `python login.py` again.\n"
            )

        STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(STORAGE_STATE_PATH))
        browser.close()
        status = "verified" if logged_in else "unverified"
        print(f"\nLogin saved ({status}) to {STORAGE_STATE_PATH}")
        return STORAGE_STATE_PATH


def build_search_url(
    *,
    role: str | None,
    country: str | None,
    experience_levels: list[str] | None,
    remote_levels: list[str] | None = None,
    sort_by: str = "created_desc",
) -> str:
    params: list[tuple[str, str]] = [
        ("demographic", "any"),
        ("hasEquity", "any"),
        ("hasSalary", "any"),
        ("industry", "any"),
        ("interviewProcess", "any"),
        ("jobType", "any"),
        ("layout", "list-compact"),
        ("sortBy", sort_by),
        ("tab", "any"),
        ("usVisaNotRequired", "any"),
    ]
    role_code = ROLE_FACET.get((role or "engineering").strip().lower())
    if role_code:
        params.append(("role", role_code))
    location = LOCATION_FACET.get((country or "").strip().lower())
    if location:
        params.append(("locations", location))
    for level in experience_levels or []:
        facet = EXPERIENCE_FACET.get(level)
        if facet is not None:
            params.append(("minExperience", facet))
    for level in remote_levels or []:
        facet = REMOTE_FACET.get(level)
        if facet is not None:
            params.append(("remote", facet))
    return f"{BASE_COMPANIES_URL}?{urlencode(params)}"


def _strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_min_experience_years(label: str | None) -> int | None:
    if not label:
        return None
    if "any" in label.lower() or "new grad" in label.lower():
        return 0
    match = re.search(r"\d+", label)
    return int(match.group()) if match else None


def _jobposting_from_fetch(job: dict, company: dict) -> JobPosting:
    skills = [s["name"] for s in job.get("skills", []) if isinstance(s, dict) and s.get("name")]
    batch = company.get("batch") or ""
    min_exp = job.get("pretty_min_experience") or ""
    location = (
        job.get("pretty_location_or_remote")
        or company.get("pretty_location")
        or "Unknown"
    )
    show_path = job.get("show_path") or f"/jobs/{job.get('id')}"
    url = show_path if show_path.startswith("http") else f"https://www.workatastartup.com{show_path}"
    description = _strip_html(job.get("description"))
    raw_text = " | ".join(
        part
        for part in [job.get("title"), company.get("name"), location, company.get("one_liner"), description]
        if part
    )
    tags = [t for t in ([*skills, batch]) if t]
    return JobPosting(
        title=job.get("title") or "Unknown title",
        company=company.get("name") or "Unknown Company",
        location=location,
        country=company.get("country") or "Unknown",
        summary=company.get("one_liner") or "",
        salary=job.get("pretty_salary_range") or "",
        equity=job.get("pretty_equity_range") or "",
        url=url,
        raw_text=raw_text[:12000],
        tags=tags,
        min_experience=min_exp,
        min_experience_years=_parse_min_experience_years(min_exp),
        remote=str(job.get("remote") or ""),
    )


def search_jobs(
    *,
    role: str | None,
    country: str | None,
    experience_levels: list[str] | None,
    remote_levels: list[str] | None = None,
    limit: int,
    exclude_ids: set[str] | None = None,
) -> list[JobPosting]:
    """Return up to `limit` jobs that are NOT already in `exclude_ids`.

    Sorted newest-first, it scrolls from the top and skips job ids we've already
    indexed, collecting until it has `limit` genuinely new jobs (or the list is
    exhausted). This makes indexing resume from where we left off — new postings at
    the top are grabbed first, then it goes deeper into older unseen jobs — using the
    caller's set of known ids as the only "cursor".
    """
    if not STORAGE_STATE_PATH.exists():
        raise WaaSAuthError(
            "No saved login session. Run `python login.py` first to authenticate."
        )

    exclude = exclude_ids or set()
    sync_playwright = _import_playwright()
    url = build_search_url(
        role=role,
        country=country,
        experience_levels=experience_levels,
        remote_levels=remote_levels,
    )

    # job_id -> JobPosting, built from every /companies/fetch payload we see.
    by_id: dict[str, JobPosting] = {}

    def grab(response):
        if "/companies/fetch" not in response.url:
            return
        try:
            data = response.json()
        except Exception:
            return
        for company in data.get("companies", []):
            for job in company.get("jobs", []):
                jid = str(job.get("id"))
                if jid and jid not in by_id:
                    by_id[jid] = _jobposting_from_fetch(job, company)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STORAGE_STATE_PATH))
        context.on("response", grab)
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2500)

        if page.get_by_text("Log In").count() > 0 or "/companies" not in page.url:
            browser.close()
            raise WaaSAuthError(
                "Saved session is no longer logged in. Run `python login.py` again."
            )

        def matched_ids() -> list[str]:
            hrefs = page.eval_on_selector_all(
                "a[href*='/jobs/']",
                "els => els.map(e => e.getAttribute('href'))",
            )
            ids: list[str] = []
            for href in hrefs:
                m = re.search(r"/jobs/(\d+)", href or "")
                if m and m.group(1) not in ids:
                    ids.append(m.group(1))
            return ids

        def fresh_ids() -> list[str]:
            return [jid for jid in matched_ids() if jid not in exclude]

        # Scroll until we have `limit` unseen jobs, or the list stops growing.
        # Stagnation is judged by total loaded (we may be skipping known jobs while
        # still making progress downward).
        prev_total = len(matched_ids())
        collected = fresh_ids()
        stagnant = 0
        while len(collected) < limit and stagnant < 4:
            page.mouse.wheel(0, 20_000)
            page.wait_for_timeout(2000)
            total = len(matched_ids())
            if total <= prev_total:
                stagnant += 1
            else:
                stagnant = 0
            prev_total = total
            collected = fresh_ids()

        browser.close()

    window = collected[:limit]
    return [by_id[jid] for jid in window if jid in by_id]
