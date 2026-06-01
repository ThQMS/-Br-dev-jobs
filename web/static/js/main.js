const API = "/api/v1";
let currentPage = 1;
let debounceTimer = null;

// ── Insights ─────────────────────────────────────────────────────────────────

async function loadInsights() {
  try {
    const res = await fetch(`${API}/insights`);
    const data = await res.json();

    document.querySelector("#stat-total .stat-value").textContent =
      data.total_jobs.toLocaleString("pt-BR");
    document.querySelector("#stat-remote .stat-value").textContent =
      `${data.remote_percentage}%`;
    document.querySelector("#stat-sources .stat-value").textContent =
      Object.keys(data.jobs_by_source).length;

    renderBarList("skill-list", data.top_skills.slice(0, 10), (s) => s.skill, (s) => s.percentage);

    const seniorities = Object.entries(data.jobs_by_seniority).map(([k, v]) => ({
      label: k,
      count: v,
      pct: data.total_jobs ? Math.round((v / data.total_jobs) * 100) : 0,
    }));
    renderBarList("seniority-list", seniorities, (s) => s.label, (s) => s.pct);
  } catch (e) {
    console.error("insights error", e);
  }
}

function renderBarList(containerId, items, labelFn, pctFn) {
  const ul = document.getElementById(containerId);
  ul.innerHTML = items
    .map(
      (item) => `
    <li>
      <span>${labelFn(item)}</span>
      <div style="display:flex;align-items:center;gap:0.5rem">
        <div class="skill-bar-wrap">
          <div class="skill-bar" style="width:${Math.min(pctFn(item), 100)}%"></div>
        </div>
        <span style="font-size:0.75rem;color:var(--text-muted);width:32px;text-align:right">${pctFn(item)}%</span>
      </div>
    </li>`
    )
    .join("");
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

async function loadJobs(page = 1) {
  currentPage = page;
  const search = document.getElementById("search").value;
  const source = document.getElementById("filter-source").value;
  const seniority = document.getElementById("filter-seniority").value;
  const remote = document.getElementById("filter-remote").checked;

  const params = new URLSearchParams({ page, page_size: 20 });
  if (search) params.set("search", search);
  if (source) params.set("source", source);
  if (seniority) params.set("seniority", seniority);
  if (remote) params.set("remote", "true");

  try {
    const res = await fetch(`${API}/jobs?${params}`);
    const data = await res.json();
    renderJobs(data);
  } catch (e) {
    console.error("jobs error", e);
  }
}

function renderJobs({ items, total, page, page_size }) {
  const container = document.getElementById("job-list");
  if (!items.length) {
    container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:2rem">Nenhuma vaga encontrada.</p>';
  } else {
    container.innerHTML = items.map(jobCard).join("");
  }
  renderPagination(total, page, page_size);
}

function jobCard(job) {
  const salary = job.salary_min
    ? `R$ ${job.salary_min.toLocaleString("pt-BR")}${job.salary_max ? ` – ${job.salary_max.toLocaleString("pt-BR")}` : "+"}`
    : null;
  const tags = (job.tags || []).slice(0, 6).map((t) => `<span class="tag">${t.name}</span>`).join("");
  return `
  <div class="card job-card">
    <a href="${job.url}" target="_blank" rel="noopener">
      <div class="job-title">${esc(job.title)}</div>
    </a>
    <div class="job-meta">
      <span>${esc(job.company)}</span>
      ${job.location ? `<span>${esc(job.location)}</span>` : ""}
      ${salary ? `<span>${salary}</span>` : ""}
    </div>
    <div class="job-meta" style="margin-top:0.25rem">
      <span class="badge badge-source">${job.source}</span>
      ${job.remote ? '<span class="badge badge-remote">Remoto</span>' : ""}
      ${job.seniority ? `<span class="badge badge-seniority">${job.seniority}</span>` : ""}
    </div>
    ${tags ? `<div class="tags">${tags}</div>` : ""}
  </div>`;
}

function renderPagination(total, page, pageSize) {
  const totalPages = Math.ceil(total / pageSize);
  const container = document.getElementById("pagination");
  if (totalPages <= 1) { container.innerHTML = ""; return; }

  const pages = [...new Set([1, page - 1, page, page + 1, totalPages])].filter(
    (p) => p >= 1 && p <= totalPages
  );

  let html = "";
  let prev = 0;
  for (const p of pages) {
    if (p - prev > 1) html += `<span style="color:var(--text-muted);padding:0 0.25rem">…</span>`;
    html += `<button class="${p === page ? "active" : ""}" onclick="loadJobs(${p})">${p}</button>`;
    prev = p;
  }
  container.innerHTML = html;
}

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Init ──────────────────────────────────────────────────────────────────────

function onFilterChange() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => loadJobs(1), 300);
}

document.getElementById("search").addEventListener("input", onFilterChange);
document.getElementById("filter-source").addEventListener("change", () => loadJobs(1));
document.getElementById("filter-seniority").addEventListener("change", () => loadJobs(1));
document.getElementById("filter-remote").addEventListener("change", () => loadJobs(1));

loadInsights();
loadJobs(1);
