# YC Job Matcher

A small end-to-end app that lets a user:

- upload a resume
- choose a target country (hard location filter)
- choose a role (engineering, design, product, …)
- optionally filter by required years of experience
- choose how many jobs to send to the LLM
- fetch a limited set of YC jobs
- pre-rank jobs cheaply
- send only the top N candidates to the LLM
- return the best-fit jobs with explanations

## What this app does

This app is designed to keep LLM cost down.

Instead of sending every job to the LLM, it uses a two-stage ranking pipeline:

1. **Cheap prefilter**
   - extract resume text
   - scrape a limited pool of YC jobs
   - filter by country/location
   - score jobs with lightweight heuristics
2. **LLM evaluation**
   - only the top `N` pre-ranked jobs are sent to the LLM
   - the LLM returns a structured score, reasoning, gaps, and a short summary

## How jobs are fetched

Jobs come from an **authenticated** search: the app drives your logged-in
workatastartup.com session in a headless browser, so role, location, **and years
of experience** are all filtered server-side by YC itself. It returns rich data
(real salary, equity, skills) straight from YC's search.

This requires a one-time `python login.py` (see Setup). Why a browser is needed:
YC's experience filter runs entirely client-side via a logged-in Algolia session
that can't be replicated with plain HTTP, so we let YC's own JavaScript run the
search and read the results.

## Saved jobs (database)

Every software-engineering job the LLM evaluates is saved to a local SQLite database
(`jobs.db` by default; set `JOBS_DB_PATH` to change). Non-software-engineering roles
are not stored.

- **No duplicates:** rows are keyed by the YC job id and upserted — re-running just
  refreshes a job's evaluation (its `first_seen_at` is preserved).
- **Stored per job:** everything handed to the LLM (title, company, location,
  country, remote, min experience, salary, equity, skills, full description) plus the
  LLM result (interview probability, confidence, fit summary, strengths, gaps,
  reasoning, should-apply) and timestamps.
- **Indexed for queries:** `min_experience_years`, `remote`, `country`, `company`,
  and `interview_probability`. Example:
  ```sql
  SELECT title, company, interview_probability
  FROM jobs
  WHERE min_experience_years <= 3 AND remote = 'yes'
  ORDER BY interview_probability DESC;
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
  main.py
  config.py
  models.py
  llm.py
  ranker.py
  resume_parser.py
  yc_browser.py       # authenticated headless-browser search
  db.py               # SQLite persistence for evaluated SWE jobs
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

### `POST /api/match`

Multipart form data:

- `resume`: file
- `country`: string (location filter applied server-side by YC; `Any` disables it)
- `role`: string — one of Engineering, Design, Product, Science, Recruiting, Operations, Sales, Marketing, Legal, Finance (defaults to Engineering)
- `experience_levels`: zero or more of `0-1`, `1-3`, `3-6`, `6+` (repeated field), filtered server-side by YC. Leave empty for any level.
- `remote_levels`: zero or more of `remote-ok`, `remote-only`, `not-remote` (repeated field), filtered server-side by YC. Leave empty to include all.
- `top_n`: int
- `max_jobs_to_fetch`: int

Returns ranked jobs with:

- heuristic pre-score
- LLM score
- fit summary
- strengths
- gaps
- reasons

## Future improvements

- add embeddings for better prefiltering
- crawl external company career pages
- cache jobs and results in Postgres
- add auth and saved candidate profiles
- add a "top interview probability" mode vs "best absolute fit" mode
- add resume rewriting suggestions for each selected job
