const form = document.getElementById("match-form");
const statusEl = document.getElementById("status");
const notesEl = document.getElementById("notes");
const resultsEl = document.getElementById("results");
const submitBtn = document.getElementById("submit-btn");

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
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderNotes(notes) {
  if (!notes?.length) {
    notesEl.innerHTML = "";
    return;
  }
  notesEl.innerHTML = notes
    .map((note) => `<div class="note-card">${escapeHtml(note)}</div>`)
    .join("");
}

function renderResults(payload) {
  if (!payload.ranked_jobs?.length) {
    resultsEl.innerHTML = `<div class="result-card">No jobs found.</div>`;
    return;
  }

  const fetched = payload.fetched_jobs || [];
  const fetchedList = fetched.length
    ? `<details class="fetched">
         <summary>Show all ${fetched.length} scraped jobs</summary>
         <ul>
           ${fetched
             .map(
               (j) =>
                 `<li><a href="${escapeHtml(j.url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(j.title)}</a>${
                   j.company ? ` — ${escapeHtml(j.company)}` : ""
                 }${j.location ? ` <span class="muted">(${escapeHtml(j.location)})</span>` : ""}</li>`
             )
             .join("")}
         </ul>
       </details>`
    : "";

  const c = payload.cost;
  const costLine = c
    ? `<br /><strong>LLM cost:</strong> ${c.llm_calls} calls, ${c.total_tokens.toLocaleString()} tokens (${c.input_tokens.toLocaleString()} in / ${c.output_tokens.toLocaleString()} out)${
        c.estimated_usd !== null && c.estimated_usd !== undefined
          ? ` ≈ $${c.estimated_usd.toFixed(4)}`
          : ` <span class="muted">(set OPENAI_*_COST_PER_1M for $)</span>`
      }`
    : "";

  const header = `
    <div class="note-card">
      <strong>Total jobs found:</strong> ${payload.total_jobs_found}<br />
      <strong>After country filter:</strong> ${payload.total_jobs_after_country_filter}<br />
      <strong>Sent to LLM:</strong> ${payload.top_n_sent_to_llm}${costLine}
      ${fetchedList}
    </div>
  `;

  const cards = payload.ranked_jobs
    .map((item, index) => {
      const llm = item.llm_evaluation;
      return `
        <div class="result-card">
          <div class="result-header">
            <div>
              <h2 class="result-title">#${index + 1} ${escapeHtml(item.job.title)}</h2>
              <div class="result-company">${escapeHtml(item.job.company)}</div>
              <div class="result-meta">
                <span class="badge">${escapeHtml(item.job.location || "Unknown")}</span>
                <span class="badge">Heuristic: ${Number(item.heuristic_score).toFixed(2)}</span>
                ${item.job.min_experience ? `<span class="badge">${escapeHtml(item.job.min_experience)}</span>` : ""}
                ${item.job.salary ? `<span class="badge">${escapeHtml(item.job.salary)}</span>` : ""}
                ${item.job.equity ? `<span class="badge">${escapeHtml(item.job.equity)}</span>` : ""}
              </div>
            </div>
            <div class="score-block">
              <div>Interview chance</div>
              <div class="big">${llm ? `${Math.round(Number(llm.interview_probability))}%` : "-"}</div>
              <div>${llm ? `${Math.round(Number(llm.confidence) * 100)}% confidence` : "No LLM result"}</div>
            </div>
          </div>

          <div>
            <a href="${escapeHtml(item.job.url || "#")}" target="_blank" rel="noreferrer">Open job</a>
          </div>

          <div class="section-title">Matched keywords</div>
          ${listHtml(item.matched_keywords)}

          <div class="section-title">Summary</div>
          <p>${escapeHtml(llm?.fit_summary || item.job.summary || "No summary")}</p>

          <div class="section-title">Strengths</div>
          ${listHtml(llm?.strengths || [])}

          <div class="section-title">Gaps</div>
          ${listHtml(llm?.gaps || [])}

          <div class="section-title">Reasoning</div>
          ${listHtml(llm?.reasoning || [])}
        </div>
      `;
    })
    .join("");

  resultsEl.innerHTML = header + cards;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusEl.textContent = "Working...";
  notesEl.innerHTML = "";
  resultsEl.innerHTML = "";
  submitBtn.disabled = true;

  try {
    const formData = new FormData(form);
    const response = await fetch("/api/match", {
      method: "POST",
      body: formData,
    });

    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || "Request failed.");
    }

    statusEl.textContent = "Done.";
    renderNotes(payload.notes || []);
    renderResults(payload);
  } catch (error) {
    statusEl.innerHTML = `<span class="error">${escapeHtml(error.message || "Something went wrong.")}</span>`;
  } finally {
    submitBtn.disabled = false;
  }
});
