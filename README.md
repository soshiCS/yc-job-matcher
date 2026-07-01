# YC Job Matcher

A small end-to-end app with two flows:

- **Index jobs** — scrape YC jobs, have the LLM profile each into a standardized form
  (role category, seniority, canonical skills), and store software-engineering jobs in
  a local database. Résumé-independent; the only flow that scrapes.
- **Match my résumé** — profile a résumé into the same vocabulary, **shortlist** the
  most similar saved jobs by skill overlap with a plain SQL query (no LLM per job),
  then have the LLM score only that shortlist for interview probability.

## What this app does

It's designed to keep LLM cost down by separating the **expensive, one-time**
indexing from **cheap, repeatable** matching.

- Each job is profiled by the LLM **once** when indexed, then reused forever.
- A new résumé costs ~**2 LLM calls total** (profile the résumé + score the
  shortlist), no matter how many jobs are in the database — because the database does
  the candidate selection (skill-overlap shortlist) instead of the LLM.

### The canonical skill vocabulary

Matching by skill overlap only works if a job tagged `JVM` and a résumé tagged
`Java Virtual Machine` resolve to the same string. So skills live in a single,
growing canonical set: when profiling a job or résumé, the LLM is shown the existing
skill vocabulary and told to **reuse an existing name** when a skill means the same
thing, and only **add a new one** when it's genuinely novel. Role category and
seniority use small **fixed enums** (always aligned).

## How jobs are fetched

Jobs come from an **authenticated** search: the app drives your logged-in
workatastartup.com session in a headless browser, so role, location, **and years
of experience** are all filtered server-side by YC itself. It returns rich data
(real salary, equity, skills) straight from YC's search.

This requires a one-time `python login.py` (see Setup). Why a browser is needed:
YC's experience filter runs entirely client-side via a logged-in Algolia session
that can't be replicated with plain HTTP, so we let YC's own JavaScript run the
search and read the results.

## Database

A local SQLite database (`jobs.db` by default; set `JOBS_DB_PATH`). Only
software-engineering jobs are stored, keyed by YC job id (upsert → no duplicates).

Tables:
- `jobs` — one row per job with its standardized profile: title, company, location,
  country, remote, min experience (label + years), salary, equity, summary, full text,
  `role_category`, `seniority`, timestamps.
- `skills` — the growing canonical skill vocabulary (`name` unique, case-insensitive).
- `job_skills` — which skills each job has (drives the overlap shortlist).

Indexed on `role_category`, `min_experience_years`, `remote`, `country`, and
`job_skills(skill_id)`. Example queries:
```sql
-- the canonical skill vocabulary
SELECT name FROM skills ORDER BY name;

-- jobs sharing the most skills with a set you care about
SELECT j.title, j.company, COUNT(*) AS overlap
FROM job_skills js JOIN jobs j ON j.job_id = js.job_id
WHERE js.skill_id IN (SELECT id FROM skills WHERE name IN ('JVM','C++','Distributed Systems'))
GROUP BY js.job_id ORDER BY overlap DESC;
```

## Stack

- **Backend:** FastAPI
- **Frontend:** plain HTML/CSS/JS served by FastAPI
- **Authenticated search:** Playwright (headless Chromium) with a saved login
- **Resume parsing:** PDF / DOCX / TXT
- **LLM:** OpenAI Responses API with JSON schema output

## Folder structure

```text
login.py            # one-time login for the authenticated source
app/
  main.py             # /api/index and /api/match endpoints
  config.py
  models.py
  taxonomy.py         # fixed role_category / seniority enums
  llm.py              # job + résumé profiling, shortlist scoring
  resume_parser.py
  yc_browser.py       # authenticated headless-browser search
  db.py               # SQLite index: jobs, skills, job_skills
  static/
    styles.css
    app.js
  templates/
    index.html
```

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium   # for the authenticated source
```

### 3. Create `.env`

Copy `.env.example` to `.env` and fill in your key:

```bash
cp .env.example .env
```

Required:

```env
OPENAI_API_KEY=your_key_here
```

Optional:

```env
OPENAI_MODEL=gpt-5
MAX_FETCHED_JOBS=80
REQUEST_TIMEOUT_SECONDS=20
WAAS_STORAGE_STATE=.waas_state.json
```

### 4. Log in for authenticated search (one-time)

Only needed if you want the `authenticated` source.

```bash
python login.py
```

A browser window opens. Log in to workatastartup.com, then press Enter in the
terminal. Your session is saved to `.waas_state.json` (gitignored). Re-run this
whenever the session expires.

### 5. Run the app

```bash
uvicorn app.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Notes about the authenticated search

This rides YC's private/undocumented search, so a YC redesign could break the
extraction in `app/yc_browser.py`. If a search returns a `401`, your saved
session has expired — re-run `python login.py`.

## API

### `POST /api/index` — scrape + profile + store

Multipart form data (no résumé):

- `country`: string (location filter; `Any` disables it)
- `role`: one of Engineering, Design, Product, Science, Recruiting, Operations, Sales, Marketing, Legal, Finance (default Engineering)
- `experience_levels`: zero or more of `0-1`, `1-3`, `3-6`, `6+`
- `remote_levels`: zero or more of `remote-ok`, `remote-only`, `not-remote`
- `max_jobs_to_fetch`: int — how many to scrape
- `start_index`: int — skip this many first (paging across runs)

Returns counts (scraped / indexed / new / skipped-non-SWE), the current database
totals, and the list of indexed jobs.

### `POST /api/match` — match a résumé against saved jobs

Multipart form data:

- `resume`: file
- `shortlist_size`: int — how many DB candidates to LLM-evaluate (default 10)
- `top_n`: int — how many results to show (default 8)

Profiles the résumé, shortlists saved jobs by skill overlap (SQL), LLM-scores the
shortlist, and returns ranked jobs with interview probability, fit summary,
strengths, gaps, reasoning, and the résumé's extracted skills.

## Future improvements

- add embeddings for better prefiltering
- crawl external company career pages
- cache jobs and results in Postgres
- add auth and saved candidate profiles
- add a "top interview probability" mode vs "best absolute fit" mode
- add resume rewriting suggestions for each selected job
