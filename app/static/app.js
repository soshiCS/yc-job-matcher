function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function listHtml(items) {
  if (!items || !items.length) return "<p>None</p>";
  return `<ul>${items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
}

function notesHtml(notes) {
  if (!notes?.length) return "";
  return notes.map((n) => `<div class="note-card">${escapeHtml(n)}</div>`).join("");
}

function costLine(c) {
  if (!c) return "";
  const usd =
    c.estimated_usd !== null && c.estimated_usd !== undefined
      ? ` ≈ $${c.estimated_usd.toFixed(4)}`
      : ` <span class="muted">(set OPENAI_*_COST_PER_1M for $)</span>`;
  return `<strong>LLM cost:</strong> ${c.llm_calls} calls, ${c.total_tokens.toLocaleString()} tokens${usd}`;
}

function jobLinks(jobs) {
  if (!jobs?.length) return "";
  return `<details class="fetched"><summary>Show ${jobs.length} jobs</summary><ul>${jobs
    .map(
      (j) =>
        `<li><a href="${escapeHtml(j.url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(j.title)}</a>${
          j.company ? ` — ${escapeHtml(j.company)}` : ""
        }</li>`
    )
    .join("")}</ul></details>`;
}

// ---------------- Index jobs ----------------
const indexForm = document.getElementById("index-form");
const indexStatus = document.getElementById("index-status");
const indexResults = document.getElementById("index-results");
const indexBtn = document.getElementById("index-btn");

indexForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  indexStatus.textContent = "Scraping and profiling… this can take a bit.";
  indexResults.innerHTML = "";
  indexBtn.disabled = true;
  try {
    const res = await fetch("/api/index", { method: "POST", body: new FormData(indexForm) });
    const p = await res.json();
    if (!res.ok) throw new Error(p.detail || "Indexing failed.");
    indexStatus.textContent = "Done.";
    indexResults.innerHTML =
      `<div class="note-card">
        <strong>Scraped:</strong> ${p.scraped} &nbsp;|&nbsp;
        <strong>Indexed (SWE):</strong> ${p.indexed} (${p.new} new) &nbsp;|&nbsp;
        <strong>Skipped non-SWE:</strong> ${p.skipped_non_swe}<br />
        <strong>Database now:</strong> ${p.db_total_jobs} jobs, ${p.db_total_skills} skills<br />
        ${costLine(p.cost)}
        ${jobLinks(p.jobs)}
      </div>` + notesHtml(p.notes);
  } catch (err) {
    indexStatus.innerHTML = `<span class="error">${escapeHtml(err.message || "Something went wrong.")}</span>`;
  } finally {
    indexBtn.disabled = false;
  }
});

// ---------------- Match résumé ----------------
const matchForm = document.getElementById("match-form");
const matchStatus = document.getElementById("match-status");
const matchNotes = document.getElementById("match-notes");
const matchResults = document.getElementById("match-results");
const matchBtn = document.getElementById("match-btn");

function renderMatch(p) {
  if (!p.ranked_jobs?.length) {
    matchResults.innerHTML = `<div class="result-card">No matching jobs. Try indexing more jobs first.</div>`;
    return;
  }
  const header = `
    <div class="note-card">
      <strong>Jobs in database:</strong> ${p.db_total_jobs}<br />
      <strong>Shortlisted &amp; evaluated:</strong> ${p.shortlist_size}<br />
      ${costLine(p.cost)}
      <div class="section-title">Your résumé skills</div>
      ${listHtml(p.resume_skills)}
      ${jobLinks(p.fetched_jobs)}
    </div>`;

  const cards = p.ranked_jobs
    .map((item, idx) => {
      const llm = item.llm_evaluation;
      return `
        <div class="result-card">
          <div class="result-header">
            <div>
              <h2 class="result-title">#${idx + 1} ${escapeHtml(item.job.title)}</h2>
              <div class="result-company">${escapeHtml(item.job.company)}</div>
              <div class="result-meta">
                <span class="badge">${escapeHtml(item.job.location || "Unknown")}</span>
                ${item.job.min_experience ? `<span class="badge">${escapeHtml(item.job.min_experience)}</span>` : ""}
                ${item.job.salary ? `<span class="badge">${escapeHtml(item.job.salary)}</span>` : ""}
              </div>
            </div>
            <div class="score-block">
              <div>Interview chance</div>
              <div class="big">${llm ? `${Math.round(Number(llm.interview_probability))}%` : "-"}</div>
              <div>${llm ? `${Math.round(Number(llm.confidence) * 100)}% confidence` : "No result"}</div>
            </div>
          </div>
          <div><a href="${escapeHtml(item.job.url || "#")}" target="_blank" rel="noreferrer">Open job</a></div>
          <div class="section-title">Matched skills</div>
          ${listHtml(item.matched_keywords)}
          <div class="section-title">Summary</div>
          <p>${escapeHtml(llm?.fit_summary || item.job.summary || "No summary")}</p>
          <div class="section-title">Strengths</div>
          ${listHtml(llm?.strengths || [])}
          <div class="section-title">Gaps</div>
          ${listHtml(llm?.gaps || [])}
          <div class="section-title">Reasoning</div>
          ${listHtml(llm?.reasoning || [])}
        </div>`;
    })
    .join("");

  matchResults.innerHTML = header + cards;
}

matchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  matchStatus.textContent = "Profiling résumé and matching…";
  matchNotes.innerHTML = "";
  matchResults.innerHTML = "";
  matchBtn.disabled = true;
  try {
    const res = await fetch("/api/match", { method: "POST", body: new FormData(matchForm) });
    const p = await res.json();
    if (!res.ok) throw new Error(p.detail || "Match failed.");
    matchStatus.textContent = "Done.";
    matchNotes.innerHTML = notesHtml(p.notes);
    renderMatch(p);
  } catch (err) {
    matchStatus.innerHTML = `<span class="error">${escapeHtml(err.message || "Something went wrong.")}</span>`;
  } finally {
    matchBtn.disabled = false;
  }
});
